"""A Zammad HTTP fixture through signed ingestion, a real database and the review API."""

import argparse
import json
from datetime import UTC, timedelta
from functools import partial

import httpx
import pytest
from helpers import START
from test_extract import chat_response, due, output
from test_service_e2e import Clock

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from duebook import cli  # noqa: E402
from duebook.api import create_app, zammad, zammad_sync  # noqa: E402
from duebook.extract import ChatClient  # noqa: E402
from duebook.settings import Settings  # noqa: E402

BASE = "https://support.example.test/"
TARGET = "http://127.0.0.1:8000"
REVIEW = "/zammad/tickets/17/review"
PROMISE = "I'll send the delivery receipt in 2 hours."
CLAIM = "I sent the delivery receipt."
CONFIRMATION = "I received the delivery receipt."
PRIVATE_NOTE = "The internal bank account is 126780."


def _at(minutes):
    return (START + timedelta(minutes=minutes)).astimezone(UTC).isoformat()


class ZammadTicket:
    """The three Zammad endpoints, using native IDs and independently shuffled responses."""

    def __init__(self):
        self.ticket = {
            "id": 17, "number": "202600017", "title": "Delivery receipt", "created_at": _at(0),
            "owner_id": 24, "owner": "Ivan", "state_id": 2, "state": "open",
        }
        self.history = [
            {"id": 100, "o_id": 17, "object": "Ticket", "type": "created", "created_at": _at(0)},
        ]
        self.articles = []
        self.unavailable = False
        self.reorder = False
        self.revoked = set()

    def say(self, aid, hid, minutes, body, sender, author_id, name, *, internal=False):
        self.articles.append({
            "id": aid, "ticket_id": 17, "from": name, "body": body, "sender": sender,
            "created_by_id": author_id, "internal": internal, "created_at": _at(minutes),
        })
        self.history.append({
            "id": hid, "o_id": aid, "object": "Ticket::Article", "type": "created",
            "created_at": _at(minutes), "related_o_id": 17, "related_object": "Ticket",
        })

    def change(self, hid, minutes, attr, new, label):
        key = attr + "_id"
        self.history.append({
            "id": hid, "o_id": 17, "object": "Ticket", "type": "updated", "attribute": attr,
            "id_from": self.ticket[key], "id_to": new, "created_at": _at(minutes),
        })
        self.ticket.update({attr: label, key: new})

    def respond(self, request):
        assert request.method == "GET"
        _, token = request.headers["authorization"].split("=")
        path = request.url.path
        if token in self.revoked or token == "denied":
            return httpx.Response(403)
        if token == "customer" and "ticket_history" in path:
            return httpx.Response(403)
        assert token in {"sync-only", "viewer", "limited", "customer"}
        if path == "/api/v1/ticket_history/17":
            if self.unavailable:
                return httpx.Response(503)
            history = self.history[::-1] + self.history[:1] if self.reorder else self.history
            return httpx.Response(200, json={"history": history, "assets": {}})
        if path == "/api/v1/tickets/17":
            return httpx.Response(200, json=self.ticket)
        assert path == "/api/v1/ticket_articles/by_ticket/17"
        articles = self.articles[::-1]
        if token == "limited":
            articles = [item for item in articles if item["id"] != 42]
        return httpx.Response(200, json=articles)


def test_external_commitment_lifecycle_is_replayable_and_source_authorized(
    conn, dsn, store, monkeypatch,
):
    clock = Clock(START + timedelta(hours=3))
    settings = Settings(database_url=dsn, zammad_url=BASE, api_key="internal-key",
                        webhook_secret="signed", openrouter_api_key="stub",
                        verifier_enabled=False)  # fmt: skip
    provider = ZammadTicket()
    report = "The package never arrived."
    agent_report = "The package arrived."
    provider.say(40, 101, 1, report, "Customer", 30, "Customer")
    provider.say(42, 103, 4, f"{agent_report} {PROMISE}", "Agent", 24, "Ivan")
    provider.say(41, 102, 3, PRIVATE_NOTE, "Agent", 24, "Ivan", internal=True)
    original = httpx.Client
    monkeypatch.setattr(
        zammad, "Client", partial(original, transport=httpx.MockTransport(provider.respond))
    )
    for key, value in {
        "DUEBOOK_ZAMMAD_SYNC_TOKEN": "sync-only", "DUEBOOK_ZAMMAD_URL": BASE,
        "DUEBOOK_DATABASE_URL": dsn, "DUEBOOK_WEBHOOK_SECRET": "signed",
    }.items():
        monkeypatch.setenv(key, value)
    extracted = iter([
        output(facts=[{"slot": "delivery.status", "value": "missing", "quote": report}]),
        output(
            commitments=[{
                "owner": "agent", "action": "send the delivery receipt", "quote": PROMISE,
                "due_quote": "in 2 hours", "due": due(kind="offset", amount=2, unit="hour"),
            }],
            facts=[{"slot": "delivery.status", "value": "received", "quote": agent_report}],
        ),
        output(resolved=[{"id": "C1", "quote": "Done.", "target_quote": PROMISE}]),
        output(resolved=[{"id": "C1", "quote": CLAIM, "target_quote": PROMISE}]),
        output(resolved=[{"id": "C1", "quote": CONFIRMATION, "target_quote": PROMISE}]),
    ])
    asks = []
    model_timeouts = set()

    def model(_, request):
        prompt = request["messages"][-1]["content"]
        assert PRIVATE_NOTE not in prompt
        if model_timeouts:
            model_timeouts.pop()
            raise httpx.ReadTimeout("fixture model timeout")
        asks.append(prompt)
        return chat_response(json.dumps(next(extracted)))

    monkeypatch.setattr(ChatClient, "_post", model)
    monkeypatch.setattr(cli, "_now", clock)

    def process():
        assert cli.process(settings, argparse.Namespace(once=True), cli.Stopper()) == 0

    stream = zammad.stream_id(BASE, 17)
    delivered = []
    lost_ack = set()
    with TestClient(create_app(settings, clock=clock)) as api:
        def to_webhook(request):
            assert request.url.path == "/events"
            seq = json.loads(request.content)["seq"]
            delivered.append(seq)
            result = api.post("/events", content=request.content, headers=request.headers)
            if seq in lost_ack:
                lost_ack.remove(seq)
                raise httpx.ReadTimeout("response lost")
            return httpx.Response(result.status_code, json=result.json())

        monkeypatch.setattr(
            zammad_sync.httpx, "Client",
            partial(original, transport=httpx.MockTransport(to_webhook)),
        )

        def review(token="viewer", code=200, *, conversation=False):
            response = api.get(
                REVIEW, headers={"Authorization": f"Bearer {token}"},
                params={"include_conversation": conversation},
            )
            assert response.status_code == code, response.text
            assert response.headers.get("cache-control") == "no-store"
            return response.json()

        provider.unavailable = True
        with pytest.raises(zammad_sync.SyncError, match="Zammad returned HTTP 503"):
            zammad_sync.sync(settings, 17, TARGET)
        assert delivered == []
        provider.unavailable = False
        assert review()["processing"] == {"awaiting_sync": 4, "backlog": 0, "held": None}
        assert cli.main(["sync-zammad", "17", "--duebook-url", TARGET]) == 0
        assert review()["processing"] == {"awaiting_sync": 0, "backlog": 4, "held": None}
        assert asks == []

        process()
        initial = review()
        (commitment,) = initial["commitments"]
        assert commitment["id"] == "article:42#1" and commitment["responsible_id"] == "24"
        assert commitment["deadline"] == {
            "at": _at(124), "confidence": "high", "quote": "in 2 hours",
        }
        assert commitment["source"] == {
            "article_id": "42", "url": f"{BASE}#ticket/zoom/17/42", "quote": PROMISE,
        }
        (dispute,) = initial["contradictions"]
        assert {f["value"] for f in dispute["facts"]} == {"received", "missing"}
        assert "messages" not in initial and PRIVATE_NOTE not in str(initial)
        viewed = review(conversation=True)["messages"]
        assert [message["id"] for message in viewed] == [40, 41, 42]
        assert viewed[1]["internal"] and viewed[1]["body"] == PRIVATE_NOTE
        assert viewed[-1]["url"] == commitment["source"]["url"]
        assert PROMISE in viewed[-1]["body"]

        provider.change(104, 25, "owner", 27, "Olga")
        provider.change(105, 25, "state", 4, "closed")
        provider.say(44, 106, 30, "Done.", "Agent", 27, "Olga")
        provider.reorder = True
        lost_ack.add(5)
        with pytest.raises(zammad_sync.SyncError, match="could not acknowledge"):
            zammad_sync.sync(settings, 17, TARGET)
        assert zammad_sync.sync(settings, 17, TARGET) == 2
        process()
        closed = review()
        (still_owed,) = closed["commitments"]
        assert closed["ticket_state"] == "closed"
        assert still_owed["id"] == commitment["id"]
        assert (still_owed["responsible_id"], still_owed["responsible_name"]) == ("27", "Olga")
        assert still_owed["inherited_from"] == "24"
        assert still_owed["inherited_from_name"] == "Ivan"
        assert (still_owed["state"], still_owed["status"]) == ("open", "breached")
        assert still_owed["suggestions"][0]["source"]["article_id"] == "44"

        provider.say(45, 107, 45, CLAIM, "Agent", 27, "Olga")
        provider.say(46, 108, 46, CONFIRMATION, "System", 1, "Zammad")
        assert zammad_sync.sync(settings, 17, TARGET) == 1
        assert zammad_sync.sync(settings, 17, TARGET) == 0
        model_timeouts.add(8)
        process()
        assert review()["processing"] == {"awaiting_sync": 0, "backlog": 1, "held": 8}
        clock.now += timedelta(minutes=2)
        process()
        (claim,) = review()["commitments"]
        assert claim["state"] == claim["resolution"]["kind"] == "claimed"
        assert claim["resolution"]["source"]["article_id"] == "45"
        assert len(asks) == 4

        # The reviewer can ask for clarification in Zammad. A later customer message is
        # recorded as new evidence and still goes through the same closing rules.
        provider.say(47, 109, 60, CONFIRMATION, "Customer", 30, "Customer")
        assert zammad_sync.sync(settings, 17, TARGET) == 1
        process()
        (confirmed,) = review()["commitments"]
        assert confirmed["state"] == "fulfilled"
        assert confirmed["resolution"]["kind"] == "counterparty_confirmation"
        assert confirmed["resolution"]["source"]["article_id"] == "47"
        assert confirmed["suggestions"] == []
        assert len(asks) == 5
        assert delivered == list(range(1, 10))
        assert store.replay(stream) == store.card(stream)
        assert [event.id for event in store.events(stream)] == [
            "initial:owner", "initial:state", "article:40", "article:42", "history:104",
            "history:105", "article:44", "article:45", "article:47",
        ]

        for token, code in [("denied", 403), ("customer", 403), ("limited", 409)]:
            refused = review(token, code, conversation=True)
            assert "commitments" not in refused and "messages" not in refused
        for path in ("card", "card?as_of=4", "brief?format=json", "status"):
            assert api.get(f"/tickets/{stream}/{path}", headers={
                "Authorization": "Bearer internal-key",
            }).status_code == 404

        # A viewer who opened the card earlier must be reauthorized for the evidence panel.
        provider.revoked.add("viewer")
        for conversation in (False, True):
            denied = review(code=403, conversation=conversation)
            assert "commitments" not in denied and "messages" not in denied
        provider.revoked.clear()

        # Deletion (or a visibility change) stops both new extraction and historical disclosure.
        provider.articles = [article for article in provider.articles if article["id"] != 42]
        provider.history = [change for change in provider.history if change["id"] != 103]
        unavailable = review(code=409, conversation=True)
        assert "commitments" not in unavailable and "messages" not in unavailable
        with pytest.raises(zammad_sync.SyncError, match="differs from the acknowledged prefix"):
            zammad_sync.sync(settings, 17, TARGET)
        assert len(list(store.events(stream))) == 9
