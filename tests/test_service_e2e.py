"""The whole service, end to end: webhook, processor, scheduler, delivery, brief.

Events arrive through the HTTP API out of order and one of them twice; a
processor applies them with stubbed model responses; the scheduler and a delivery
worker run at a moment past the promise's deadline. The alert must arrive
once, to the operator who holds the ticket by then, and the brief must show
the broken promise with the message it rests on.
"""

import argparse
import json
from datetime import timedelta

import httpx
import pytest
from helpers import ANNA, CAL, IVAN, OLGA, START, Ticket
from test_extract import chat_response, due, output

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from duebook import Party, cli, signing  # noqa: E402
from duebook.api import create_app  # noqa: E402
from duebook.extract import DEFAULT_MODEL, ChatClient  # noqa: E402
from duebook.proposals import MakeCommitment, ResolveCommitment, Verdict, Verification  # noqa: E402
from duebook.settings import Settings  # noqa: E402
from duebook.store import Store, Worker  # noqa: E402
from duebook.store.codec import encode  # noqa: E402
from duebook.store.scheduler import run_due  # noqa: E402
from duebook.temporal import Offset, Unit  # noqa: E402

pytestmark = pytest.mark.usefixtures("conn")

SECRET = "hook-secret"
KEY = "read-key"
PROMISE = "Перезвоню вам через 2 часа"
REFUND = "Вернём деньги за доставку"
REFUND_CONTEXT = "Проверьте, пожалуйста, поступили ли деньги за доставку"
RECEIVED = "всё получила"
#: The promise is made 15 minutes in and is due two hours later.
DEADLINE = START + timedelta(minutes=15, hours=2)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Inbox:
    """A channel that keeps every alert it is given, as given."""

    def __init__(self):
        self.received = []

    def send(self, notice):
        self.received.append(notice)


def conversation(verification: Verification | None) -> Ticket:
    t = Ticket("T-77")
    t.assign(None, IVAN.id)
    t.say(ANNA, "Мне так и не перезвонили по заказу 4815.")
    t.wait(minutes=15)
    t.say(
        IVAN,
        PROMISE + ".",
        MakeCommitment(Party.AGENT, "перезвонить", PROMISE, Offset(2, Unit.HOUR), "через 2 часа"),
    )
    t.wait(minutes=30)
    t.assign(IVAN.id, OLGA.id)  # Ivan's shift ends; the promise passes to Olga
    t.say(OLGA, f"{REFUND}. {REFUND_CONTEXT}?",
          MakeCommitment(Party.COMPANY, "вернуть деньги", REFUND))  # fmt: skip
    t.say(ANNA, f"Спасибо, {RECEIVED}!",
          ResolveCommitment("e5#0", RECEIVED, REFUND, verification, REFUND_CONTEXT))  # fmt: skip
    return t


def deliver(client: TestClient, event) -> dict:
    body = json.dumps(encode(event), ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", signing.HEADER: signing.sign(SECRET, body)}
    response = client.post("/events", content=body, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize("outcome", ["yes", "disabled", "timeout"])
def test_the_service_verifies_closes_and_alerts_once_on_broken_promises(
    conn, dsn, monkeypatch, caplog, outcome,
):
    verdict = Verification(Verdict.YES, RECEIVED, DEFAULT_MODEL) if outcome == "yes" else None
    ticket = conversation(verdict)
    events = [event for event, _ in ticket.log]
    clock = Clock(START + timedelta(hours=1))
    env = {
        "DUEBOOK_DATABASE_URL": dsn,
        "DUEBOOK_WEBHOOK_SECRET": SECRET,
        "DUEBOOK_API_KEY": KEY,
        "DUEBOOK_OPENROUTER_API_KEY": "stub",
    }
    if outcome == "disabled":
        env["DUEBOOK_VERIFIER_ENABLED"] = "false"
    settings = Settings.from_env(env)
    store = Store(conn, CAL, settings.policy())
    extracted = iter([
        output(),
        output(commitments=[{
            "owner": "agent", "action": "перезвонить", "quote": PROMISE,
            "due_quote": "через 2 часа", "due": due(kind="offset", amount=2, unit="hour"),
        }]),
        output(commitments=[{"owner": "company", "action": "вернуть деньги", "quote": REFUND}]),
        output(resolved=[{"id": "C2", "quote": RECEIVED, "target_quote": REFUND,
                          "context_quote": REFUND_CONTEXT}]),
    ])  # fmt: skip
    asked = []

    def stub_post(_, body):
        name = body["response_format"]["json_schema"]["name"]
        asked.append(name)
        if name == "ticket_batch":
            start = 2 if len(asked) == 1 else 5
            rows = [{"seq": start, "reread": False, **next(extracted)}]
            if start == 2:
                rows.append({"seq": 3, "reread": False, **next(extracted)})
            else:
                rows.append({"seq": 6, "reread": True, **output()})
            return chat_response(json.dumps({"messages": rows}))
        if name == "ticket_update":
            return chat_response(json.dumps(next(extracted)))
        assert name == "context_verdict" and outcome != "disabled"
        prompt = body["messages"][1]["content"]
        assert PROMISE in prompt and REFUND_CONTEXT in prompt
        assert body["model"] == DEFAULT_MODEL and body["provider"]["order"] == ["deepinfra/fp8"]
        assert body["reasoning"] == {"effort": "low", "exclude": True}
        if outcome == "timeout":
            raise httpx.ReadTimeout("stub timeout")
        return chat_response(
            json.dumps({"verdict": "yes", "evidence_quote": RECEIVED, "id": "C2",
                        "target_quote": REFUND, "context_quote": REFUND_CONTEXT}),
            cost=0.00002,
            reasoning=25,
        )

    monkeypatch.setattr(ChatClient, "_post", stub_post)
    monkeypatch.setattr(cli, "_now", clock)

    with TestClient(create_app(settings, clock=clock)) as client:
        # The helpdesk delivers out of order and repeats itself.
        acks = [deliver(client, events[n]) for n in (2, 0, 3, 2, 1, 5, 4)]
        assert [(a["seq"], a["new"]) for a in acks] == [
            (3, True), (1, True), (4, True), (3, False), (2, True), (6, True), (5, True),
        ]  # fmt: skip
        # Acknowledged, not yet read: the webhook never calls the model.
        auth = {"Authorization": f"Bearer {KEY}"}
        status = client.get("/tickets/T-77/status", headers=auth).json()
        assert status == {
            "ticket_id": "T-77", "last_seq": 0, "backlog": 6, "hold": None,
            "verifier_calls": 0, "verifier_failures": 0,
        }  # fmt: skip
        assert asked == []

        args = argparse.Namespace(once=True)
        for _ in range(2):
            assert cli.process(settings, args, cli.Stopper()) == 0
        enabled = outcome != "disabled"
        counts = {"verifier_calls": int(enabled), "verifier_failures": int(outcome == "timeout")}
        expected_calls = ["ticket_batch", "ticket_batch", "ticket_update"]
        if enabled:
            expected_calls.append("context_verdict")
        assert asked == expected_calls and store.card("T-77") == ticket.card
        assert store.replay("T-77") == ticket.card
        status = client.get("/tickets/T-77/status", headers=auth).json()
        assert status == {"ticket_id": "T-77", "last_seq": 6, "backlog": 0, "hold": None, **counts}
        assert counts.items() <= client.get("/health").json().items()

        row = conn.execute("select * from event_proposals where ticket_id = 'T-77' and seq = 6")
        close = row.fetchone()
        assert close["usage"]["reported_usd"] == 0.00012
        assert close["usage"]["reasoning_tokens"] == 0
        stored_verdict = close["proposals"][0]["verification"]
        verification = close["verifier_usage"]
        if outcome == "yes":
            assert stored_verdict == encode(verdict)
            assert verification == {
                "calls": 1, "failures": 0, "providers": {"DeepInfra": 1},
                "usage": {
                    "calls": 1, "prompt_tokens": 1000, "completion_tokens": 50,
                    "cached_tokens": 800, "reported_usd": 0.00002, "reasoning_tokens": 25,
                },
            }  # fmt: skip
        elif outcome == "timeout":
            assert stored_verdict["verdict"] == "unsure" and "timeout" in stored_verdict["error"]
            assert verification["calls"] == verification["failures"] == 1
            assert "verifier failed for ticket T-77 event 6 target e5#0" in caplog.text
        else:
            assert stored_verdict is None and verification is None

        # Before the deadline nothing breaks; past it, the scheduler queues the
        # alert and a worker sends it. Running both twice sends it once.
        inbox = Inbox()
        worker = Worker(store, inbox, "deliverer-1")
        late = DEADLINE + timedelta(minutes=5)
        for moment in (late, late + timedelta(minutes=1)):
            run_due(store, moment)
            worker.run_once(moment)

        (alert,) = inbox.received
        assert alert.kind == "breached" and alert.ticket_id == "T-77"
        assert alert.recipient == OLGA.id  # who holds the ticket now, not who promised
        assert alert.due_at == DEADLINE
        assert store.outbox.pending() == 0

        clock.now = late
        brief = client.get("/tickets/T-77/brief", params={"format": "json"}, headers=auth)
        urgent = brief.json()["sections"][0]
        assert urgent["key"] == "urgent"
        (line,) = urgent["lines"]
        assert line["text"].startswith("ПРОСРОЧЕНО") and f"отвечает {OLGA.name}" in line["text"]
        (source,) = line["sources"]
        assert source == {
            "event_id": "e3", "seq": 3, "author_id": IVAN.id, "author": "Иван",
            "side": "company", "quote": PROMISE,
        }  # fmt: skip

        card = client.get("/tickets/T-77/card", headers=auth).json()
        assert card["commitment_status"] == {
            "e3#0": "breached",
            "e5#0": "fulfilled" if outcome == "yes" else "open",
        }
        replayed = client.get("/tickets/T-77/card", params={"as_of": 6}, headers=auth).json()
        assert replayed["card"] == card["card"] and asked == expected_calls
