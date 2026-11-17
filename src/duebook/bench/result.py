"""One answer format for every method, so one scorer judges them all.

A method under test — the event memory, a model reading the whole transcript,
a rolling summary, Mem0 — is asked the same question at the same moments: at
every handoff, and at the end of the ticket, what does it hold? Its answer is
a ``Snapshot``:

* promises: who owes it, the message it was made in, when it is due, and
  whether it is done;
* the facts it presents as current;
* the steps it knows failed;
* the disagreements it knows are open.

The event memory produces a snapshot from its card (``from_card``); the
baselines produce one from a model's JSON. Nothing in the scorer knows which
method it is looking at.

Every item carries the seq of the message it rests on. A promise is
identified by that message and the side that owes it — the wording of the
action is free text and is never compared, so a method is not rewarded or
punished for paraphrasing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..card import Card, CommitmentState, StepOutcome
from ..events import AssigneeChanged, Side
from ..lifecycle import AlertPolicy
from .generate import SimTicket


class PromiseStatus(StrEnum):
    OPEN = "open"
    #: The debtor says it is done; the other side has not confirmed.
    CLAIMED = "claimed"
    #: Confirmed done by the other side.
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    #: Replaced by a renegotiated promise.
    SUPERSEDED = "superseded"

    @property
    def done(self) -> bool:
        return self in (PromiseStatus.CLAIMED, PromiseStatus.FULFILLED)


@dataclass(frozen=True, slots=True)
class Promise:
    #: The side that owes it: CUSTOMER or COMPANY (an agent's personal promise
    #: is the company's).
    side: Side
    #: The message the promise was made in.
    seq: int
    action: str
    due_at: datetime | None
    status: PromiseStatus = PromiseStatus.OPEN
    #: False when the method itself would not raise an alert on this deadline
    #: (the event memory withholds alerts on low-confidence deadlines).
    alertable: bool = True


@dataclass(frozen=True, slots=True)
class FactItem:
    name: str
    value: str
    seq: int | None = None

    @property
    def text(self) -> str:
        return f"{self.name} {self.value}"


@dataclass(frozen=True, slots=True)
class FailedStep:
    action: str
    #: Where the step was suggested, and where it was reported to have failed.
    seq: int | None = None
    failed_seq: int | None = None


@dataclass(frozen=True, slots=True)
class Dispute:
    topic: str
    #: What each side says, as the method states it.
    values: tuple[str, ...]
    seqs: tuple[int, ...] = ()

    @property
    def text(self) -> str:
        return " ".join((self.topic, *self.values))


class Moment(StrEnum):
    HANDOFF = "handoff"
    END = "end"


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A moment a method is asked what it holds: after event ``seq``, judged at ``at``."""

    seq: int
    at: datetime
    kind: Moment


@dataclass(frozen=True, slots=True)
class Snapshot:
    checkpoint: Checkpoint
    promises: tuple[Promise, ...] = ()
    facts: tuple[FactItem, ...] = ()
    failed_steps: tuple[FailedStep, ...] = ()
    disputes: tuple[Dispute, ...] = ()
    #: Set when the method could not answer at this checkpoint, or answered
    #: from a ticket it stopped reading early. Scored as it stands, reported.
    error: str | None = None


def checkpoints(ticket: SimTicket) -> tuple[Checkpoint, ...]:
    """Every handoff between operators, then the end of the ticket."""
    handoffs = [
        Checkpoint(e.seq, e.at, Moment.HANDOFF)
        for e in ticket.events
        if isinstance(e, AssigneeChanged) and e.previous is not None
    ]
    return (*handoffs, Checkpoint(ticket.events[-1].seq, ticket.end, Moment.END))


_STATUS = {
    CommitmentState.OPEN: PromiseStatus.OPEN,
    CommitmentState.CLAIMED: PromiseStatus.CLAIMED,
    CommitmentState.FULFILLED: PromiseStatus.FULFILLED,
    CommitmentState.CANCELLED: PromiseStatus.CANCELLED,
    CommitmentState.SUPERSEDED: PromiseStatus.SUPERSEDED,
}


def from_card(
    card: Card, checkpoint: Checkpoint, policy: AlertPolicy | None = None, error: str | None = None
) -> Snapshot:
    """What the event memory holds: the card, read the way the handoff brief reads it.

    A disputed slot is shown once, as a dispute, and not among current facts —
    the brief does the same, because a value one side contests is not a fact
    yet.
    """
    policy = policy or AlertPolicy()
    promises = tuple(
        Promise(
            side=c.owner.side,
            seq=c.source.seq,
            action=c.action,
            due_at=c.due.deadline.due_at if c.due else None,
            status=_STATUS[c.state],
            alertable=c.due is not None and c.due.confidence >= policy.min_confidence,
        )
        for c in card.commitments
    )
    facts_by_id = {f.id: f for f in card.facts}
    open_disputes = card.open_contradictions()
    disputed = {c.slot for c in open_disputes}
    facts = tuple(
        FactItem(f.slot, f.value, f.source.seq)
        for f in card.current_facts()
        if f.slot not in disputed
    )
    failed = tuple(
        FailedStep(s.action, s.source.seq, s.outcome_source.seq if s.outcome_source else None)
        for s in card.steps
        if s.outcome is StepOutcome.FAILED
    )
    disputes = []
    for c in open_disputes:
        sides = [facts_by_id[i] for i in c.fact_ids if i in facts_by_id]
        disputes.append(
            Dispute(c.slot, tuple(f.value for f in sides), tuple(f.source.seq for f in sides))
        )
    return Snapshot(checkpoint, promises, facts, failed, tuple(disputes), error)
