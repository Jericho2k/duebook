import random
import threading
from datetime import timedelta

import pytest
from helpers import ANNA, CAL, IVAN, START, Ticket, promise, story
from test_extract import FakeModel, due, output

from duebook import Card, CommitmentState
from duebook.extract import Extractor, ModelError, Usage
from duebook.extract.extractor import Extraction
from duebook.pipeline import Pipeline
from duebook.store import Store, connect

pytestmark = pytest.mark.usefixtures("conn")


class Scripted:
    """An extractor that answers from a ticket's log and can be made to fail."""

    def __init__(self, *tickets: Ticket, fail_at: int | None = None, failures: int = 0):
        self.answers = {(e.ticket_id, e.id): p for t in tickets for e, p in t.log}
        # Only the first ticket given is ever made to fail.
        self.fail_at, self.failures = (tickets[0].card.ticket_id, fail_at), failures
        self.calls: list[tuple[int, int]] = []
        self.lock = threading.Lock()

    def extract(self, card: Card, event) -> Extraction:
        with self.lock:
            self.calls.append((event.seq, card.last_seq))
            if (event.ticket_id, event.seq) == self.fail_at and self.failures:
                self.failures -= 1
                raise ModelError("gave up after 3 attempts: http 503", Usage(3, 2700, 0, 0, 0.0003))
        return Extraction(
            self.answers[event.ticket_id, event.id], usage=Usage(1, 900, 80, reported_usd=0.0001)
        )


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


def pipeline(store, extractor) -> Pipeline:
    return Pipeline(store, extractor, clock=Clock())


def applied(conn) -> list[int]:
    return [r["seq"] for r in conn.execute("select seq from event_proposals order by seq")]


# ----------------------------------------------------------------- order ----


def test_events_in_order_are_applied_one_by_one_to_the_reducers_card(store):
    ticket = story()
    reader = Scripted(ticket)
    p = pipeline(store, reader)
    for event, _ in ticket.log:
        handled = p.handle(event)
        assert handled.applied == (event.seq,) and handled.new and handled.held is None
    assert store.card("T-1") == ticket.card == handled.card


def test_the_model_reads_each_event_against_the_card_as_of_the_previous_one(store):
    ticket = story()
    reader = Scripted(ticket)
    p = pipeline(store, reader)
    for event, _ in ticket.log:
        p.handle(event)
    assert reader.calls == [(seq, seq - 1) for seq in range(1, 8)]


def test_an_event_ahead_of_its_predecessor_waits_and_is_applied_when_the_gap_closes(store, conn):
    ticket = story()
    events = [event for event, _ in ticket.log]
    reader = Scripted(ticket)
    p = pipeline(store, reader)
    p.handle(events[0])

    early = p.handle(events[3])
    assert early.applied == () and early.waiting_for == 2 and early.card.last_seq == 1
    assert p.handle(events[2]).waiting_for == 2
    assert applied(conn) == [1] and reader.calls == [(1, 0)]

    closed = p.handle(events[1])
    assert closed.applied == (2, 3, 4) and closed.waiting_for is None
    assert store.card("T-1") == store.replay("T-1")


def test_a_redelivered_event_is_not_read_or_applied_again(store, conn):
    ticket = story()
    reader = Scripted(ticket)
    p = pipeline(store, reader)
    for event, _ in ticket.log[:3]:
        p.handle(event)
    again = p.handle(ticket.log[1][0])
    assert again.new is False and again.applied == () and again.card.last_seq == 3
    assert len(reader.calls) == 3 and applied(conn) == [1, 2, 3]


def test_any_arrival_order_with_duplicates_ends_in_the_same_card(store):
    ticket = story()
    events = [event for event, _ in ticket.log] * 2
    random.Random(7).shuffle(events)
    reader = Scripted(ticket)
    p = pipeline(store, reader)
    for event in events:
        p.handle(event)
    assert store.card("T-1") == ticket.card
    assert sorted(seq for seq, _ in reader.calls) == list(range(1, 8))


def test_two_workers_on_one_ticket_apply_each_event_once_and_in_order(store, dsn):
    ticket = story()
    reader = Scripted(ticket)
    events = [event for event, _ in ticket.log] * 2
    random.Random(11).shuffle(events)

    def work(batch):
        with connect(dsn) as conn:
            p = pipeline(Store(conn, CAL), reader)
            for event in batch:
                p.handle(event)

    threads = [threading.Thread(target=work, args=(events[n::3],)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.card("T-1") == ticket.card
    assert reader.calls == [(seq, seq - 1) for seq in range(1, 8)]


# ------------------------------------------------------------------ hold ----


def test_a_failed_extraction_holds_the_ticket_and_records_nothing_for_the_event(store, conn):
    ticket = story()
    p = pipeline(store, Scripted(ticket, fail_at=3, failures=1))
    for event, _ in ticket.log[:2]:
        p.handle(event)

    handled = p.handle(ticket.log[2][0])
    assert handled.applied == () and handled.card.last_seq == 2
    assert handled.held.seq == 3 and "http 503" in handled.held.error
    assert handled.held.retry_at == START + timedelta(minutes=1)
    # Stored, so it cannot be lost — and not applied, so it is not "nothing found".
    assert store.event("T-1", 3) is not None
    assert applied(conn) == [1, 2] and store.card("T-1").last_seq == 2


def test_later_events_queue_behind_a_held_one_without_calling_the_model(store, conn):
    ticket = story()
    reader = Scripted(ticket, fail_at=3, failures=1)
    p = pipeline(store, reader)
    for event, _ in ticket.log[:3]:
        p.handle(event)
    calls = len(reader.calls)

    for event, _ in ticket.log[3:]:
        handled = p.handle(event)
        assert handled.held.seq == 3 and handled.applied == ()
    assert len(reader.calls) == calls and applied(conn) == [1, 2]
    assert store.backlog("T-1") == 5


def test_a_held_ticket_catches_up_completely_once_the_event_can_be_read(store, conn):
    ticket = story()
    reader = Scripted(ticket, fail_at=3, failures=1)
    p = pipeline(store, reader)
    for event, _ in ticket.log:
        p.handle(event)

    assert p.retry_held() == []  # not yet: still backing off
    p.clock.now += timedelta(minutes=1)
    (caught_up,) = p.retry_held()
    assert caught_up.applied == (3, 4, 5, 6, 7) and caught_up.held is None
    assert store.card("T-1") == ticket.card and store.held("T-1") is None
    assert conn.execute("select count(*) as n from holds").fetchone()["n"] == 0


def test_repeated_failures_back_off_and_their_cost_stays_on_the_event(store, conn):
    ticket = story()
    p = pipeline(store, Scripted(ticket, fail_at=1, failures=3))
    event = ticket.log[0][0]
    delays = []
    for _ in range(3):
        hold = p.handle(event).held
        delays.append(hold.retry_at - p.clock.now)
        p.clock.now = hold.retry_at
    assert delays == [timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=4)]
    assert hold.attempts == 3 and hold.first_failed_at == START

    assert p.handle(event).applied == (1,)
    usage = conn.execute("select usage from event_proposals").fetchone()["usage"]
    # Three failed attempts of three calls each, and the one that worked.
    assert usage["calls"] == 10 and usage["reported_usd"] == pytest.approx(0.001)


def test_a_held_ticket_does_not_stop_other_tickets(store):
    held, fine = promise("T-held"), promise("T-fine")
    p = pipeline(store, Scripted(held, fine, fail_at=2, failures=5))
    for (a, _), (b, _) in zip(held.log, fine.log, strict=True):
        p.handle(a)
        p.handle(b)
    assert store.card("T-held").last_seq == 1 and store.held("T-held") is not None
    assert store.card("T-fine").last_seq == 2 and store.held("T-fine") is None


# ---------------------------------------------------------- end to end ----


def test_an_alert_is_queued_by_the_event_that_makes_it_due(store, conn):
    ticket = promise("T-1")
    p = pipeline(store, Scripted(ticket))
    p.clock.now = START + timedelta(hours=3)  # the helpdesk delivered the ticket late
    for event, _ in ticket.log:
        p.handle(event)
    (row,) = conn.execute("select kind, recipient from outbox").fetchall()
    assert (row["kind"], row["recipient"]) == ("breached", IVAN.id)


def test_a_ticket_runs_from_recorded_model_responses_to_a_stored_card(store, conn):
    refund = {
        "owner": "company", "action": "вернуть деньги", "quote": "вернём деньги до пятницы",
        "due_quote": "до пятницы", "due": due(kind="weekday", weekday="friday"),
        "supersedes": None,
    }  # fmt: skip
    model = FakeModel(
        output(facts=[{"slot": "order.number", "value": "4815", "quote": "Заказ 4815",
                       "change": False}]),
        output(commitments=[refund], facts=[{"slot": "order.number", "value": 4815}]),
        output(resolved=[{"id": "C1", "target_quote": "вернём деньги", "quote": "Деньги пришли"}]),
    )  # fmt: skip
    t = Ticket()
    t.say(ANNA, "Заказ 4815 не пришёл, хочу вернуть деньги.")
    t.wait(minutes=10)
    t.say(IVAN, "Оформил возврат, вернём деньги до пятницы.")
    t.wait(days=2)
    t.say(ANNA, "Деньги пришли, спасибо.")

    p = Pipeline(store, Extractor(model, CAL), clock=Clock())
    for event, _ in t.log:
        handled = p.handle(event)

    (commitment,) = handled.card.commitments
    assert commitment.state is CommitmentState.FULFILLED
    assert commitment.source.quote == "вернём деньги до пятницы" and commitment.source.seq == 2
    assert [f.value for f in handled.card.current_facts()] == ["4815"]
    assert len(model.requests) == 3 and handled.usage.usd() == pytest.approx(0.0001)
    # The unreadable entry is counted, and the log alone rebuilds the card.
    row = conn.execute("select malformed from event_proposals where seq = 2").fetchone()
    assert [m["section"] for m in row["malformed"]] == ["facts"]
    assert store.replay("T-1") == handled.card
