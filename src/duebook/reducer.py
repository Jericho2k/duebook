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
    Conditional,
    ConditionalState,
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
    names_dropped,
    names_other,
    negated,
    overlap,
    quote_identifies,
    restates,
    shares_name,
    vouches,
)
from .events import (
    AssigneeChanged,
    Event,
    MessagePosted,
    PromiseCheck,
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
    MakeConditional,
    Proposal,
    RecordStep,
    ResolveCommitment,
    SetStepOutcome,
    SettleConditional,
    Unreadable,
    Verdict,
    Verification,
)
from .slots import DEFAULT as DEFAULT_VOCABULARY
from .slots import Vocabulary
from .temporal import DueExpr, WorkCalendar, parse_due, resolve


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
    #: A lapse in words that agree with the condition ("так и не пришла").
    SAYS_CONDITION_HOLDS = "says_condition_holds"


PARTIAL = frozenset({Reason.DEADLINE_UNGROUNDED, Reason.DEADLINE_INVALID, Reason.DEADLINE_IN_PAST})
#: Refusals of a close that leave a suggestion behind.
GUARDED = frozenset({Reason.TARGET_MISMATCH, Reason.NOT_ABOUT_TARGET, Reason.AMBIGUOUS_TARGET,
                     Reason.VERIFIER_REJECTED, Reason.CONTEXT_NAMES_OTHER,
                     Reason.SAYS_CONDITION_HOLDS})  # fmt: skip


#: The closing rules new events are decided under. Each event stores its version
#: (``event_proposals.close_rules``) and is replayed under it (decision 0001):
#: 2 a verifier "no" vetoes a close and every close needs evidence; 3 a contextual
#: close may not name another thing; 4 promise identity (decision 0003);
#: 5 identifier slots hold a set (decision 0005); 6 an unconfirmed claim is checked, and a
#: confirmation aimed at something that is not an open promise is suggested (decision 0006).
RULES = 8


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
    #: The version of the closing rules, 1 to ``RULES``.
    rules: int = RULES

    @property
    def legacy(self) -> bool:
        return self.rules < 2

    @property
    def exact_context(self) -> bool:
        return self.rules >= 3

    @property
    def identity(self) -> bool:
        return self.rules >= 4

    @property
    def identifiers(self) -> bool:
        return self.rules >= 5

    @property
    def claims(self) -> bool:
        return self.rules >= 6


#: The reducer as it was before the guards: who may close, never what.
UNGUARDED = Guards(target=False, relevance=False, ambiguity=False, verifier=False)


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
    guards = guards or Guards()
    if isinstance(event, PromiseCheck) and guards.rules >= 7:
        if event.seq != card.last_seq:
            raise OutOfOrder(f"check {event.id} expects seq {event.seq}, got {card.last_seq}")
        return _apply_check(card, event, proposals, guards)
    if event.seq <= card.last_seq:
        return Outcome(card, replayed=True)
    if event.seq != card.last_seq + 1:
        raise OutOfOrder(f"expected seq {card.last_seq + 1}, got {event.seq}")

    cal = cal or WorkCalendar()
    vocab = vocab or DEFAULT_VOCABULARY
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


def _apply_check(card, event: PromiseCheck, proposals, guards: Guards) -> Outcome:
    rejections = []
    for p in proposals:
        if not isinstance(p, ResolveCommitment) or p.commitment_id != event.commitment_id:
            rejections.append(Rejection(p, Reason.UNKNOWN_TARGET))
            continue
        proof = next((m for m in event.messages if m.id == p.evidence_id), None)
        if proof is None or not proof.public or proof.ticket_id != event.ticket_id or (
            proof.seq > event.seq or proof.at > event.at or not p.quote.strip()
            or p.quote not in proof.text
        ):
            rejections.append(Rejection(p, Reason.UNGROUNDED))
            continue
        target = card.commitment(p.commitment_id)
        if target is None or not target.unresolved or proof.seq <= target.source.seq:
            rejections.append(Rejection(p, Reason.UNKNOWN_TARGET))
            continue
        previous = max((m for m in event.messages if m.seq < proof.seq and m.public
                        and m.author.side is other_side(proof) and m.text.strip()),
                       key=lambda m: m.seq, default=None)
        source = Source(proof.id, proof.seq, proof.author.id, proof.author.side, p.quote)
        updated, reasons = _resolve_commitment(card, proof, p, source, guards, previous)
        if reasons:
            rejections.extend(Rejection(p, reason) for reason in reasons)
        else:
            card = updated
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
            return _assert_fact(card, event, proposal, source, new_id, vocab, guards)
        case MakeCommitment():
            return _make_commitment(card, event, proposal, source, new_id, text, cal, guards)
        case ResolveCommitment():
            return _resolve_commitment(card, event, proposal, source, guards, previous)
        case CancelCommitment():
            return _cancel_commitment(card, event, proposal, source, guards)
        case MakeConditional():
            return _make_conditional(card, event, proposal, source, new_id, text, cal)
        case SettleConditional():
            return _settle_conditional(card, event, proposal, source, new_id, cal, guards)
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
    card: Card, event: MessagePosted, p: AssertFact, source: Source, new_id: str,
    vocab: Vocabulary, guards: Guards,
) -> tuple[Card, list[Reason]]:
    # The proposed name is not trusted to be the name used last time: see slots.
    slot = vocab.normalise(p.slot, {f.slot for f in card.facts})
    value = p.value.strip()
    if not slot or not value:
        return card, [Reason.EMPTY]

    side = event.author.side
    current = [f for f in card.facts if f.current and f.slot == slot]
    agreeing = next((f for f in current if _norm(f.value) == _norm(value)), None)
    identifier = guards.identifiers and slot in vocab.identifiers
    if identifier:
        # Another order is not another value of the first (decision 0005): only
        # the values this message explicitly corrects compete with the new one.
        rivals = (_corrected(event.text, current) if agreeing is None else
                  {i for c in card.open_contradictions() if agreeing.id in c.fact_ids
                   for i in c.fact_ids})  # fmt: skip
        current = [f for f in current if f.id in rivals or f is agreeing]

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


def _corrected(text: str, facts: Sequence[Fact]) -> set[str]:
    """The facts whose value the message negates: "заказ не 538916, а 538961"."""
    said = _norm(text)
    negated = r"(?<!\w)(?:не|not)\s+(?:\S+\s+){0,2}"
    return {f.id for f in facts if re.search(negated + re.escape(_norm(f.value)) + r"(?!\w)", said)}


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
    # Only the disagreements these values were in: an identifier slot can hold others.
    ids = {winner.id, *(f.id for f in losers)}
    return replace(
        card,
        contradictions=tuple(
            replace(c, resolved_at=at)
            if c.slot == slot and c.resolved_at is None and ids & set(c.fact_ids)
            else c
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
    due, reasons = _due(event, p.due, p.due_quote, p.quote, text, cal)
    if any(_same_promise(c, p, due, guards) for c in card.commitments):
        return card, [Reason.DUPLICATE]

    if guards.rules >= 7 and p.restates is not None:
        old = card.commitment(p.restates)
        if (
            old is not None and _reconciled_identity(old, p, due, reasons)
            and not _check_close(old, _rivals(card, old), (p.quote, p.action), p.target_quote,
                                 guards, p.verification, event.text, restates=True)
        ):
            return card, [Reason.DUPLICATE]

    retired, refused = p.supersedes, None
    if p.supersedes is not None:
        old = card.commitment(p.supersedes)
        if old is None or old.owner.side is not p.owner.side:
            return card, [Reason.UNKNOWN_TARGET]
        if not old.unresolved:
            return card, [Reason.ALREADY_RESOLVED]
        refused = _check_close(old, _rivals(card, old), (p.quote, p.action), p.target_quote,
                               guards, p.verification, event.text, restates=True)  # fmt: skip
        if (
            guards.rules >= 7 and p.reconciliation and p.reconciliation.verdict is Verdict.YES
            and (due is None or old.due and due.deadline.due_at == old.due.deadline.due_at)
        ):
            refused = refused or Reason.NOT_ABOUT_TARGET
        if refused is not None:
            # The new promise was made and is kept; the old one is NOT retired
            # on this evidence, only marked as possibly replaced.
            retired = None
            hint = Suggestion(Closure.SUPERSEDED, source, refused.value, replacement=new_id)
            card = _put_commitment(card, _suggest(old, hint))

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


def identity_candidates(card: Card, event: MessagePosted, p: MakeCommitment,
                        cal: WorkCalendar) -> list[tuple[Commitment, str]]:
    if not p.action.strip() or p.owner.side is not event.author.side:
        return []
    if not p.quote.strip() or _norm(p.quote) not in _norm(event.text):
        return []
    due, reasons = _due(event, p.due, p.due_quote, p.quote, _norm(event.text), cal)
    if any(_same_promise(c, p, due, Guards()) for c in card.commitments):
        return []
    candidates = [c for c in card.commitments if c.state is CommitmentState.OPEN
                  and c.owner.side is p.owner.side
                  and shares_name(f"{p.quote} {p.action}", _wording(c))]
    return [(c, "unclear" if reasons else _date_relation(c, due)) for c in candidates]


def _date_relation(c: Commitment, due: Due | None) -> str:
    if due is None:
        return "unstated"
    if c.due is not None and c.due.deadline.due_at == due.deadline.due_at:
        return "same"
    return "different"


def _reconciled_identity(target: Commitment, p: MakeCommitment,
                         due: Due | None, reasons: list[Reason]) -> bool:
    return (
        target.state is CommitmentState.OPEN and target.owner.side is p.owner.side
        and p.reconciliation is not None and p.reconciliation.verdict is Verdict.YES
        and not reasons and (due is None or target.due is not None
                             and due.deadline.due_at == target.due.deadline.due_at)
        and shares_name(f"{p.quote} {p.action}", _wording(target))
        and not names_dropped(f"{p.quote} {p.action}", _wording(target))
    )


def _same_promise(c: Commitment, p: MakeCommitment, due: Due | None, guards: Guards) -> bool:
    """Is ``p`` a restatement of the open commitment ``c``? When in doubt, no.

    Dropping a real promise is silent and permanent; keeping a restatement
    costs one visible extra alert (decision 0003).
    """
    if not c.unresolved or c.owner is not p.owner or c.id == p.supersedes:
        return False
    if not guards.identity:
        return _norm(c.action) == _norm(p.action)
    if c.due is not None and due is not None and c.due.deadline.due_at != due.deadline.due_at:
        return False
    return restates((p.quote, p.action), _wording(c))


def _due(
    event: MessagePosted, expr: DueExpr | None, due_quote: str, said: str, text: str,
    cal: WorkCalendar,
) -> tuple[Due | None, list[Reason]]:
    """Resolve the deadline and say how far it can be trusted.

    Two independent readings of the same phrase — the model's structured
    expression and the rule parser's. Agreement is HIGH confidence, a parser
    that cannot read the phrase leaves MEDIUM, and disagreement is LOW: the
    deadline is kept for the operator to see and withheld from alerting.
    """
    if due_quote and _norm(due_quote) not in text:
        return None, [Reason.DEADLINE_UNGROUNDED]

    parsed = parse_due(due_quote or said)
    model, expr = expr, expr if expr is not None else parsed
    if expr is None:
        return None, []

    try:
        deadline = resolve(expr, event.at, cal)
    except ValueError:
        return None, [Reason.DEADLINE_INVALID]
    if deadline.due_at <= event.at:
        return None, [Reason.DEADLINE_IN_PAST]

    confidence = Confidence.MEDIUM
    if model is not None and parsed is not None:
        try:
            agrees = resolve(parsed, event.at, cal) == deadline
        except ValueError:
            agrees = False
        confidence = Confidence.HIGH if agrees else Confidence.LOW
    return Due(deadline, expr, confidence, due_quote), []


def _resolve_commitment(
    card: Card, event: MessagePosted, p: ResolveCommitment, source: Source, guards: Guards,
    previous: MessagePosted | None,
) -> tuple[Card, list[Reason]]:
    c = card.commitment(p.commitment_id)
    if c is None or not c.unresolved:
        refused = Reason.UNKNOWN_TARGET if c is None else Reason.ALREADY_RESOLVED
        if guards.claims and guards.relevance:  # a suggestion is the guards' evidence
            card = _suggest_fitting(card, event, p, source, guards, refused)
        return card, [refused]
    context = _resolution_context(event, p, c, previous, guards)
    # A short excerpt must not hide a second possible subject in the question.
    said = (p.quote, previous.text) if context and previous else (p.quote,)
    if context and guards.exact_context and names_beyond(context.quote, _wording(c)):
        refused: Reason | None = Reason.CONTEXT_NAMES_OTHER
    else:
        refused = _check_close(c, _rivals(card, c), said, p.target_quote, guards,
                               p.verification, event.text)  # fmt: skip
    if refused is not None:
        hint = Suggestion(Closure.RESOLVED, source, refused.value)
        return _put_commitment(card, _suggest(c, hint)), [refused]

    if guards.rules >= 7 and p.evidence_id and p.partial:
        updated = replace(c, progress=(*c.progress, source)) if source not in c.progress else c
        return _put_commitment(card, updated), []

    debtor = event.author.side is c.owner.side
    delivery = (
        p.verification.delivery_quote if debtor and guards.rules >= 8 and guards.verifier
        and event.public and p.delivered and p.verification
        and p.verification.verdict is Verdict.YES else ""
    )
    delivered = bool(delivery.strip()) and delivery in event.text
    if debtor and not delivered:
        # The debtor's word that it is done is a claim, not a fact.
        if c.state is CommitmentState.CLAIMED:
            return card, [Reason.ALREADY_CLAIMED]
        updated = replace(c, state=CommitmentState.CLAIMED, claimed_at=event.at, resolution=source,
                          claim_checked=guards.claims)  # fmt: skip
    else:
        updated = replace(
            c, state=CommitmentState.FULFILLED, resolved_at=event.at,
            resolution=replace(source, quote=delivery) if delivered else source,
            resolution_context=context, claimed_at=None if delivered else c.claimed_at,
        )
    return _put_commitment(card, updated), []


def _suggest_fitting(
    card: Card, event: MessagePosted, p: ResolveCommitment, source: Source, guards: Guards,
    refused: Reason,
) -> Card:
    """A confirmation aimed at something that is not an open promise is not lost: when its
    words fit exactly one open promise of the other side, it is suggested there (0006)."""
    owing = other_side(event)
    checks = Guards(target=False, verifier=False, rules=guards.rules)
    fitting = [c for c in card.commitments if c.unresolved and c.owner.side is owing
               and _check_close(c, _rivals(card, c), (p.quote,), "", checks) is None]  # fmt: skip
    if len(fitting) != 1:
        return card
    return _put_commitment(card, _suggest(fitting[0], Suggestion(Closure.RESOLVED, source,
                                                                 refused.value)))  # fmt: skip


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
    refused = _check_close(c, _rivals(card, c), (p.quote,), p.target_quote, guards,
                           p.verification, event.text)  # fmt: skip
    if refused is not None:
        hint = Suggestion(Closure.CANCELLED, source, refused.value)
        return _put_commitment(card, _suggest(c, hint)), [refused]
    updated = replace(c, state=CommitmentState.CANCELLED, resolved_at=event.at, resolution=source)
    return _put_commitment(card, updated), []


def _make_conditional(
    card: Card, event: MessagePosted, p: MakeConditional, source: Source, new_id: str, text: str,
    cal: WorkCalendar,
) -> tuple[Card, list[Reason]]:
    if not p.action.strip() or not _norm(p.condition):
        return card, [Reason.EMPTY]
    if _norm(p.condition) not in text:
        return card, [Reason.UNGROUNDED]
    if event.author.side is not p.owner.side:
        return card, [Reason.OWNER_AUTHOR_MISMATCH]
    if any(k.pending and k.owner is p.owner and restates((p.quote, p.action), _wording(k))
           for k in card.conditionals):  # fmt: skip
        return card, [Reason.DUPLICATE]
    due, reasons = _due(event, p.due, p.due_quote, p.condition, text, cal)
    offer = Conditional(new_id, p.owner, p.action.strip(), source, p.condition.strip(),
                        event.at, due)  # fmt: skip
    return replace(card, conditionals=(*card.conditionals, offer)), reasons


def _settle_conditional(
    card: Card, event: MessagePosted, p: SettleConditional, source: Source, new_id: str,
    cal: WorkCalendar, guards: Guards,
) -> tuple[Card, list[Reason]]:
    """Activate an offer, or lapse it, on evidence that names it.

    A wrong lapse is silent, as a wrong close is, so a lapse must pass the same
    checks, may not come from the side that owes the offer, and may not be
    worded as the condition itself is ("так и не пришла" does not say it failed).
    """
    k = next((k for k in card.conditionals if k.id == p.conditional_id), None)
    if k is None:
        return card, [Reason.UNKNOWN_TARGET]
    if not k.pending:
        return card, [Reason.ALREADY_RESOLVED]
    rivals = [r for r in card.conditionals if r.pending and r.id != k.id]
    refused = _check_close(k, rivals, (p.quote,), p.target_quote, guards)
    if refused is None and not p.holds:
        if event.author.side is k.owner.side:
            refused = Reason.DEBTOR_CANNOT_CANCEL
        elif negated(p.quote) is negated(k.condition):
            refused = Reason.SAYS_CONDITION_HOLDS
    kind = Closure.ACTIVATED if p.holds else Closure.LAPSED
    if refused is not None:
        hint = Suggestion(kind, source, refused.value)
        return _put_conditional(card, _suggest(k, hint)), [refused]
    if not p.holds:
        return _put_conditional(card, replace(k, state=ConditionalState.LAPSED, settled=source)), []

    owed = Commitment(
        id=new_id, owner=k.owner, action=k.action, source=k.source, created_at=event.at,
        due=_offered_due(k, event, cal),
        responsible=None if k.owner is Party.CUSTOMER else card.assignee, activated_by=source,
    )  # fmt: skip
    activated = replace(k, state=ConditionalState.ACTIVATED, settled=source, commitment_id=new_id)
    card = replace(card, commitments=(*card.commitments, owed))
    return _put_conditional(card, activated), []


def _offered_due(k: Conditional, event: MessagePosted, cal: WorkCalendar) -> Due | None:
    """The deadline the offer gave its action, outside the condition, counted from activation."""
    expr = parse_due(_norm(k.source.quote).replace(_norm(k.condition), " "))
    if expr is None:
        return None
    try:
        deadline = resolve(expr, event.at, cal)
    except ValueError:
        return None
    return Due(deadline, expr, Confidence.MEDIUM) if deadline.due_at > event.at else None


def _put_conditional(card: Card, updated: Conditional) -> Card:
    return replace(card, conditionals=tuple(updated if k.id == updated.id else k
                                            for k in card.conditionals))  # fmt: skip


def _check_close(
    target: Commitment | Conditional,
    rivals: Sequence[Commitment | Conditional],
    said: Sequence[str],
    target_quote: str,
    guards: Guards,
    verification: Verification | None = None,
    message: str = "",
    restates: bool = False,
) -> Reason | None:
    """Require evidence about this target, and no rival, before suppressing its alerts."""
    if guards.target and not quote_identifies(target_quote, target.source.quote):
        return Reason.TARGET_MISMATCH
    verdict = verification.verdict if verification else None
    if guards.verifier and not guards.legacy and verdict is Verdict.NO:
        return Reason.VERIFIER_REJECTED
    vouched = guards.verifier and guards.legacy and vouches(verification, message)
    if vouched:
        said = (*said, verification.evidence_quote)
    text = " ".join(said)
    fit = overlap(text, _wording(target))
    unrelated = fit == 0 and not (guards.legacy and not rivals and confirms(text))
    if guards.relevance and unrelated and not vouched:
        return Reason.NOT_ABOUT_TARGET
    if guards.relevance and restates and names_other(text, _wording(target)):
        return Reason.NOT_ABOUT_TARGET
    if guards.ambiguity and any(overlap(text, _wording(r)) >= max(fit, 1) for r in rivals):
        return Reason.AMBIGUOUS_TARGET
    return None


def _rivals(card: Card, target: Commitment) -> list[Commitment]:
    """The other open commitments of the target's side: what a close could mean instead."""
    side = target.owner.side
    return [c for c in card.commitments if c.unresolved and c.id != target.id
            and c.owner.side is side]  # fmt: skip


def _wording(c: Commitment | Conditional) -> tuple[str, str]:
    """What a commitment is known by: the words it was made in, and its action."""
    return c.source.quote, c.action


def _suggest(c: Commitment | Conditional, hint: Suggestion) -> Commitment | Conditional:
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
