"""The ticket card: everything the system currently believes about one ticket.

The card is a value. It is never edited in place; ``reducer.apply`` returns a
new one. Nothing is ever deleted from it either — a fact that stopped being true
keeps its validity interval and a pointer to what replaced it, because "what
was true earlier but changed" is half of what a new operator needs to know.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum

from .events import Side
from .temporal import Deadline, DueExpr


@dataclass(frozen=True, slots=True)
class Source:
    """Where a belief came from. Every belief has one; there are no orphans."""

    event_id: str
    seq: int
    author_id: str
    side: Side
    #: The literal span of the message that supports it.
    quote: str


# ---------------------------------------------------------------- facts ----


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    #: What the fact is about, e.g. "order.status". One current value per slot.
    slot: str
    value: str
    source: Source
    valid_from: datetime
    #: None while current. Set when a newer value replaced it.
    valid_to: datetime | None = None
    superseded_by: str | None = None

    @property
    def current(self) -> bool:
        return self.valid_to is None


@dataclass(frozen=True, slots=True)
class Contradiction:
    """The two sides disagree about a slot, and nobody has settled it yet.

    Not the same as a change. When one side updates its own statement the old
    value is superseded. When the customer says one thing and the company
    another, neither is history — the disagreement itself is the finding.
    """

    id: str
    slot: str
    fact_ids: tuple[str, str]
    detected_at: datetime
    resolved_at: datetime | None = None


# ---------------------------------------------------------- commitments ----


class Party(StrEnum):
    """Who owes the action."""

    AGENT = "agent"  # a personal promise: "I will call you back"
    COMPANY = "company"  # "we will refund you"
    CUSTOMER = "customer"  # "I will send the screenshot"

    @property
    def side(self) -> Side:
        return Side.CUSTOMER if self is Party.CUSTOMER else Side.COMPANY


class CommitmentState(StrEnum):
    """What EVENTS have established. Time-dependent status is derived, not stored."""

    OPEN = "open"
    #: The debtor says it is done; the other side has not confirmed.
    CLAIMED = "claimed"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    #: Renegotiated: replaced by a newer commitment.
    SUPERSEDED = "superseded"


class Confidence(IntEnum):
    """How far a resolved deadline can be trusted."""

    #: The model and the rule parser read the same phrase differently.
    LOW = 0
    #: Only the model could read it.
    MEDIUM = 1
    #: Both readings agree.
    HIGH = 2


@dataclass(frozen=True, slots=True)
class Due:
    deadline: Deadline
    expr: DueExpr
    confidence: Confidence
    quote: str = ""


@dataclass(frozen=True, slots=True)
class Commitment:
    id: str
    owner: Party
    action: str
    source: Source
    created_at: datetime
    due: Due | None = None
    state: CommitmentState = CommitmentState.OPEN
    #: The agent currently answerable for a company-side commitment. None means
    #: nobody is: the promise outlived its owner's assignment.
    responsible: str | None = None
    #: Set when responsibility moved with the ticket rather than being taken on.
    inherited_from: str | None = None
    claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: Source | None = None
    superseded_by: str | None = None

    @property
    def unresolved(self) -> bool:
        return self.state in (CommitmentState.OPEN, CommitmentState.CLAIMED)

    @property
    def orphaned(self) -> bool:
        return self.unresolved and self.owner is not Party.CUSTOMER and self.responsible is None


# ------------------------------------------------- steps and questions ----


class StepOutcome(StrEnum):
    PENDING = "pending"
    WORKED = "worked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Step:
    """Something that was tried. A FAILED step is what must not be suggested again."""

    id: str
    action: str
    source: Source
    outcome: StepOutcome = StepOutcome.PENDING
    outcome_source: Source | None = None


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str
    source: Source
    #: The side that owes the answer.
    waiting_on: Side
    answer: Source | None = None

    @property
    def open(self) -> bool:
        return self.answer is None


# ----------------------------------------------------------------- card ----


@dataclass(frozen=True, slots=True)
class Card:
    ticket_id: str
    last_seq: int = 0
    assignee: str | None = None
    status: str = ""
    facts: tuple[Fact, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    commitments: tuple[Commitment, ...] = ()
    steps: tuple[Step, ...] = ()
    questions: tuple[Question, ...] = ()

    def current_facts(self) -> tuple[Fact, ...]:
        return tuple(f for f in self.facts if f.current)

    def changed_facts(self) -> tuple[tuple[Fact, Fact], ...]:
        """(old, new) pairs: what used to be true and what replaced it."""
        by_id = {f.id: f for f in self.facts}
        return tuple(
            (f, by_id[f.superseded_by])
            for f in self.facts
            if f.superseded_by and f.superseded_by in by_id
        )

    def open_contradictions(self) -> tuple[Contradiction, ...]:
        return tuple(c for c in self.contradictions if c.resolved_at is None)

    def commitment(self, commitment_id: str) -> Commitment | None:
        return next((c for c in self.commitments if c.id == commitment_id), None)
