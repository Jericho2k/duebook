"""The processor: the half of the pipeline that runs apart from the webhook."""

import random
import threading
from datetime import timedelta

import pytest
from helpers import CAL, START, Ticket, promise, story
from test_pipeline import Clock, Scripted, applied

from duebook.pipeline import Pipeline
from duebook.store import Store, connect

pytestmark = pytest.mark.usefixtures("conn")


def ingest(store, ticket: Ticket, seqs=None) -> None:
    for event, _ in ticket.log:
        if seqs is None or event.seq in seqs:
            store.ingest(event, START)


def test_ingesting_an_event_marks_its_ticket_as_having_work(store):
    ingest(store, story())
    assert store.pending() == [("T-1", 7)]
    assert store.card("T-1").last_seq == 0


def test_the_processor_applies_everything_ingested_and_then_has_nothing_to_do(store, conn):
    ticket = story()
    reader = Scripted(ticket)
    p = Pipeline(store, reader, clock=Clock())
    ingest(store, ticket)

    (handled,) = p.process()
    assert handled.applied == tuple(range(1, 8)) and store.card("T-1") == ticket.card
    assert store.pending() == [] and p.process() == []
    assert len(reader.calls) == 7


def test_a_ticket_waiting_on_a_gap_is_not_picked_up_again_until_the_gap_closes(store, conn):
    ticket = story()
    reader = Scripted(ticket)
    p = Pipeline(store, reader, clock=Clock())
    ingest(store, ticket, {1, 2, 4, 5})

    (handled,) = p.process()
    assert handled.applied == (1, 2) and handled.waiting_for == 3
    assert p.process() == []  # no spinning on a ticket that cannot move

    ingest(store, ticket, {3})
    (handled,) = p.process()
    assert handled.applied == (3, 4, 5)


def test_an_event_that_arrives_just_after_the_processor_looked_is_not_lost(conn):
    ticket = story()
    late = ticket.log[2][0]

    class Racing(Store):
        def backlog(self, ticket_id):
            # The processor has just found no next event; the webhook stores
            # one before the processor records that it is done.
            if self.event(ticket_id, 3) is None:
                self.ingest(late, START)
            return Store.backlog(self, ticket_id)

    store = Racing(conn, CAL)
    p = Pipeline(store, Scripted(ticket), clock=Clock())
    ingest(store, ticket, {1, 2})
    (handled,) = p.process()
    assert handled.applied == (1, 2)
    assert store.pending() == [("T-1", 3)]
    (handled,) = p.process()
    assert handled.applied == (3,) and store.pending() == []


def test_a_held_ticket_is_left_alone_until_its_retry_is_due(store, conn):
    ticket = story()
    reader = Scripted(ticket, fail_at=3, failures=1)
    p = Pipeline(store, reader, clock=Clock())
    ingest(store, ticket, {1, 2, 3})

    (handled,) = p.process()
    assert handled.held.seq == 3 and applied(conn) == [1, 2]
    calls = len(reader.calls)

    ingest(store, ticket, {4, 5, 6, 7})  # new messages do not hurry a failing model
    p.process()
    assert p.process() == [] and len(reader.calls) == calls

    p.clock.now += timedelta(minutes=1)
    (handled,) = p.process()
    assert handled.applied == (3, 4, 5, 6, 7) and store.card("T-1") == ticket.card
    assert p.process() == []


def test_a_processor_passes_over_a_ticket_another_one_is_working_on(store, dsn):
    ticket = story()
    reader = Scripted(ticket)
    p = Pipeline(store, reader, clock=Clock())
    ingest(store, ticket)
    with connect(dsn) as other_conn:
        other = Store(other_conn, CAL)
        with other.exclusive("T-1"):
            assert p.process() == [] and reader.calls == []
        # Skipped, not marked done: the ticket is still waiting for whoever is free.
        assert store.pending() == [("T-1", 7)]
    (handled,) = p.process()
    assert handled.applied == tuple(range(1, 8))


def test_several_processors_apply_every_event_of_every_ticket_once_and_in_order(store, dsn):
    tickets = [promise(f"P-{n}") for n in range(12)] + [story()]
    reader = Scripted(*tickets)
    events = [event for t in tickets for event, _ in t.log]
    random.Random(3).shuffle(events)
    for event in events:
        store.ingest(event, START)

    def work():
        with connect(dsn) as conn:
            p = Pipeline(Store(conn, CAL), reader, clock=Clock())
            while p.process(limit=3):
                pass

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for t in tickets:
        assert store.card(t.card.ticket_id) == t.card
    assert len(reader.calls) == len(events)
    assert store.pending() == []


def test_a_stopped_processor_finishes_the_ticket_in_hand_and_takes_no_other(store):
    first, second = promise("T-a"), promise("T-b")
    p = Pipeline(store, Scripted(first, second), clock=Clock())
    ingest(store, first)
    ingest(store, second)
    asked = []

    def stop():
        asked.append(True)
        return len(asked) > 1

    (handled,) = p.process(stop=stop)
    assert handled.applied == (1, 2)
    assert len(store.pending()) == 1


def test_the_processor_loop_sleeps_only_when_a_pass_found_nothing(store):
    ticket = promise("T-1")
    p = Pipeline(store, Scripted(ticket), clock=Clock())
    ingest(store, ticket)
    sleeps = []
    p.run(sleep=sleeps.append, stop=lambda: len(sleeps) >= 2)
    # The first pass did the work and went straight on; the next two were idle.
    assert store.card("T-1") == ticket.card
    assert sleeps == [1.0, 1.0]


def test_a_ticket_that_cannot_be_advanced_is_held_and_does_not_starve_the_others(conn, caplog):
    broken, fine = promise("T-broken"), promise("T-fine")

    class Faulty(Store):
        def record(self, event, *args, **kwargs):
            if event.ticket_id == "T-broken" and event.seq == 2:
                raise RuntimeError("corrupt row")
            return Store.record(self, event, *args, **kwargs)

    store = Faulty(conn, CAL)
    reader = Scripted(broken, fine)
    p = Pipeline(store, reader, clock=Clock())
    ingest(store, broken)
    ingest(store, fine)

    done = {h.ticket_id: h for h in p.process()}
    assert store.card("T-fine") == fine.card and done["T-fine"].held is None
    assert "advancing T-broken failed" in caplog.text
    hold = done["T-broken"].held
    assert hold.seq == 2 and "corrupt row" in hold.error
    # Backing off like a failed extraction: no model call on every pass.
    calls = len(reader.calls)
    assert p.process() == [] and len(reader.calls) == calls
    p.clock.now = hold.retry_at
    (again,) = p.process()
    assert again.held.attempts == 2 and len(reader.calls) == calls + 1
