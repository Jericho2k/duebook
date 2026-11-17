import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta

import pytest
from helpers import CAL, START, persist, promise, story
from test_outbox import Recording, rows
from test_pipeline import Scripted

from duebook import cli
from duebook.settings import Settings
from duebook.store import Worker
from duebook.store.scheduler import run_due

pytestmark = pytest.mark.usefixtures("conn")

LATE = START + timedelta(hours=3)


def once() -> argparse.Namespace:
    return argparse.Namespace(once=True)


# ------------------------------------------------------- stopping cleanly ----


def test_a_stopped_worker_sends_the_alert_in_hand_and_gives_the_rest_back(store, conn):
    for n in range(4):
        persist(store, promise(f"T-{n}"), now=LATE)
    channel = Recording()
    asked = []

    def stop():
        asked.append(True)
        return len(asked) > 1

    done = Worker(store, channel, "w1", batch=4).run_once(LATE, stop)
    assert done.sent == 1 and len(channel.sent) == 1
    # Released, not left leased: another worker takes them at once, as first attempts.
    again = store.outbox.claim("w2", LATE, limit=10)
    assert len(again) == 3 and {c.attempts for c in again} == {1}


def test_a_stopped_scheduler_ends_its_pass_between_batches(store, conn):
    for n in range(5):
        persist(store, promise(f"T-{n}"))
    assert run_due(store, LATE, batch=2, stop=iter([False, True]).__next__) == 2
    assert run_due(store, LATE, batch=2) == 3
    assert len(rows(conn)) == 5


def test_the_stopper_wakes_a_sleeping_loop_at_once():
    stopper = cli.Stopper()
    started = time.monotonic()
    stopper.set()
    stopper.sleep(30)
    assert stopper() and time.monotonic() - started < 1


# ----------------------------------------------------- commands in process ----


def test_process_once_applies_what_the_webhook_stored(store, dsn):
    ticket = story()
    for event, _ in ticket.log:
        store.ingest(event, START)
    settings = Settings(database_url=dsn, timezone="Europe/Moscow")
    assert cli.process(settings, once(), cli.Stopper(), reader=Scripted(ticket)) == 0
    assert store.card("T-1") == ticket.card


def test_schedule_once_and_deliver_once_send_a_due_alert(store, conn, dsn):
    # The commands read the real clock, so the promise was made and broken long ago.
    long_ago = START - timedelta(days=800)
    persist(store, promise("T-1", long_ago), now=long_ago)
    settings = Settings(database_url=dsn, timezone="Europe/Moscow")
    assert rows(conn) == []
    assert cli.schedule(settings, once(), cli.Stopper()) == 0
    (row,) = rows(conn)
    channel = Recording()
    assert cli.deliver(settings, once(), cli.Stopper(), sink=channel) == 0
    assert channel.sent == [row["key"]]


def test_the_processor_refuses_to_start_without_a_model_key(dsn):
    with pytest.raises(cli.ConfigError, match="OPENROUTER_API_KEY"):
        cli.process(Settings(database_url=dsn), argparse.Namespace(once=False), cli.Stopper())


def test_the_store_built_by_a_command_uses_the_configured_calendar_and_policy(dsn):
    settings = Settings(database_url=dsn, timezone="Europe/Moscow", alerts_since=START)
    seen = []

    def body(store):
        seen.append((store.cal, store.policy.alerts_since))
        raise SystemExit  # not an Exception: leaves supervise at once

    with pytest.raises(SystemExit):
        cli.supervise(settings, cli.Stopper(), body, "test")
    assert seen == [(CAL, START)]


# ------------------------------------------------------ real processes ----


def run_command(dsn, *args, **env):
    return subprocess.Popen(
        [sys.executable, "-m", "duebook", *args],
        env={**os.environ, "DUEBOOK_DATABASE_URL": dsn, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for(proc, text: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stderr.readline()
        if text in line:
            return
        if not line and proc.poll() is not None:
            break
    proc.kill()
    raise AssertionError(f"{text!r} never appeared; exit code {proc.poll()}")


@pytest.mark.parametrize(
    ("command", "role"),
    [("process", "processor"), ("schedule", "scheduler"), ("deliver", "deliverer")],
)
def test_each_loop_exits_cleanly_on_sigterm(dsn, command, role):
    proc = run_command(dsn, command, DUEBOOK_OPENROUTER_API_KEY="unused")
    wait_for(proc, f"{role}")
    time.sleep(0.3)  # let it reach its idle sleep
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 0
    assert "stopped" in proc.stderr.read()


def test_migrate_is_safe_to_run_on_an_up_to_date_database(dsn):
    proc = run_command(dsn, "migrate")
    assert proc.wait(timeout=30) == 0
    assert "up to date" in proc.stderr.read()


def test_a_configuration_error_exits_with_status_2_and_names_the_variable(dsn):
    proc = run_command(dsn, "deliver", DUEBOOK_ALERT_CHANNEL="pager")
    assert proc.wait(timeout=30) == 2
    assert "DUEBOOK_ALERT_CHANNEL" in proc.stderr.read()
