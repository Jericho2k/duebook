from dataclasses import replace
from datetime import timedelta

import psycopg
import pytest
from helpers import ANNA, CAL, IVAN, START, persist, promise, story

from duebook import Card, Reason
from duebook.proposals import AssertFact
from duebook.reducer import OutOfOrder
from duebook.store import Conflict, Store, connect

pytestmark = pytest.mark.usefixtures("conn")


def count(conn, table: str) -> int:
    return conn.execute(f"select count(*) as n from {table}").fetchone()["n"]


# ------------------------------------------------------------ event log ----


def test_an_event_is_stored_once_however_often_it_is_delivered(store, conn):
    event, proposals = story().log[1]
    first = store.record(story().log[0][0], now=START)
    assert not first.replayed
    assert not store.record(event, proposals, now=START).replayed
    again = store.record(event, proposals, now=START)
    assert again.replayed and again.card == store.card("T-1")
    assert count(conn, "events") == 2 and count(conn, "event_proposals") == 2


def test_ingest_reports_whether_the_event_was_new(store):
    event = story().log[0][0]
    assert store.ingest(event, START) is True
    assert store.ingest(event, START) is False


def test_a_different_event_at_an_occupied_position_is_a_conflict_not_a_redelivery(store):
    event = story().log[1][0]
    store.ingest(event, START)
    with pytest.raises(Conflict):
        store.ingest(replace(event, text="совсем другое сообщение"), START)
    with pytest.raises(Conflict):
        store.ingest(replace(event, seq=9), START)  # same id, another position


def test_the_event_log_cannot_be_rewritten(store, conn):
    persist(store, story())
    for statement in (
        "update events set body = '{}' where seq = 2",
        "delete from events where seq = 2",
        "delete from event_proposals where seq = 2",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(statement)


def test_an_event_ahead_of_its_predecessor_writes_nothing(store, conn):
    log = story().log
    store.record(log[0][0], now=START)
    with pytest.raises(OutOfOrder):
        store.record(*log[2], now=START)
    assert count(conn, "events") == 1 and store.card("T-1").last_seq == 1


# ------------------------------------------------- snapshot and replay ----


def test_the_snapshot_is_the_card_the_reducer_produced(store):
    ticket = story()
    persist(store, ticket)
    assert store.card("T-1") == ticket.card


def test_a_card_is_rebuilt_from_the_log_without_asking_the_model_again(store, dsn):
    ticket = story()
    persist(store, ticket)
    with connect(dsn) as other:
        # A fresh process: nothing but the database, and no extractor anywhere.
        assert Store(other, CAL).replay("T-1") == ticket.card


def test_replay_can_stop_at_any_point_in_the_ticket(store):
    ticket = story()
    persist(store, ticket)
    as_of_3 = store.replay("T-1", upto=3)
    assert as_of_3.last_seq == 3
    assert [f.value for f in as_of_3.current_facts() if f.slot == "account.plan"] == ["Базовый"]
    assert [f.value for f in store.replay("T-1").current_facts() if f.slot == "account.plan"] == [
        "Про"
    ]


def test_refused_proposals_are_kept_with_the_reason_and_replay_refuses_them_again(store, conn):
    ticket = story()
    invented = AssertFact("order.status", "потерян", "такого в сообщении нет")
    ticket.say(ANNA, "Спасибо!", invented)
    persist(store, ticket)
    row = conn.execute("select * from event_proposals where seq = 8").fetchone()
    assert row["rejections"] == [{"index": 0, "reason": Reason.UNGROUNDED.value}]
    assert row["proposals"][0]["value"] == "потерян"
    assert store.replay("T-1") == ticket.card


def test_a_lost_snapshot_is_recomputed_from_the_log(store, conn):
    ticket = story()
    persist(store, ticket)
    conn.execute("update tickets set card = %s, last_seq = 0", (psycopg.types.json.Jsonb({}),))
    assert store.rebuild("T-1", START) == ticket.card
    assert store.card("T-1") == ticket.card


def test_an_unknown_ticket_has_an_empty_card(store):
    assert store.card("T-404") == Card("T-404")


# ---------------------------------------------------------- atomicity ----


class Exploding(Store):
    """A store whose alert queue fails, to interrupt a transaction half-way."""

    def _settle(self, card, now):
        raise RuntimeError("power cut")


def test_event_proposals_snapshot_and_alerts_are_written_together_or_not_at_all(conn):
    log = story().log
    Store(conn, CAL).record(log[0][0], now=START)
    with pytest.raises(RuntimeError):
        Exploding(conn, CAL).record(*log[1], now=START)
    assert count(conn, "events") == 1
    assert count(conn, "event_proposals") == 1
    assert Store(conn, CAL).card("T-1").last_seq == 1


def test_an_alert_already_due_is_queued_in_the_events_own_transaction(store, conn):
    ticket = promise("T-1")
    # Imported three hours late: the promise is already broken when it is stored.
    persist(store, ticket, now=START + timedelta(hours=3))
    (row,) = conn.execute("select * from outbox").fetchall()
    assert row["kind"] == "breached" and row["recipient"] == IVAN.id
    assert row["commitment_id"] == ticket.card.commitments[0].id


def test_the_ticket_row_records_when_its_alerts_next_change(store, conn):
    persist(store, promise("T-1"))
    row = conn.execute("select wake_at from tickets").fetchone()
    assert row["wake_at"] == START + timedelta(hours=1)  # due soon: half the two-hour span
    assert count(conn, "outbox") == 0
