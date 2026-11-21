"""Read stored proposals through the existing fold and alert clock."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime

from ...card import Card, Closure, Party, Suggestion
from ...events import MessagePosted, other_side, remember_turn
from ...lifecycle import AlertPolicy, next_wakeup
from ...proposals import (
    CancelCommitment,
    MakeCommitment,
    MakeConditional,
    Proposal,
    ResolveCommitment,
    SettleConditional,
)
from ...reducer import PARTIAL, Guards, apply
from ...store.codec import decode_proposals
from ...temporal import WorkCalendar
from ..generate import SimTicket
from ..methods import MethodRun
from ..result import from_card
from ..run import Replayed, fold
from .labels import require

CREATIONS = (MakeCommitment, MakeConditional)
RELEVANT = (*CREATIONS, ResolveCommitment, CancelCommitment, SettleConditional)


@dataclass(frozen=True, slots=True)
class Prediction:
    id: str
    action: str
    owner: Party
    actor: str
    seq: int
    state: str
    state_seq: int
    due_at: datetime | None
    suggestions: tuple[Suggestion, ...]
    replacement: str | None = None
    condition: str | None = None


def predictions(card: Card) -> dict[str, Prediction]:
    activated = {offer.commitment_id: offer for offer in card.conditionals if offer.commitment_id}
    result = {}
    for c in card.commitments:
        offer = activated.get(c.id)
        ident = offer.id if offer else c.id
        replacement = card.commitment(c.superseded_by) if c.superseded_by else None
        source = replacement.source if replacement else c.resolution or c.activated_by or c.source
        result[ident] = Prediction(
            ident, c.action, c.owner, c.source.author_id, c.source.seq, c.state.value, source.seq,
            c.due.deadline.due_at if c.due else None,
            (offer.suggestions if offer else ()) + c.suggestions, c.superseded_by,
            offer.condition if offer else None,
        )  # fmt: skip
    for offer in card.conditionals:
        if offer.commitment_id is None:
            result[offer.id] = Prediction(
                offer.id, offer.action, offer.owner, offer.source.author_id, offer.source.seq,
                offer.state.value, (offer.settled or offer.source).seq,
                offer.due.deadline.due_at if offer.due else None, offer.suggestions,
                condition=offer.condition,
            )  # fmt: skip
    return result


def logical_id(card: Card, physical: str | None) -> str | None:
    return next(
        (o.id for o in card.conditionals if o.commitment_id and o.commitment_id == physical),
        physical,
    )


def closure_of(proposal: Proposal) -> tuple[str | None, Closure | None]:
    match proposal:
        case ResolveCommitment(commitment_id=ident):
            return ident, Closure.RESOLVED
        case CancelCommitment(commitment_id=ident):
            return ident, Closure.CANCELLED
        case MakeCommitment(supersedes=ident) if ident is not None:
            return ident, Closure.SUPERSEDED
        case SettleConditional(conditional_id=ident, holds=holds):
            return ident, Closure.ACTIVATED if holds else Closure.LAPSED
    return None, None


@dataclass(frozen=True, slots=True)
class Decision:
    seq: int
    index: int
    proposal: Proposal
    target: str | None
    created: str | None
    admitted: bool
    suggestion_on: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Frame:
    seq: int
    at: datetime
    kind: str
    card: Card
    alert_checkpoint: bool = True


@dataclass(frozen=True, slots=True)
class Trace:
    ticket: SimTicket
    cards: dict[int, Card]
    proposals: tuple[tuple[Proposal, ...], ...]
    failure: str | None
    guards: Guards
    calendar: WorkCalendar

    @classmethod
    def read(
        cls, ticket: SimTicket, run: MethodRun, guards: Guards, calendar: WorkCalendar
    ) -> Trace:
        require(
            run.ticket_id == ticket.ticket_id and "proposals" in run.detail,
            "V2 needs matching saved per-event proposals",
        )
        events = ticket.events
        require(
            bool(events)
            and len({event.id for event in events}) == len(events)
            and all(
                event.seq == seq and event.ticket_id == ticket.ticket_id
                for seq, event in enumerate(events, 1)
            )
            and all(a.at <= b.at for a, b in zip(events, events[1:], strict=False))
            and events[-1].at <= ticket.end,
            "invalid frozen event stream or observation end",
        )
        proposals = tuple(decode_proposals(row) for row in run.detail["proposals"])
        n = len(proposals)
        require(
            n <= len(events) and ((n < len(events)) == bool(run.failure)),
            "proposal prefix does not agree with the saved run's completion state",
        )
        prefix = events[:n]
        answers = {event.id: ps for event, ps in zip(prefix, proposals, strict=True)}
        folded = fold(replace(ticket, events=prefix), Replayed(answers), [e.seq for e in prefix],
                      calendar, guards)  # fmt: skip
        cards = {0: Card(ticket.ticket_id), **folded.cards}
        cards.update({event.seq: folded.card for event in events[n:]})
        for saved in run.snapshots:
            cp = saved.checkpoint
            require(
                1 <= cp.seq <= len(events) and events[cp.seq - 1].at <= cp.at <= ticket.end,
                "saved snapshot is outside the frozen transcript",
            )
            require(
                from_card(cards[cp.seq], cp, error=saved.error) == saved,
                f"replay differs at {ticket.ticket_id} event {cp.seq}; check rules and calendar",
            )
        return cls(ticket, cards, proposals, run.failure, guards, calendar)

    def frames(self, clock: set[datetime], policy: AlertPolicy) -> Iterator[Frame]:
        events = self.ticket.events
        for i, event in enumerate(events):
            card = self.cards[event.seq]
            next_event = events[i + 1] if i + 1 < len(events) else None
            limit = next_event.at if next_event else self.ticket.end
            yield Frame(event.seq, event.at, "message" if isinstance(event, MessagePosted) else
                        "event", card, not next_event or next_event.at > event.at)  # fmt: skip
            points = {at for at in clock if event.at < at < limit}
            wake = next_wakeup(card, event.at, policy)
            while wake is not None and wake < limit:
                points.add(wake)
                wake = next_wakeup(card, wake, policy)
            for at in sorted(points):
                yield Frame(event.seq, at, "clock", card)
        yield Frame(events[-1].seq, self.ticket.end, "end", self.cards[events[-1].seq])

    def decisions(self) -> Iterator[Decision]:
        turns = {}
        for event, proposals in zip(self.ticket.events, self.proposals, strict=False):
            previous = turns.get(other_side(event))
            remember_turn(turns, event)
            if not any(isinstance(p, RELEVANT) for p in proposals):
                continue
            before, after = self.cards[event.seq - 1], self.cards[event.seq]
            outcome = apply(before, event, proposals, self.calendar, guards=self.guards,
                            previous=previous)  # fmt: skip
            current = predictions(after)
            for index, p in enumerate(proposals):
                if not isinstance(p, RELEVANT):
                    continue
                ident = f"{event.id}#{index}"
                target, closure = closure_of(p)
                target = logical_id(after, target)
                reasons = tuple(r.reason.value for r in outcome.rejections if r.proposal is p)
                # For renegotiation, admission means retiring the named old obligation.
                admitted = all(r.partial for r in outcome.rejections if r.proposal is p)
                if closure is not None:
                    admitted = admitted and all(reason in PARTIAL for reason in reasons)
                hints = tuple(
                    k
                    for k, pred in current.items()
                    if any(
                        s.kind == closure
                        and s.source.seq == event.seq
                        and s.source.quote == p.quote
                        and s.reason in reasons
                        and (s.replacement is None or s.replacement == ident)
                        for s in pred.suggestions
                    )
                )
                created = ident if isinstance(p, CREATIONS) else None
                yield Decision(event.seq, index, p, target, created, admitted, hints, reasons)
