"""One incoming event, all the way through: store, read, apply, persist, alert.

    handled = pipeline.handle(event)

The steps run in a fixed order and each guards the next.

1. **Ingest.** The event is stored before anything else is attempted. From
   here on it cannot be lost, so the caller may acknowledge it to the helpdesk
   whatever happens next. A redelivered event is recognised and ignored.
2. **Advance.** Under a per-ticket lock, stored events are applied strictly in
   ``seq`` order, starting after the last applied one. An event that arrived
   ahead of its predecessor simply waits in the log; when the missing one
   arrives, both are applied in the same pass.
3. **Extract, then record.** The model is shown the card as of the previous
   event and the one new message. What it proposes goes to
   ``Store.record``, which applies the reducer and writes the event's
   proposals, the snapshot and any due alerts in one transaction.

If extraction fails the ticket is **held** at that event. Nothing is recorded
for it — not an empty list, which would mean "a person read this and found
nothing" — and nothing after it is applied, because every later reading would
rest on a card that skipped a message. The hold is stored with the error and a
retry time, and ``retry_held`` picks it up.

``handle`` reports a hold in its return value instead of raising. The event is
already safe, so the caller should acknowledge it; a raise would make a queue
redeliver it and hammer a model that is down.

``handle`` does both halves in the caller's time. A service splits them: the
webhook only calls ``Store.ingest`` and answers at once, because a helpdesk
that waits seconds for a model call times out and redelivers; a processor
calls ``process`` in a loop to apply whatever has been stored.

Several processors may run at once. Each ticket is advanced under its
advisory lock, as before, but a processor *tries* the lock and passes over a
ticket another one holds, so two processors never queue behind each other on
one ticket. After advancing, a processor records how many ingests it had seen
(``Store.processed``); a ticket that is waiting on a gap or backing off after a
failure therefore drops out of the work list instead of being picked up on
every pass, and comes back when a new event arrives or its retry is due.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from .card import Card
from .events import Event
from .extract.cost import Usage
from .extract.extractor import Reader
from .store.store import Hold, Store

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Handled:
    ticket_id: str
    card: Card
    #: False when the event had been received before.
    new: bool = True
    #: The events this call applied, in order. Can be several, or none.
    applied: tuple[int, ...] = ()
    #: Set when the ticket is stopped at an event the extractor could not read.
    held: Hold | None = None
    #: The seq that stored later events are waiting for, if there is a gap.
    waiting_for: int | None = None
    #: What the model calls made by this call cost, failed ones included.
    usage: Usage = field(default_factory=Usage)


@dataclass(slots=True)
class Pipeline:
    store: Store
    extractor: Reader
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    #: Delay before the first retry of a failed extraction; doubles up to the cap.
    retry_base: timedelta = timedelta(minutes=1)
    retry_cap: timedelta = timedelta(hours=1)

    def handle(self, event: Event) -> Handled:
        """Take one event from the helpdesk. Safe to call again with the same event."""
        new = self.store.ingest(event, self.clock())
        return self._advance(event.ticket_id, new=new)

    def advance(self, ticket_id: str) -> Handled:
        """Apply every stored event of the ticket that can be applied now, in order.

        Waits for the ticket's lock if another worker has it.
        """
        return self._advance(ticket_id, new=False)

    def retry_held(self, limit: int = 100) -> list[Handled]:
        """Try again every held ticket whose retry time has come."""
        return [
            self._advance(hold.ticket_id, new=False)
            for hold in self.store.holds_due(self.clock(), limit)
        ]

    def process(self, limit: int = 20, stop: Callable[[], bool] = lambda: False) -> list[Handled]:
        """One pass of a processor: advance tickets with new events or a retry due.

        Returns what was done; tickets another processor holds are skipped and
        are not in the result. ``stop`` is checked between tickets, so a
        processor asked to exit finishes the ticket in hand and no more.
        """
        work: dict[str, int | None] = dict(self.store.pending(limit))
        for hold in self.store.holds_due(self.clock(), limit):
            work.setdefault(hold.ticket_id, None)
        done = []
        for ticket_id, seen in work.items():
            if stop():
                break
            with self.store.try_exclusive(ticket_id) as mine:
                if not mine:
                    continue
                try:
                    handled = self._advance(ticket_id, new=False)
                except (psycopg.OperationalError, psycopg.InterfaceError):
                    raise  # the connection is gone: the caller reconnects
                except Exception as error:
                    # A fault in one ticket must not starve the others, and must
                    # not be retried on every pass either: the model call before
                    # it may already have been paid for. The ticket is held,
                    # with backoff, like a failed extraction.
                    log.exception("advancing %s failed", ticket_id)
                    if not self._hold_after_fault(ticket_id, error):
                        continue
                    handled = Handled(ticket_id, self.store.card(ticket_id), new=False,
                                      held=self.store.held(ticket_id))  # fmt: skip
                done.append(handled)
                if seen is not None:
                    self.store.processed(ticket_id, seen)
        return done

    def _hold_after_fault(self, ticket_id: str, error: Exception) -> bool:
        """Hold the ticket at its next event after an unexpected fault. False if that failed too."""
        try:
            seq = self.store.card(ticket_id).last_seq + 1
            if self.store.event(ticket_id, seq) is None:
                return False
            previous = self.store.held(ticket_id)
            attempts = previous.attempts if previous is not None and previous.seq == seq else 0
            now = self.clock()
            self.store.hold(
                ticket_id, seq, f"{type(error).__name__}: {error}", now=now,
                retry_at=now + min(self.retry_base * 2**attempts, self.retry_cap),
                usage=previous.usage if previous is not None else None,
            )  # fmt: skip
        except (psycopg.OperationalError, psycopg.InterfaceError):
            raise
        except Exception:
            log.exception("could not hold %s either", ticket_id)
            return False
        return True

    def run(
        self,
        *,
        sleep: Callable[[float], None],
        idle: timedelta = timedelta(seconds=1),
        limit: int = 20,
        stop: Callable[[], bool] = lambda: False,
    ) -> None:
        """Process until ``stop`` says so, sleeping ``idle`` whenever a pass found nothing."""
        while not stop():
            if not self.process(limit, stop):
                sleep(idle.total_seconds())

    def _advance(self, ticket_id: str, *, new: bool) -> Handled:
        applied: list[int] = []
        spent = Usage()
        with self.store.exclusive(ticket_id):
            while True:
                card = self.store.card(ticket_id)
                event = self.store.event(ticket_id, card.last_seq + 1)
                if event is None:
                    break
                now = self.clock()
                hold = self.store.held(ticket_id)
                if hold is not None and hold.seq == event.seq and now < hold.retry_at:
                    # Still backing off. A new message is not a reason to call
                    # a failing model sooner.
                    return Handled(ticket_id, card, new, tuple(applied), hold, usage=spent)

                earlier = _usage(hold.usage) if hold is not None else Usage()
                try:
                    extraction = self.extractor.extract(card, event)
                except Exception as error:  # any failure to read holds the ticket
                    failed = getattr(error, "usage", None) or Usage()
                    spent += failed
                    attempts = hold.attempts if hold is not None and hold.seq == event.seq else 0
                    hold = self.store.hold(
                        ticket_id,
                        event.seq,
                        f"{type(error).__name__}: {error}",
                        now=now,
                        retry_at=now + min(self.retry_base * 2**attempts, self.retry_cap),
                        usage=asdict(earlier + failed),
                    )
                    return Handled(ticket_id, card, new, tuple(applied), hold, usage=spent)

                spent += extraction.total_usage
                self.store.record(
                    event,
                    extraction.proposals,
                    now=now,
                    usage=asdict(earlier + extraction.usage),
                    verifier_usage=asdict(extraction.verifier_usage)
                    if extraction.verifier_usage is not None else None,
                    malformed=[
                        {"section": m.section, "error": m.error} for m in extraction.malformed
                    ],
                )
                applied.append(event.seq)

            waiting = card.last_seq + 1 if self.store.backlog(ticket_id) else None
        return Handled(ticket_id, card, new, tuple(applied), waiting_for=waiting, usage=spent)


def _usage(raw: dict | None) -> Usage:
    return Usage(**raw) if raw else Usage()
