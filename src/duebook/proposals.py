"""What an extractor is allowed to say.

A proposal is a request to change the card, never a change. The extractor — a
language model in production, a hand-written list in tests — emits these, and
``reducer.apply`` decides which ones are admitted.

Every proposal carries a ``quote``: the literal span of the message it rests
on. A proposal whose quote is not in the message is the model talking, not the
ticket, and is refused on that ground alone.

A proposal that closes or replaces a commitment names it twice: by id, and by
``target_quote``, a span copied from that commitment's original wording. The
two must agree before anything is closed (see ``closing``). They default to
empty only so that proposals stored before the field existed can be read; an
empty ``target_quote`` closes nothing.

Such a proposal may also carry a ``Verification``: a second model's verdict,
asked between extraction and the reducer, on whether the new message really
closes (cancels, replaces) THAT commitment. It travels with the proposal and
is stored with it, so a replay reproduces the card without asking anyone. It
defaults to None, which is what every proposal stored before it existed reads
as: no second opinion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .card import Party, StepOutcome
from .temporal import DueExpr


class Verdict(StrEnum):
    YES = "yes"
    NO = "no"
    UNSURE = "unsure"


@dataclass(frozen=True, slots=True)
class Verification:
    """A second model's verdict on one close, with the words it rests on.

    NO can reject a change. To support a contextual close, YES must cite the
    confirming reply and the proposal must identify the preceding turn.
    """

    verdict: Verdict
    #: Copied from the new message: what shows the commitment was closed.
    evidence_quote: str = ""
    #: Which model gave it, for the record.
    model: str = ""
    #: Why there is no real verdict, when the verifier could not be asked.
    error: str = ""


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
    #: A span of the superseded commitment's original wording.
    target_quote: str = ""
    #: The verifier's verdict on retiring the superseded commitment.
    verification: Verification | None = None


@dataclass(frozen=True, slots=True)
class ResolveCommitment:
    """Someone says the action was done."""

    commitment_id: str
    quote: str
    #: A span of the commitment's original wording, naming it a second time.
    target_quote: str = ""
    #: The verifier's verdict on this close, when one was asked.
    verification: Verification | None = None
    #: An identifying span in the preceding public turn from the other side.
    context_quote: str = ""


@dataclass(frozen=True, slots=True)
class CancelCommitment:
    commitment_id: str
    quote: str
    #: A span of the commitment's original wording, naming it a second time.
    target_quote: str = ""
    #: The verifier's verdict on this close, when one was asked.
    verification: Verification | None = None


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
