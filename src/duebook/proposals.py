"""What an extractor is allowed to say.

A proposal is a request to change the card, never a change. The extractor — a
language model in production, a hand-written list in tests — emits these, and
``reducer.apply`` decides which ones are admitted.

Every proposal carries a ``quote``: the literal span of the message it rests
on. A proposal whose quote is not in the message is the model talking, not the
ticket, and is refused on that ground alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .card import Party, StepOutcome
from .temporal import DueExpr


@dataclass(frozen=True, slots=True)
class AssertFact:
    slot: str
    value: str
    quote: str
    #: The message says the value CHANGED ("I've upgraded you to Pro"), as
    #: opposed to disputing what the other side said.
    change: bool = False


@dataclass(frozen=True, slots=True)
class MakeCommitment:
    owner: Party
    action: str
    quote: str
    #: The model's structured reading of the deadline, if one was stated.
    due: DueExpr | None = None
    #: The literal deadline phrase ("до пятницы"), for the independent parser.
    due_quote: str = ""
    #: Set when this renegotiates an earlier commitment.
    supersedes: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveCommitment:
    """Someone says the action was done."""

    commitment_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class CancelCommitment:
    commitment_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class RecordStep:
    action: str
    quote: str
    outcome: StepOutcome = StepOutcome.PENDING


@dataclass(frozen=True, slots=True)
class SetStepOutcome:
    step_id: str
    outcome: StepOutcome
    quote: str


@dataclass(frozen=True, slots=True)
class AskQuestion:
    text: str
    quote: str


@dataclass(frozen=True, slots=True)
class AnswerQuestion:
    question_id: str
    quote: str


Proposal = (
    AssertFact
    | MakeCommitment
    | ResolveCommitment
    | CancelCommitment
    | RecordStep
    | SetStepOutcome
    | AskQuestion
    | AnswerQuestion
)
