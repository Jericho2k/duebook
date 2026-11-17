from datetime import timedelta

import pytest
from helpers import ANNA, CAL, IVAN, OLGA, START, persist, promise

from duebook import AssigneeChanged, Notice, NoticeKind
from duebook.proposals import ResolveCommitment
from duebook.store import Store, Worker, connect
from duebook.store.scheduler import idle_for, run, run_due

pytestmark = pytest.mark.usefixtures("conn")

LATE = START + timedelta(hours=3)
TTL = timedelta(minutes=5)


def notice(n: int) -> Notice:
    return Notice(f"T-{n}:c:breached:x", NoticeKind.BREACHED, f"T-{n}", "c", START, IVAN.id)


def rows(conn):
    return conn.execute("select * from outbox order by key").fetchall()


class Recording:
    """A channel that remembers what it was asked to send."""

    def __init__(self, fail: int = 0):
        self.sent: list[str] = []
        self.fail = fail

    def send(self, n: Notice) -> None:
        if self.fail:
            self.fail -= 1
            raise ConnectionError("channel is down")
        self.sent.append(n.key)


# --------------------------------------------------------------- outbox ----


def test_the_same_notice_derived_twice_is_queued_once(store, conn):
    assert store.outbox.enqueue([notice(1), notice(2)], START) == (notice(1).key, notice(2).key)
    assert store.outbox.enqueue([notice(1), notice(3)], LATE) == (notice(3).key,)
    assert [r["key"] for r in rows(conn)] == [notice(n).key for n in (1, 2, 3)]


def test_a_claimed_notice_is_not_given_to_a_second_worker(store):
    store.outbox.enqueue([notice(n) for n in range(4)], START)
    first = store.outbox.claim("w1", START, limit=3)
    second = store.outbox.claim("w2", START, limit=3)
    assert len(first) == 3 and len(second) == 1
    assert not {c.notice.key for c in first} & {c.notice.key for c in second}
    assert store.outbox.claim("w3", START) == []


def test_a_worker_skips_rows_another_is_still_claiming_instead_of_waiting(store, dsn):
    store.outbox.enqueue([notice(n) for n in range(5)], START)
    with connect(dsn) as other_conn:
        other = Store(other_conn, CAL)
        with store.conn.transaction():
            # The first claim is not committed yet: its rows are locked.
            mine = store.outbox.claim("w1", START, limit=3)
            theirs = other.outbox.claim("w2", START, limit=5)
    assert len(mine) == 3 and len(theirs) == 2
    assert not {c.notice.key for c in mine} & {c.notice.key for c in theirs}


def test_a_claim_is_a_lease_that_another_worker_takes_over_when_it_goes_stale(store):
    store.outbox.enqueue([notice(1)], START)
    (dead,) = store.outbox.claim("w1", START)
    assert store.outbox.claim("w2", START + TTL - timedelta(seconds=1)) == []
    (taken,) = store.outbox.claim("w2", START + TTL)
    assert taken.notice == dead.notice and taken.token != dead.token and taken.attempts == 2


def test_a_delivery_is_recorded_once(store, conn):
    store.outbox.enqueue([notice(1)], START)
    (claim,) = store.outbox.claim("w1", START)
    assert store.outbox.delivered(claim, START) is True
    assert store.outbox.delivered(claim, LATE) is False
    assert rows(conn)[0]["delivered_at"] == START
    assert store.outbox.claim("w2", LATE + TTL) == [] and store.outbox.pending() == 0


def test_a_worker_that_lost_its_lease_cannot_hand_the_notice_back(store, conn):
    store.outbox.enqueue([notice(1)], START)
    (slow,) = store.outbox.claim("w1", START)
    (fast,) = store.outbox.claim("w2", START + TTL)
    assert store.outbox.failed(slow, "timeout", START + TTL) is False
    assert rows(conn)[0]["claim_token"].hex == fast.token.replace("-", "")


def test_a_failed_send_goes_back_to_the_queue_for_later(store, conn):
    store.outbox.enqueue([notice(1)], START)
    (claim,) = store.outbox.claim("w1", START)
    assert store.outbox.failed(claim, "http 503", START + timedelta(minutes=1))
    assert store.outbox.claim("w1", START + timedelta(seconds=59)) == []
    (again,) = store.outbox.claim("w1", START + timedelta(minutes=1))
    assert again.attempts == 2 and rows(conn)[0]["last_error"] == "http 503"


# --------------------------------------------------------------- worker ----


def test_the_worker_sends_a_due_notice_and_records_it(store, conn):
    persist(store, promise("T-1"), now=LATE)
    channel = Recording()
    done = Worker(store, channel, "w1").run_once(LATE)
    assert (done.sent, done.discarded, done.failed) == (1, 0, 0)
    assert channel.sent == [rows(conn)[0]["key"]] and rows(conn)[0]["delivered_at"] == LATE
    assert Worker(store, channel, "w1").run_once(LATE + TTL).claimed == 0


def test_an_alert_that_stopped_being_true_is_retired_instead_of_sent(store, conn):
    ticket = promise("T-1")
    persist(store, ticket, now=LATE)  # the breach alert is queued...
    ticket.now = LATE
    ticket.say(
        ANNA,
        "Мне перезвонили, спасибо",
        ResolveCommitment("e2#0", "Мне перезвонили", "Перезвоню вам"),
    )
    store.record(*ticket.log[-1], now=LATE)  # ...and the customer confirms the call
    channel = Recording()
    done = Worker(store, channel, "w1").run_once(LATE)
    assert channel.sent == [] and done.discarded == 1
    assert rows(conn)[0]["discard_reason"] == "no longer due" and store.outbox.pending() == 0


def test_an_alert_queued_before_a_handoff_goes_to_whoever_holds_the_ticket_now(store, conn):
    persist(store, promise("T-1"))
    run_due(store, LATE)
    assert rows(conn)[0]["recipient"] == IVAN.id

    handoff = AssigneeChanged(
        id="e3", ticket_id="T-1", seq=3, at=LATE, previous=IVAN.id, current=OLGA.id
    )
    store.record(handoff, (), now=LATE)

    class Recipients:
        def __init__(self):
            self.seen = []

        def send(self, n: Notice) -> None:
            self.seen.append((n.key, n.recipient))

    channel = Recipients()
    Worker(store, channel, "w1").run_once(LATE + timedelta(minutes=1))
    assert channel.seen == [(rows(conn)[0]["key"], OLGA.id)]


def test_a_channel_failure_is_retried_with_a_growing_delay_until_it_succeeds(store, conn):
    persist(store, promise("T-1"), now=LATE)
    channel = Recording(fail=2)
    worker = Worker(store, channel, "w1", retry_base=timedelta(seconds=30))
    assert worker.run_once(LATE).failed == 1
    assert worker.run_once(LATE + timedelta(seconds=29)).claimed == 0
    assert worker.run_once(LATE + timedelta(seconds=30)).failed == 1
    assert worker.run_once(LATE + timedelta(seconds=89)).claimed == 0  # second delay is 60 s
    assert worker.run_once(LATE + timedelta(seconds=90)).sent == 1
    assert len(channel.sent) == 1 and rows(conn)[0]["attempts"] == 3


# ------------------------------------------------------------ scheduler ----


def test_the_scheduler_touches_only_tickets_whose_time_has_come(store, conn):
    for n in range(20):
        persist(store, promise(f"T-{n}", START + timedelta(hours=n)))
    # Promise n is due soon one hour after it was made, i.e. at START + n + 1 h.
    assert run_due(store, START + timedelta(hours=3)) == 3
    assert [r["ticket_id"] for r in rows(conn)] == ["T-0", "T-1", "T-2"]
    assert idle_for(store, START + timedelta(hours=3), timedelta(days=1)) == timedelta(hours=1)


def test_the_scheduler_sleeps_exactly_until_the_next_deadline_matters(store, conn):
    persist(store, promise("T-1"))
    now, naps = [START], []

    def sleep(seconds: float) -> None:
        naps.append(timedelta(seconds=seconds))
        now[0] += naps[-1]

    run(store, clock=lambda: now[0], sleep=sleep, max_idle=timedelta(days=7),
        stop=lambda: len(naps) == 3)  # fmt: skip
    # Due soon after one hour, broken after two, then nothing left to wait for.
    assert naps == [timedelta(hours=1), timedelta(hours=1), timedelta(days=7)]
    assert [r["kind"] for r in rows(conn)] == ["breached", "due_soon"]
    assert [r["enqueued_at"] - START for r in rows(conn)] == [
        timedelta(hours=2), timedelta(hours=1),
    ]  # fmt: skip


def test_an_idle_scheduler_still_wakes_within_its_bound(store):
    assert idle_for(store, START, timedelta(seconds=30)) == timedelta(seconds=30)
    persist(store, promise("T-1"))
    assert idle_for(store, START, timedelta(seconds=30)) == timedelta(seconds=30)
    assert idle_for(store, START + timedelta(hours=5), timedelta(seconds=30)) == timedelta(0)


class DiesOnSecondTicket(Store):
    calls = 0

    def _settle(self, card, now):
        type(self).calls += 1
        if type(self).calls == 2:
            raise KeyboardInterrupt("killed")
        return Store._settle(self, card, now)


def test_a_scheduler_killed_mid_pass_redoes_the_pass_and_queues_nothing_twice(store, conn):
    for n in range(3):
        persist(store, promise(f"T-{n}"))
    with pytest.raises(KeyboardInterrupt):
        DiesOnSecondTicket(conn, CAL).wake(LATE)
    assert rows(conn) == []  # the half-finished pass left nothing behind
    assert run_due(store, LATE) == 3 and run_due(store, LATE) == 0
    assert sorted(r["ticket_id"] for r in rows(conn)) == ["T-0", "T-1", "T-2"]
