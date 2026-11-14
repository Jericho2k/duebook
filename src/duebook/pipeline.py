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
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .card import Card
from .events import Event
from .extract.cost import Usage
from .extract.extractor import Extraction
from .store.store import Hold, Store


class Reader(Protocol):
    """Anything that turns an event into proposals. Raises when it cannot."""

    def extract(self, card: Card, event: Event) -> Extraction: ...


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

    def retry_held(self, limit: int = 100) -> list[Handled]:
        """Try again every held ticket whose retry time has come."""
        return [
            self._advance(hold.ticket_id, new=False)
            for hold in self.store.holds_due(self.clock(), limit)
        ]

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

                spent += extraction.usage
                self.store.record(
                    event,
                    extraction.proposals,
                    now=now,
                    usage=asdict(earlier + extraction.usage),
                    malformed=[
                        {"section": m.section, "error": m.error} for m in extraction.malformed
                    ],
                )
                applied.append(event.seq)

            waiting = card.last_seq + 1 if self.store.backlog(ticket_id) else None
        return Handled(ticket_id, card, new, tuple(applied), waiting_for=waiting, usage=spent)


def _usage(raw: dict | None) -> Usage:
    return Usage(**raw) if raw else Usage()
