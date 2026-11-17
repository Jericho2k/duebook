"""The scheduler: sleep until the next deadline matters, not until the next poll.

Nothing happens on a ticket at the moment a promise becomes due soon or is
broken, so something has to notice. The naive way is to recompute every open
ticket every minute. ``lifecycle.next_wakeup`` makes that unnecessary: each
ticket row carries the next instant at which its alerts change, and the
scheduler

1. handles the tickets whose instant has come (an index range scan),
2. asks for the earliest remaining instant,
3. sleeps until then.

``max_idle`` bounds the sleep because a new event can create an earlier
wake-up while the scheduler is asleep. The event's own transaction already
queues whatever is due at that moment, so the bound only limits how late a
*future* instant created meanwhile can be noticed.

Being killed at any point is safe: see ``Store.wake``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from .store import Store


def run_due(store: Store, now: datetime, batch: int = 100) -> int:
    """Handle every ticket due at ``now``. Returns how many were handled."""
    total = 0
    while handled := store.wake(now, batch):
        total += handled
        if handled < batch:
            break
    return total


def idle_for(store: Store, now: datetime, max_idle: timedelta) -> timedelta:
    """How long the scheduler may sleep before anything can need it."""
    upcoming = store.next_wake()
    if upcoming is None:
        return max_idle
    return min(max_idle, max(upcoming - now, timedelta(0)))


def run(
    store: Store,
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    max_idle: timedelta = timedelta(seconds=30),
    stop: Callable[[], bool] = lambda: False,
) -> None:
    while not stop():
        run_due(store, clock())
        sleep(idle_for(store, clock(), max_idle).total_seconds())
