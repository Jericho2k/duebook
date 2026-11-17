"""The delivery worker: claim a notice, check it is still true, send it, record it.

The order of those four steps is the whole design.

* Claim first, in its own committed transaction, so two workers never hold the
  same notice at once.
* Check before sending. An alert is queued the moment it becomes due, and the
  ticket can move on before a worker gets to it — the customer confirms the
  refund, the promise is renegotiated. An alert that is no longer derivable
  from the current card is retired, not sent: a false alarm costs more than a
  missed one.
  The same check re-reads who the alert is for, so a ticket handed over
  while its alert waited in the queue alerts the operator who holds it now.
* Send, then record. A worker killed between the two leaves a claimed,
  unrecorded notice; its lease expires and another worker sends it again under
  the same key. Recording first would turn that same crash into a lost alert,
  which is the worse failure.

So the channel must treat ``Notice.key`` as an idempotency key. That is the
one requirement this design places on the outside world.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from ..lifecycle import Notice, notices
from .store import Store


class Channel(Protocol):
    def send(self, notice: Notice) -> None:
        """Deliver one alert. Must be idempotent on ``notice.key``. Raises on failure."""


@dataclass(frozen=True, slots=True)
class Round:
    """What one pass of a worker did."""

    sent: int = 0
    #: Retired because the card no longer supports them.
    discarded: int = 0
    #: The channel raised; the notice went back to the queue.
    failed: int = 0

    @property
    def claimed(self) -> int:
        return self.sent + self.discarded + self.failed


@dataclass(slots=True)
class Worker:
    store: Store
    channel: Channel
    name: str
    batch: int = 10
    #: First retry delay after a failed send; doubles per attempt up to the cap.
    retry_base: timedelta = timedelta(seconds=30)
    retry_cap: timedelta = timedelta(hours=1)

    def run_once(self, now: datetime) -> Round:
        sent = discarded = failed = 0
        for claim in self.store.outbox.claim(self.name, now, self.batch):
            card = self.store.card(claim.notice.ticket_id)
            current = {n.key: n for n in notices(card, now, self.store.policy)}
            # Send the alert as the card derives it NOW, not as it was queued.
            # The key says what the alert is about and does not change, but who
            # must hear it does: a ticket handed over after the alert was
            # queued owes it to the new operator, not the one who left.
            notice = current.get(claim.notice.key)
            if notice is None:
                self.store.outbox.discard(claim, "no longer due", now)
                discarded += 1
                continue
            try:
                self.channel.send(notice)
            except Exception as error:  # any channel fault is retried
                delay = min(self.retry_base * 2 ** (claim.attempts - 1), self.retry_cap)
                self.store.outbox.failed(claim, repr(error), now + delay)
                failed += 1
                continue
            self.store.outbox.delivered(claim, now)
            sent += 1
        return Round(sent, discarded, failed)

    def run(
        self,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
        idle: timedelta = timedelta(seconds=5),
        stop: Callable[[], bool] = lambda: False,
    ) -> None:
        while not stop():
            if not self.run_once(clock()).claimed:
                sleep(idle.total_seconds())
