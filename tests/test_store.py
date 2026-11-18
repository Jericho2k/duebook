from dataclasses import replace
from datetime import timedelta

import psycopg
import pytest
from helpers import ANNA, CAL, IVAN, START, Ticket, persist, promise, story
from psycopg.types.json import Jsonb

from duebook import AlertPolicy, Card, Reason
from duebook.brief import compose
from duebook.proposals import (
    AssertFact,
    MakeCommitment,
    ResolveCommitment,
    Verdict,
    Verification,
)
from duebook.reducer import OutOfOrder
from duebook.store import Conflict, Store, connect
from duebook.store.codec import encode

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


def test_an_imported_ticket_with_a_promise_broken_before_go_live_queues_nothing(conn):
    ticket = promise("T-1")
    go_live = START + timedelta(days=30)
    store = Store(conn, CAL, AlertPolicy(alerts_since=go_live))
    persist(store, ticket, now=go_live)
    assert count(conn, "outbox") == 0
    assert conn.execute("select wake_at from tickets").fetchone()["wake_at"] is None
    assert store.wake(go_live + timedelta(days=1)) == 0 and count(conn, "outbox") == 0
    # Not an alert, but still the truth about the promise.
    brief = compose(store.card("T-1"), go_live, cal=CAL, policy=store.policy)
    assert "ПРОСРОЧЕНО" in brief.text


def test_a_promise_made_after_go_live_alerts_normally(conn):
    ticket = promise("T-1")
    store = Store(conn, CAL, AlertPolicy(alerts_since=START))
    persist(store, ticket, now=START + timedelta(hours=3))
    (row,) = conn.execute("select * from outbox").fetchall()
    assert row["kind"] == "breached" and row["recipient"] == IVAN.id


def test_the_ticket_row_records_when_its_alerts_next_change(store, conn):
    persist(store, promise("T-1"))
    row = conn.execute("select wake_at from tickets").fetchone()
    assert row["wake_at"] == START + timedelta(hours=1)  # due soon: half the two-hour span
    assert count(conn, "outbox") == 0


def test_a_verifiers_verdict_is_stored_with_the_close_and_replay_needs_no_model(store, conn):
    # The verdict travels in the stored proposals; replaying them reproduces
    # the card it helped make.
    ticket = promise("T-1")
    verdict = Verification(Verdict.YES, "звонок был", model="stub")
    call = ticket.card.commitments[0]
    ticket.say(ANNA, "Да, звонок был, спасибо.",
               ResolveCommitment(call.id, "звонок был", "Перезвоню вам", verdict))  # fmt: skip
    assert not ticket.card.commitments[0].unresolved
    persist(store, ticket)
    row = conn.execute("select * from event_proposals where seq = 3").fetchone()
    assert row["proposals"][0]["verification"]["verdict"] == "yes"
    assert store.replay("T-1") == ticket.card


@pytest.mark.parametrize("rules", [1, 2])
def test_old_closes_replay_under_their_original_rule(store, conn, rules):
    ticket = promise("T-1")
    call = ticket.card.commitments[0]
    if rules == 1:  # before the veto: a "no" did not stop a close
        quote = "Деньги пришли"
        close = ResolveCommitment(call.id, quote, "Перезвоню вам", Verification(Verdict.NO))
    else:  # before decision 0001's amendment: the question could name another thing
        ticket.say(IVAN, "Проверьте, был ли звонок насчёт монтажа?")
        quote = "Да, на месте"
        yes = Verification(Verdict.YES, quote)
        close = ResolveCommitment(call.id, quote, "Перезвоню вам", yes, "звонок насчёт монтажа")
    persist(store, ticket)
    ticket.say(ANNA, quote, close)
    event, _ = ticket.log[-1]
    assert ticket.card.commitments[0].unresolved
    store.ingest(event, START)
    conn.execute(
        "insert into event_proposals"
        " (ticket_id, seq, proposals, rejections, applied_at, close_rules)"
        " values (%s, %s, %s, '[]', %s, %s)",
        (ticket.card.ticket_id, event.seq, Jsonb(encode([close])), START, rules),
    )
    assert not store.replay("T-1").commitments[0].unresolved


def test_a_promise_recorded_before_decision_0003_replays_under_the_same_action_rule(store, conn):
    ticket = promise("T-1")
    persist(store, ticket)
    call = ticket.card.commitments[0]
    quote = "Перезвоню вам насчёт гарантии"
    again = MakeCommitment(call.owner, call.action, quote)
    ticket.say(IVAN, f"{quote}.", again)
    assert len(ticket.card.commitments) == 2
    event, _ = ticket.log[-1]
    store.ingest(event, START)
    conn.execute(
        "insert into event_proposals"
        " (ticket_id, seq, proposals, rejections, applied_at, close_rules)"
        " values (%s, %s, %s, '[]', %s, 3)",
        ("T-1", event.seq, Jsonb(encode([again])), START),
    )
    assert len(store.replay("T-1").commitments) == 1


def test_a_second_order_recorded_before_decision_0005_replays_as_a_dispute(store, conn):
    t = Ticket("T-1")
    t.say(ANNA, "Проблема с заказом 512407", AssertFact("order.number", "512407", "512407"))
    second = AssertFact("order.number", "538916", "538916")
    t.say(IVAN, "Оформим возврат по заказу 538916", second)
    assert t.card.open_contradictions() == ()
    first, _ = t.log[0]
    event, _ = t.log[-1]
    store.record(first, t.log[0][1], now=first.at)
    store.ingest(event, START)
    conn.execute(
        "insert into event_proposals"
        " (ticket_id, seq, proposals, rejections, applied_at, close_rules)"
        " values (%s, %s, %s, '[]', %s, 4)",
        ("T-1", event.seq, Jsonb(encode([second])), START),
    )
    assert len(store.replay("T-1").open_contradictions()) == 1
