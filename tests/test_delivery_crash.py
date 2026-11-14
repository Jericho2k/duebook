"""The stated result: a worker killed at any point loses no alert and repeats none.

Two tests make the claim from two sides.

The first is exhaustive and deterministic: for every notice in a batch and for
each of the two places a worker can die around a send — after claiming and
before sending, after sending and before recording — the worker is killed
there, its connection is dropped, and a new worker takes over.

The second is blunt: real worker processes are killed with SIGKILL at random
moments, again and again, while they drain a few hundred notices.

Both count what the *recipient* sees. The channel honours ``Notice.key`` as an
idempotency key, as the design requires, and the tests also count the raw send
attempts so that the price of a crash is stated rather than hidden: an attempt
is repeated only when a worker died between sending and recording, at most
once per such death.
"""

from __future__ import annotations

import os
import random
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from helpers import CAL, MSK, START, persist, promise

from duebook.store import Store, Worker, connect
from duebook.store.scheduler import run_due

pytestmark = pytest.mark.usefixtures("conn")

LATE = START + timedelta(hours=3)
TTL = timedelta(minutes=5)
NOTICES = 6


class Killed(BaseException):
    """Stands in for the process dying: nothing may catch it and carry on."""


class IdempotentChannel:
    """An external channel that shows the recipient each key once."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self.inbox: list[str] = []
        self.kill_at: tuple[str, int] | None = None

    def send(self, notice) -> None:
        index = len(self.attempts)
        if self.kill_at == ("before_send", index):
            self.kill_at = None
            raise Killed
        self.attempts.append(notice.key)
        if notice.key not in self.inbox:
            self.inbox.append(notice.key)
        if self.kill_at == ("after_send", index):
            self.kill_at = None
            raise Killed


def overdue(store: Store, count: int, start: datetime = START) -> set[str]:
    """``count`` tickets, each with one broken promise queued for alerting."""
    for n in range(count):
        persist(store, promise(f"T-{n:03d}", start))
    run_due(store, start + timedelta(hours=3))
    keys = {r["key"] for r in store.conn.execute("select key from outbox")}
    assert len(keys) == count
    return keys


@pytest.mark.parametrize("point", ["before_send", "after_send"])
@pytest.mark.parametrize("index", range(NOTICES))
def test_a_worker_killed_around_any_send_loses_nothing_and_repeats_nothing(
    store, dsn, point, index
):
    keys = overdue(store, NOTICES)
    channel = IdempotentChannel()
    channel.kill_at = (point, index)

    doomed = connect(dsn)
    victim = Worker(Store(doomed, CAL), channel, "w1", batch=4)
    with pytest.raises(Killed):
        while True:
            victim.run_once(LATE)
    doomed.close()  # the process is gone; whatever it had not committed is too

    # Until the dead worker's leases expire its notices are untouchable...
    survivor = Worker(store, channel, "w2", batch=4)
    while survivor.run_once(LATE).claimed:
        pass
    assert store.outbox.pending() > 0
    # ...and afterwards they are taken over.
    while survivor.run_once(LATE + TTL).claimed:
        pass

    assert sorted(channel.inbox) == sorted(keys)  # 0 lost, 0 duplicated
    assert store.outbox.pending() == 0
    delivered = store.conn.execute(
        "select count(*) as n from outbox where delivered_at is not null"
    ).fetchone()["n"]
    assert delivered == NOTICES
    # The whole cost of the crash: one repeated attempt, and only when the
    # worker died after the message had left.
    repeated = len(channel.attempts) - NOTICES
    assert repeated == (1 if point == "after_send" else 0)


def test_two_workers_draining_together_send_each_notice_once(store, dsn):
    keys = overdue(store, 25)
    channel = IdempotentChannel()
    with connect(dsn) as other:
        workers = [Worker(store, channel, "w1", batch=3), Worker(Store(other, CAL), channel, "w2")]
        while sum(w.run_once(LATE).claimed for w in workers):
            pass
    assert sorted(channel.attempts) == sorted(keys)


# ----------------------------------------------------- real processes ----

SCRIPT = Path(__file__).with_name("crash_worker.py")
#: A Monday well in the past: the workers below run on the real clock.
PAST = datetime(2026, 1, 12, 10, 0, tzinfo=MSK)
REAL_TTL = 1.0


def spawn(dsn: str, name: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.parents[1] / "src")}
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), dsn, name, str(REAL_TTL)],
        stdout=subprocess.PIPE, env=env, text=True,
    )  # fmt: skip
    assert process.stdout.readline().strip() == "ready"
    return process


def test_workers_killed_with_sigkill_at_random_moments_lose_nothing_and_repeat_nothing(
    store, conn, dsn
):
    total = 300
    conn.execute("drop table if exists channel_attempts, channel_inbox")
    conn.execute("create table channel_attempts (key text not null, worker text not null)")
    conn.execute("create table channel_inbox (key text primary key)")
    keys = overdue(store, total, PAST)
    rng = random.Random(20270111)

    kills = 0
    deadline = time.monotonic() + 120
    while store.outbox.pending() and time.monotonic() < deadline:
        generation = [spawn(dsn, f"gen{kills}-{n}") for n in range(2)]
        time.sleep(rng.uniform(0.01, 0.15))
        for process in generation:
            process.send_signal(signal.SIGKILL)
            process.wait()
            kills += 1

    assert store.outbox.pending() == 0, "the queue was not drained in time"
    assert kills >= 6, "too few kills for the test to mean anything"

    inbox = {r["key"] for r in conn.execute("select key from channel_inbox")}
    attempts = conn.execute("select count(*) as n from channel_attempts").fetchone()["n"]
    outbox = conn.execute(
        "select count(*) filter (where delivered_at is not null) as delivered,"
        " count(*) filter (where attempts > 1) as reclaimed from outbox"
    ).fetchone()

    assert inbox == keys  # 0 lost, 0 duplicated: each alert reached the recipient once
    assert outbox["delivered"] == total  # and each delivery is recorded
    # The test is only worth something if kills really stranded claimed notices.
    assert outbox["reclaimed"] > 0
    # A repeated attempt needs a worker to die between sending and recording,
    # so there can be at most one per killed process.
    assert 0 <= attempts - total <= kills
    print(
        f"\n{total} notices, {kills} SIGKILLs, {outbox['reclaimed']} notices re-claimed after "
        f"a dead worker, {attempts - total} repeated attempts absorbed by the key, "
        f"0 lost, 0 duplicated ({datetime.now(UTC):%Y-%m-%d})"
    )
