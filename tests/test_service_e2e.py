"""The whole service, end to end: webhook, processor, scheduler, delivery, brief.

Events arrive through the HTTP API out of order and one of them twice; a
processor applies them with a scripted extractor; the scheduler and a delivery
worker run at a moment past the promise's deadline. The alert must arrive
once, to the operator who holds the ticket by then, and the brief must show
the broken promise with the message it rests on.
"""

import json
from datetime import timedelta

import pytest
from helpers import ANNA, CAL, IVAN, OLGA, START, Ticket
from test_pipeline import Scripted

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from duebook import Party, signing  # noqa: E402
from duebook.api import create_app  # noqa: E402
from duebook.pipeline import Pipeline  # noqa: E402
from duebook.proposals import MakeCommitment  # noqa: E402
from duebook.settings import Settings  # noqa: E402
from duebook.store import Store, Worker  # noqa: E402
from duebook.store.codec import encode  # noqa: E402
from duebook.store.scheduler import run_due  # noqa: E402

pytestmark = pytest.mark.usefixtures("conn")

SECRET = "hook-secret"
KEY = "read-key"
PROMISE = "Перезвоню вам через 2 часа"
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


def conversation() -> Ticket:
    t = Ticket("T-77")
    t.assign(None, IVAN.id)
    t.say(ANNA, "Мне так и не перезвонили по заказу 4815.")
    t.wait(minutes=15)
    t.say(
        IVAN,
        PROMISE + ".",
        MakeCommitment(Party.AGENT, "перезвонить", PROMISE, due_quote="через 2 часа"),
    )
    t.wait(minutes=30)
    t.assign(IVAN.id, OLGA.id)  # Ivan's shift ends; the promise passes to Olga
    return t  # fmt: skip


def deliver(client: TestClient, event) -> dict:
    body = json.dumps(encode(event), ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", signing.HEADER: signing.sign(SECRET, body)}
    response = client.post("/events", content=body, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


def test_a_promise_posted_through_the_webhook_is_alerted_once_to_its_holder_and_briefed(conn, dsn):
    ticket = conversation()
    events = [event for event, _ in ticket.log]
    clock = Clock(START + timedelta(hours=1))
    settings = Settings(
        database_url=dsn, webhook_secret=SECRET, api_key=KEY, timezone="Europe/Moscow"
    )
    store = Store(conn, CAL, settings.policy())

    with TestClient(create_app(settings, clock=clock)) as client:
        # The helpdesk delivers out of order and repeats itself.
        acks = [deliver(client, events[n]) for n in (2, 0, 3, 2, 1)]
        assert [(a["seq"], a["new"]) for a in acks] == [
            (3, True), (1, True), (4, True), (3, False), (2, True),
        ]  # fmt: skip
        # Acknowledged, not yet read: the webhook never calls the model.
        auth = {"Authorization": f"Bearer {KEY}"}
        status = client.get("/tickets/T-77/status", headers=auth).json()
        assert status == {"ticket_id": "T-77", "last_seq": 0, "backlog": 4, "hold": None}

        reader = Scripted(ticket)
        processor = Pipeline(store, reader, clock=clock)
        (handled,) = processor.process()
        assert handled.applied == (1, 2, 3, 4) and processor.process() == []
        assert len(reader.calls) == 4 and store.card("T-77") == ticket.card

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
        assert line["text"].startswith("ПРОСРОЧЕНО") and "отвечает agent-olga" in line["text"]
        (source,) = line["sources"]
        assert source == {
            "event_id": "e3", "seq": 3, "author_id": IVAN.id, "author": "Иван",
            "side": "company", "quote": PROMISE,
        }  # fmt: skip

        card = client.get("/tickets/T-77/card", headers=auth).json()
        assert card["commitment_status"] == {"e3#0": "breached"}
