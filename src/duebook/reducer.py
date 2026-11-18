"""The deterministic layer: an event plus proposals in, a new card out.

    card' = apply(card, event, proposals)

This function is the method. It is pure — no clock, no network, no model — so
the same stream always folds to the same card, a card can be rebuilt as of any
point in the ticket, and every rule below can be tested without a language
model in the loop.

The extractor proposes; this decides. A proposal is admitted only if

* it rests on a literal quote from the message it claims to come from;
* the speaker is entitled to make it — nobody can bind the other side, and the
  debtor cannot verify or cancel their own obligation;
* it is consistent with what the card already holds;
* if it closes or replaces a commitment, it is about THAT commitment: the
  model named it twice and both names agree, the closing words share content
  with the commitment's own — or a verifier, asked about that one close, said
  yes and quoted the message for it — and they fit no other open commitment
  as well.

Closing is where a wrong admission costs most. A commitment closed by mistake
raises no alert and drops out of the brief, so nobody is ever reminded of it;
one left open by mistake still alerts, and a person looks. Closes therefore
fail safe: a close that does not pass is refused, the commitment stays open,
and the evidence is kept on it as a ``Suggestion`` for a person to confirm.

It also decides what a proposal is ABOUT. A fact's slot is normalised against a
controlled vocabulary and the slots already on the card, so that a model
naming one thing two ways does not split it into two facts.

Everything refused is returned with a reason rather than dropped, because a
rejection rate is a measurement: it is how a drifting extractor gets noticed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from .card import (
    Card,
    Closure,
    Commitment,
    CommitmentState,
    Confidence,
    Contradiction,
    Due,
    Fact,
    Party,
    Question,
    Source,
    Step,
    StepOutcome,
    Suggestion,
    Unread,
)
from .closing import (
    NAMING,
    asks_to_confirm,
    confirms,
    confirms_reply,
    names_beyond,
    names_other,
    overlap,
    quote_identifies,
    vouches,
)
from .events import (
    AssigneeChanged,
    Event,
    MessagePosted,
    Role,
    Side,
    StatusChanged,
    other_side,
    remember_turn,
)
from .proposals import (
    AnswerQuestion,
    AskQuestion,
    AssertFact,
    CancelCommitment,
    MakeCommitment,
    Proposal,
    RecordStep,
    ResolveCommitment,
    SetStepOutcome,
    Unreadable,
    Verdict,
    Verification,
)
from .slots import DEFAULT as DEFAULT_VOCABULARY
from .slots import Vocabulary
from .temporal import WorkCalendar, parse_due, resolve


class Reason(StrEnum):
    NOT_A_MESSAGE = "not_a_message"
    UNGROUNDED = "ungrounded"
    EMPTY = "empty"
    DUPLICATE = "duplicate"
    UNKNOWN_TARGET = "unknown_target"
    ALREADY_RESOLVED = "already_resolved"
    ALREADY_CLAIMED = "already_claimed"
    #: A commitment may only be created by the side that would owe it.
    OWNER_AUTHOR_MISMATCH = "owner_author_mismatch"
    #: The debtor cannot release themselves; they can only renegotiate.
    DEBTOR_CANNOT_CANCEL = "debtor_cannot_cancel"
    WRONG_PARTY = "wrong_party"
    # The three below are PARTIAL: the commitment was admitted without a deadline.
    DEADLINE_UNGROUNDED = "deadline_ungrounded"
    DEADLINE_INVALID = "deadline_invalid"
    DEADLINE_IN_PAST = "deadline_in_past"
    # The three below refuse a CLOSE and keep it as a suggestion on the target.
    #: The id and the span of the target's wording name different things.
    TARGET_MISMATCH = "target_mismatch"
    #: The closing words have nothing to do with the target.
    NOT_ABOUT_TARGET = "not_about_target"
    #: The closing words fit another open commitment at least as well.
    AMBIGUOUS_TARGET = "ambiguous_target"
    VERIFIER_REJECTED = "verifier_rejected"
    #: The question a reply confirms names something the target does not:
    #: what was confirmed is probably not on the card.
    CONTEXT_NAMES_OTHER = "context_names_other"


PARTIAL = frozenset({Reason.DEADLINE_UNGROUNDED, Reason.DEADLINE_INVALID, Reason.DEADLINE_IN_PAST})
#: Refusals of a close that leave a suggestion behind.
GUARDED = frozenset({Reason.TARGET_MISMATCH, Reason.NOT_ABOUT_TARGET, Reason.AMBIGUOUS_TARGET,
                     Reason.VERIFIER_REJECTED, Reason.CONTEXT_NAMES_OTHER})  # fmt: skip


@dataclass(frozen=True, slots=True)
class Guards:
    """The checks a close must pass. All on; switched off only to measure what they do."""

    #: The span of the target's wording must be in the target's source quote.
    target: bool = True
    #: The closing words must identify the target.
    relevance: bool = True
    #: No other open commitment of the same side may fit the closing words as well.
    ambiguity: bool = True
    #: Use the stored verdict when deciding the proposed change.
    verifier: bool = True
    #: The question a contextual close relies on may name nothing the target does not.
    exact_context: bool = True
    #: Reproduce decisions recorded before evidence was required for every close.
    legacy: bool = False


#: The reducer as it was before the guards: who may close, never what.
UNGUARDED = Guards(target=False, relevance=False, ambiguity=False, verifier=False)
#: The rules each stored event was decided under (``event_proposals.close_rules``),
#: so a card is rebuilt as it was decided (decision 0001). Absent: the current rules.
CLOSE_RULES = {1: Guards(legacy=True), 2: Guards(exact_context=False)}


@dataclass(frozen=True, slots=True)
class Rejection:
    proposal: Proposal
    reason: Reason

    @property
    def partial(self) -> bool:
        """The proposal was admitted in part: a commitment without its deadline, or a
        renegotiated commitment admitted without retiring the one it named."""
        if self.reason in GUARDED:
            return isinstance(self.proposal, MakeCommitment)
        return self.reason in PARTIAL


@dataclass(frozen=True, slots=True)
class Outcome:
    card: Card
    rejections: tuple[Rejection, ...] = ()
    #: True when the event had already been applied and nothing was done.
    replayed: bool = False


class OutOfOrder(ValueError):
    """An event arrived before its predecessor. Ordering is the ingester's job."""


def apply(
    card: Card,
    event: Event,
    proposals: Sequence[Proposal] = (),
    cal: WorkCalendar | None = None,
    vocab: Vocabulary | None = None,
    guards: Guards | None = None,
    *,
    previous: MessagePosted | None = None,
) -> Outcome:
    if event.ticket_id != card.ticket_id:
        raise ValueError(f"event {event.id} belongs to ticket {event.ticket_id}")
    if event.seq <= card.last_seq:
        return Outcome(card, replayed=True)
    if event.seq != card.last_seq + 1:
        raise OutOfOrder(f"expected seq {card.last_seq + 1}, got {event.seq}")

    cal = cal or WorkCalendar()
    vocab = vocab or DEFAULT_VOCABULARY
    guards = guards or Guards()
    card = replace(card, last_seq=event.seq)
    rejections: list[Rejection] = []

    if isinstance(event, AssigneeChanged):
        card = _hand_over(card, event)
    elif isinstance(event, StatusChanged):
        card = replace(card, status=event.current, status_seq=event.seq)

    if not isinstance(event, MessagePosted):
        rejections += [Rejection(p, Reason.NOT_A_MESSAGE) for p in proposals]
        return Outcome(card, tuple(rejections))

    text = _norm(event.text)
    for index, proposal in enumerate(proposals):
        if not _norm(proposal.quote) or _norm(proposal.quote) not in text:
            rejections.append(Rejection(proposal, Reason.UNGROUNDED))
            continue
        source = Source(event.id, event.seq, event.author.id, event.author.side, proposal.quote)
        new_id = f"{event.id}#{index}"
        card, reasons = _admit(card, event, proposal, source, new_id, text, cal, vocab, guards,
                               previous)  # fmt: skip
        rejections += [Rejection(proposal, reason) for reason in reasons]

    return Outcome(card, tuple(rejections))


def replay(
    ticket_id: str,
    stream: Iterable[tuple[Event, Sequence[Proposal]]],
    cal: WorkCalendar | None = None,
    vocab: Vocabulary | None = None,
    guards: Guards | None = None,
) -> Card:
    """Fold a whole ticket. Stop the stream early to get the card as of that point."""
    card, turns = Card(ticket_id), {}
    for event, proposals in stream:
        card = apply(card, event, proposals, cal, vocab, guards,
                     previous=turns.get(other_side(event))).card  # fmt: skip
        remember_turn(turns, event)
    return card


# ------------------------------------------------------------- handoff ----


def _hand_over(card: Card, event: AssigneeChanged) -> Card:
    """Company-side promises follow the ticket, and say that they did.

    A promise made by the departing assignee does not leave with them: the
    company still owes it. It moves to the new assignee marked as inherited, or
    — when the ticket is left unassigned — becomes orphaned, which is the state
    in which promises are most often lost.
    """

    def move(c: Commitment) -> Commitment:
        if not c.unresolved or c.owner is Party.CUSTOMER:
            return c
        if c.responsible is not None and c.responsible != event.previous:
            return c
        if c.responsible == event.current:
            return c
        return replace(
            c,
            responsible=event.current,
            inherited_from=c.responsible or c.inherited_from,
        )

    return replace(
        card,
        assignee=event.current,
        assignee_seq=event.seq,
        commitments=tuple(move(c) for c in card.commitments),
    )


# ------------------------------------------------------------ admission ----


def _admit(
    card: Card,
    event: MessagePosted,
    proposal: Proposal,
    source: Source,
    new_id: str,
    text: str,
    cal: WorkCalendar,
    vocab: Vocabulary,
    guards: Guards,
    previous: MessagePosted | None,
) -> tuple[Card, list[Reason]]:
    match proposal:
        case AssertFact():
            return _assert_fact(card, event, proposal, source, new_id, vocab)
        case MakeCommitment():
            return _make_commitment(card, event, proposal, source, new_id, text, cal, guards)
        case ResolveCommitment():
            return _resolve_commitment(card, event, proposal, source, guards, previous)
        case CancelCommitment():
            return _cancel_commitment(card, event, proposal, source, guards)
        case RecordStep():
            return _record_step(card, proposal, source, new_id)
        case SetStepOutcome():
            return _set_step_outcome(card, proposal, source)
        case AskQuestion():
            return _ask(card, event, proposal, source, new_id)
        case AnswerQuestion():
            return _answer(card, event, proposal, source)
        case Unreadable():
            unread = Unread(new_id, source, event.at, proposal.error)
            return replace(card, unread=(*card.unread, unread)), []
    raise TypeError(f"not a proposal: {proposal!r}")


def _assert_fact(
    card: Card, event: MessagePosted, p: AssertFact, source: Source, new_id: str, vocab: Vocabulary
) -> tuple[Card, list[Reason]]:
    # The proposed name is not trusted to be the name used last time: see slots.
    slot = vocab.normalise(p.slot, {f.slot for f in card.facts})
    value = p.value.strip()
    if not slot or not value:
        return card, [Reason.EMPTY]

    side = event.author.side
    current = [f for f in card.facts if f.current and f.slot == slot]
    agreeing = next((f for f in current if _norm(f.value) == _norm(value)), None)

    if agreeing is not None:
        # Saying it again adds nothing — unless it is the OTHER side conceding,
        # which is what settles a disagreement.
        losers = [f for f in current if f.id != agreeing.id]
        if agreeing.source.side == side or not losers:
            return card, [Reason.DUPLICATE]
        return _settle(card, slot, winner=agreeing, losers=losers, at=event.at), []

    fact = Fact(id=new_id, slot=slot, value=value, source=source, valid_from=event.at)
    card = replace(card, facts=(*card.facts, fact))

    if side is Side.SYSTEM or p.change:
        # A system of record, or an explicit change, replaces whatever was held.
        return _settle(card, slot, winner=fact, losers=current, at=event.at), []

    own = [f for f in current if f.source.side == side]
    others = [f for f in current if f.source.side != side]
    if own:
        # A side updating its own statement is a change, not a conflict.
        card = _supersede(card, own, by=fact, at=event.at)
        card = _repoint(card, {f.id for f in own}, fact.id)
    elif others:
        conflict = Contradiction(
            id=f"{new_id}!", slot=slot, fact_ids=(others[0].id, fact.id), detected_at=event.at
        )
        card = replace(card, contradictions=(*card.contradictions, conflict))
    return card, []


def _supersede(card: Card, old: Sequence[Fact], by: Fact, at) -> Card:
    ids = {f.id for f in old}
    return replace(
        card,
        facts=tuple(
            replace(f, valid_to=at, superseded_by=by.id) if f.id in ids else f for f in card.facts
        ),
    )


def _settle(card: Card, slot: str, winner: Fact, losers: Sequence[Fact], at) -> Card:
    card = _supersede(card, losers, by=winner, at=at)
    return replace(
        card,
        contradictions=tuple(
            replace(c, resolved_at=at) if c.slot == slot and c.resolved_at is None else c
            for c in card.contradictions
        ),
    )


def _repoint(card: Card, old_ids: set[str], new_id: str) -> Card:
    """An open disagreement follows a side's latest statement, not its first."""
    return replace(
        card,
        contradictions=tuple(
            replace(c, fact_ids=tuple(new_id if i in old_ids else i for i in c.fact_ids))
            if c.resolved_at is None
            else c
            for c in card.contradictions
        ),
    )


def _make_commitment(
    card: Card,
    event: MessagePosted,
    p: MakeCommitment,
    source: Source,
    new_id: str,
    text: str,
    cal: WorkCalendar,
    guards: Guards,
) -> tuple[Card, list[Reason]]:
    if not p.action.strip():
        return card, [Reason.EMPTY]
    if event.author.side is not p.owner.side:
        # "You promised me a refund" is a customer's claim about the company,
        # not a promise by the company.
        return card, [Reason.OWNER_AUTHOR_MISMATCH]
    if any(
        c.unresolved and c.owner is p.owner and _norm(c.action) == _norm(p.action)
        and c.id != p.supersedes
        for c in card.commitments
    ):  # fmt: skip
        return card, [Reason.DUPLICATE]

    retired, refused = p.supersedes, None
    if p.supersedes is not None:
        old = card.commitment(p.supersedes)
        if old is None or old.owner.side is not p.owner.side:
            return card, [Reason.UNKNOWN_TARGET]
        if not old.unresolved:
            return card, [Reason.ALREADY_RESOLVED]
        refused = _check_close(card, old, (p.quote, p.action), p.target_quote, guards,
                               p.verification, event.text, restates=True)  # fmt: skip
        if refused is not None:
            # The new promise was made and is kept; the old one is NOT retired
            # on this evidence, only marked as possibly replaced.
            retired = None
            hint = Suggestion(Closure.SUPERSEDED, source, refused.value, replacement=new_id)
            card = _put_commitment(card, _suggest(old, hint))

    due, reasons = _due(event, p, text, cal)
    if refused is not None:
        reasons = [refused, *reasons]

    if p.owner is Party.CUSTOMER:
        responsible = None
    elif event.author.role is Role.AGENT:
        responsible = event.author.id
    else:
        # A bot cannot be answerable. Its promise lands on whoever holds the
        # ticket — and on nobody, visibly, when no one does.
        responsible = card.assignee

    commitment = Commitment(
        id=new_id,
        owner=p.owner,
        action=p.action.strip(),
        source=source,
        created_at=event.at,
        due=due,
        responsible=responsible,
    )
    commitments = tuple(
        replace(c, state=CommitmentState.SUPERSEDED, resolved_at=event.at, superseded_by=new_id)
        if retired is not None and c.id == retired
        else c
        for c in card.commitments
    )
    return replace(card, commitments=(*commitments, commitment)), reasons


def _due(
    event: MessagePosted, p: MakeCommitment, text: str, cal: WorkCalendar
) -> tuple[Due | None, list[Reason]]:
    """Resolve the deadline and say how far it can be trusted.

    Two independent readings of the same phrase — the model's structured
    expression and the rule parser's. Agreement is HIGH confidence, a parser
    that cannot read the phrase leaves MEDIUM, and disagreement is LOW: the
    deadline is kept for the operator to see and withheld from alerting.
    """
    if p.due_quote and _norm(p.due_quote) not in text:
        return None, [Reason.DEADLINE_UNGROUNDED]

    parsed = parse_due(p.due_quote or p.quote)
    expr = p.due if p.due is not None else parsed
    if expr is None:
        return None, []

    try:
        deadline = resolve(expr, event.at, cal)
    except ValueError:
        return None, [Reason.DEADLINE_INVALID]
    if deadline.due_at <= event.at:
        return None, [Reason.DEADLINE_IN_PAST]

    confidence = Confidence.MEDIUM
    if p.due is not None and parsed is not None:
        try:
            agrees = resolve(parsed, event.at, cal) == deadline
        except ValueError:
            agrees = False
        confidence = Confidence.HIGH if agrees else Confidence.LOW
    return Due(deadline, expr, confidence, p.due_quote), []


def _resolve_commitment(
    card: Card, event: MessagePosted, p: ResolveCommitment, source: Source, guards: Guards,
    previous: MessagePosted | None,
) -> tuple[Card, list[Reason]]:
    c = card.commitment(p.commitment_id)
    if c is None:
        return card, [Reason.UNKNOWN_TARGET]
    if not c.unresolved:
        return card, [Reason.ALREADY_RESOLVED]
    context = _resolution_context(event, p, c, previous, guards)
    # A short excerpt must not hide a second possible subject in the question.
    said = (p.quote, previous.text) if context and previous else (p.quote,)
    if context and guards.exact_context and names_beyond(context.quote, _wording(c)):
        refused: Reason | None = Reason.CONTEXT_NAMES_OTHER
    else:
        refused = _check_close(card, c, said, p.target_quote, guards, p.verification,
                               event.text)  # fmt: skip
    if refused is not None:
        hint = Suggestion(Closure.RESOLVED, source, refused.value)
        return _put_commitment(card, _suggest(c, hint)), [refused]

    if event.author.side is c.owner.side:
        # The debtor's word that it is done is a claim, not a fact.
        if c.state is CommitmentState.CLAIMED:
            return card, [Reason.ALREADY_CLAIMED]
        updated = replace(c, state=CommitmentState.CLAIMED, claimed_at=event.at, resolution=source)
    else:
        updated = replace(
            c, state=CommitmentState.FULFILLED, resolved_at=event.at, resolution=source,
            resolution_context=context,
        )
    return _put_commitment(card, updated), []


def _resolution_context(
    event: MessagePosted, proposal: ResolveCommitment, target: Commitment,
    previous: MessagePosted | None, guards: Guards,
) -> Source | None:
    quote = proposal.context_quote
    if (
        not quote or previous is None or not guards.verifier or guards.legacy
        or previous.ticket_id != event.ticket_id or previous.seq >= event.seq
        or not previous.public or previous.author.side is not other_side(event)
        or previous.author.side is not target.owner.side
        or quote not in previous.text or not asks_to_confirm(previous.text)
        or not vouches(proposal.verification, event.text) or not confirms_reply(proposal.quote)
        or overlap(quote, _wording(target)) < NAMING
    ):
        return None
    return Source(previous.id, previous.seq, previous.author.id, previous.author.side, quote)


def _cancel_commitment(
    card: Card, event: MessagePosted, p: CancelCommitment, source: Source, guards: Guards
) -> tuple[Card, list[Reason]]:
    c = card.commitment(p.commitment_id)
    if c is None:
        return card, [Reason.UNKNOWN_TARGET]
    if not c.unresolved:
        return card, [Reason.ALREADY_RESOLVED]
    if event.author.side is c.owner.side:
        return card, [Reason.DEBTOR_CANNOT_CANCEL]
    refused = _check_close(card, c, (p.quote,), p.target_quote, guards, p.verification,
                           event.text)  # fmt: skip
    if refused is not None:
        hint = Suggestion(Closure.CANCELLED, source, refused.value)
        return _put_commitment(card, _suggest(c, hint)), [refused]
    updated = replace(c, state=CommitmentState.CANCELLED, resolved_at=event.at, resolution=source)
    return _put_commitment(card, updated), []


def _check_close(
    card: Card,
    target: Commitment,
    said: Sequence[str],
    target_quote: str,
    guards: Guards,
    verification: Verification | None = None,
    message: str = "",
    restates: bool = False,
) -> Reason | None:
    """Require evidence about this commitment before suppressing its alerts."""
    if guards.target and not quote_identifies(target_quote, target.source.quote):
        return Reason.TARGET_MISMATCH
    verdict = verification.verdict if verification else None
    if guards.verifier and not guards.legacy and verdict is Verdict.NO:
        return Reason.VERIFIER_REJECTED
    vouched = guards.verifier and guards.legacy and vouches(verification, message)
    if vouched:
        said = (*said, verification.evidence_quote)
    text = " ".join(said)
    rivals = [
        c for c in card.commitments
        if c.unresolved and c.id != target.id and c.owner.side is target.owner.side
    ]  # fmt: skip
    fit = overlap(text, _wording(target))
    unrelated = fit == 0 and not (guards.legacy and not rivals and confirms(text))
    if guards.relevance and unrelated and not vouched:
        return Reason.NOT_ABOUT_TARGET
    if guards.relevance and restates and names_other(text, _wording(target)):
        return Reason.NOT_ABOUT_TARGET
    if guards.ambiguity and any(overlap(text, _wording(r)) >= max(fit, 1) for r in rivals):
        return Reason.AMBIGUOUS_TARGET
    return None


def _wording(c: Commitment) -> tuple[str, str]:
    """What a commitment is known by: the words it was made in, and its action."""
    return c.source.quote, c.action


def _suggest(c: Commitment, hint: Suggestion) -> Commitment:
    return replace(c, suggestions=(*c.suggestions, hint))


def _put_commitment(card: Card, updated: Commitment) -> Card:
    return replace(
        card, commitments=tuple(updated if c.id == updated.id else c for c in card.commitments)
    )


def _record_step(
    card: Card, p: RecordStep, source: Source, new_id: str
) -> tuple[Card, list[Reason]]:
    if not p.action.strip():
        return card, [Reason.EMPTY]
    if any(_norm(s.action) == _norm(p.action) for s in card.steps):
        return card, [Reason.DUPLICATE]
    step = Step(
        id=new_id,
        action=p.action.strip(),
        source=source,
        outcome=p.outcome,
        outcome_source=source if p.outcome is not StepOutcome.PENDING else None,
    )
    return replace(card, steps=(*card.steps, step)), []


def _set_step_outcome(card: Card, p: SetStepOutcome, source: Source) -> tuple[Card, list[Reason]]:
    if not any(s.id == p.step_id for s in card.steps):
        return card, [Reason.UNKNOWN_TARGET]
    if p.outcome is StepOutcome.PENDING:
        return card, [Reason.EMPTY]
    steps = tuple(
        replace(s, outcome=p.outcome, outcome_source=source) if s.id == p.step_id else s
        for s in card.steps
    )
    return replace(card, steps=steps), []


def _ask(
    card: Card, event: MessagePosted, p: AskQuestion, source: Source, new_id: str
) -> tuple[Card, list[Reason]]:
    if not p.text.strip():
        return card, [Reason.EMPTY]
    side = event.author.side
    if side is Side.SYSTEM:
        return card, [Reason.WRONG_PARTY]
    if any(q.open and _norm(q.text) == _norm(p.text) for q in card.questions):
        return card, [Reason.DUPLICATE]
    waiting_on = Side.COMPANY if side is Side.CUSTOMER else Side.CUSTOMER
    question = Question(id=new_id, text=p.text.strip(), source=source, waiting_on=waiting_on)
    return replace(card, questions=(*card.questions, question)), []


def _answer(
    card: Card, event: MessagePosted, p: AnswerQuestion, source: Source
) -> tuple[Card, list[Reason]]:
    q = next((q for q in card.questions if q.id == p.question_id), None)
    if q is None:
        return card, [Reason.UNKNOWN_TARGET]
    if not q.open:
        return card, [Reason.ALREADY_RESOLVED]
    if event.author.side is not q.waiting_on:
        return card, [Reason.WRONG_PARTY]
    questions = tuple(replace(x, answer=source) if x.id == q.id else x for x in card.questions)
    return replace(card, questions=questions), []


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip()
