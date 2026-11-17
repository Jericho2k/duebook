"""Scoring a method's snapshot against a ticket's gold labels.

Every method answers in the same format (``result.Snapshot``) at the same
checkpoints — each handoff and the end — and is judged by this code alone.

**Promises** (RQ1)

* *Commitments* — precision, recall and F1 of the promises found against the
  ones planted so far. A promise is identified by the message it was made in
  and the side that owes it; the wording of the action is not compared.
* *Deadlines* — of the promises found, the share whose deadline is the planted
  instant (within a minute, so "23:59" and "23:59:59" agree), or correctly
  absent. An hour off is wrong: an alert would fire at the wrong time.
* *Statuses* — of the promises found, the share whose state agrees with what
  had happened by the checkpoint: still owed, done, or dropped (cancelled or
  replaced by a renegotiated promise). Round 1 judged done / not done only;
  its tickets planted no cancellation or renegotiation, so there the two agree
  except that a promise wrongly called cancelled or replaced now counts as
  wrong, as it should.
* *Breach alerts* — a company promise the method holds as open, with a
  deadline it trusts, that has passed by the checkpoint is an alert. Precision
  and recall against the promises planted as broken; every alert that is not
  one is a false alert.
* *Closes* — of the promises found, which the method holds as closed (done,
  claimed, cancelled or replaced). A *false close* is one closed while the
  label says it is still owed — the costliest error, because a promise closed
  by mistake is never alerted on again. A *wrong-target close* is closed when
  the label is too, but by a different message than the one that closed it:
  the right outcome reached through the wrong evidence. A *missed close* is
  still held open when the label is closed. *Suggestions* are closes the event
  memory held back for a person to confirm; one is right when it names the
  message that really closed the promise.
* *Hard close cases* — after a labelled reply, the method must retain every
  affected promise and show the correct one as fulfilled if the reply
  identifies it. Any still owed must remain open.

**Context** (RQ2) — facts are matched by VALUE, because methods do not agree
on names (see ``match``). For every fact a method presents as current that
carries a planted value, the gold label at that checkpoint says whether the
value is current, stale (it was changed, or lost a dispute) or contested.

* *Freshness error* — the share of those facts that are stale or contested.
  Lower is better.
* *Fact recall* — of the planted values current at the checkpoint (said,
  not changed since, not lost or still in a dispute), the share the method
  presents among its facts.
* *Fact changes* — after a planted change, the new value is presented as
  current and the old one is not.
* *Disputes* — precision and recall of the open disagreements a method reports
  against the planted ones still open; one still reported after it was settled
  is a false one.
* *Failed steps* — precision and recall of the steps a method knows failed,
  against the planted failed fixes. A step is recognised by any message it
  rests on or by a word stem of the step.

Counts add up across checkpoints and tickets, so a result is a micro-average.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from datetime import timedelta

from ..card import Card
from ..events import Side
from ..lifecycle import AlertPolicy
from .generate import GoldCommitment, GoldFact, SimTicket
from .match import mentions, mentions_any
from .result import (
    Checkpoint,
    Dispute,
    FailedStep,
    Moment,
    Promise,
    PromiseStatus,
    Snapshot,
    checkpoints,
    from_card,
)

#: Two deadlines this close are the same deadline.
TOLERANCE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def __add__(self, other: Counts) -> Counts:
        return Counts(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass(frozen=True, slots=True)
class Score:
    commitments: Counts = field(default_factory=Counts)
    #: Found promises whose deadline is right, and how many were found.
    deadlines_right: int = 0
    deadlines_judged: int = 0
    #: Found promises whose done / not done is right.
    statuses_right: int = 0
    statuses_judged: int = 0
    alerts: Counts = field(default_factory=Counts)
    #: Found promises the method closed rightly, closed while still owed,
    #: closed on the wrong message, and left open although closed.
    closes_right: int = 0
    closes_false: int = 0
    closes_wrong_target: int = 0
    closes_missed: int = 0
    #: Held-back closes that name the message that closed the promise, and others.
    suggestions_right: int = 0
    suggestions_wrong: int = 0
    #: Presented facts that carry a planted value, and of those, the stale and
    #: the contested ones. Facts with no planted value are counted, not judged.
    facts_judged: int = 0
    facts_stale: int = 0
    facts_contested: int = 0
    facts_unjudged: int = 0
    changes_right: int = 0
    changes_judged: int = 0
    disputes: Counts = field(default_factory=Counts)
    failed_steps: Counts = field(default_factory=Counts)
    #: Checkpoints scored, and how many of them the method could not answer.
    checkpoints: int = 0
    errors: int = 0
    #: Planted values current at the checkpoint, and how many were presented.
    facts_current: int = 0
    facts_recalled: int = 0
    #: Per-case successes and failures, counted once per eligible checkpoint.
    rescue_close_correct: Counts = field(default_factory=Counts)
    ambiguous_close_refrained: Counts = field(default_factory=Counts)
    wrong_thing_not_closed: Counts = field(default_factory=Counts)

    def __add__(self, other: Score) -> Score:
        return Score(*(getattr(self, f.name) + getattr(other, f.name) for f in fields(self)))

    @property
    def deadline_accuracy(self) -> float:
        return self.deadlines_right / self.deadlines_judged if self.deadlines_judged else 1.0

    @property
    def status_accuracy(self) -> float:
        return self.statuses_right / self.statuses_judged if self.statuses_judged else 1.0

    @property
    def freshness_error(self) -> float:
        """Share of judged current facts that are stale or contested. Lower is better."""
        bad = self.facts_stale + self.facts_contested
        return bad / self.facts_judged if self.facts_judged else 0.0

    @property
    def closes_judged(self) -> int:
        """Found promises whose label says they were closed by the checkpoint."""
        return self.closes_right + self.closes_wrong_target + self.closes_missed

    @property
    def close_precision(self) -> float:
        """Of the closes a method made, the share both due and on the right message."""
        made = self.closes_right + self.closes_false + self.closes_wrong_target
        return self.closes_right / made if made else 1.0

    @property
    def fact_recall(self) -> float:
        return self.facts_recalled / self.facts_current if self.facts_current else 1.0

    @property
    def change_accuracy(self) -> float:
        return self.changes_right / self.changes_judged if self.changes_judged else 1.0

    def as_dict(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = [value.tp, value.fp, value.fn] if isinstance(value, Counts) else value
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> Score:
        return cls(
            **{
                k: Counts(*v) if isinstance(v, list) else v
                for k, v in raw.items()
                if k in {f.name for f in fields(cls)}
            }
        )


@dataclass(frozen=True, slots=True)
class TicketScore:
    """One ticket: the end of it, and all its handoffs added up."""

    end: Score
    handoffs: Score

    @property
    def all(self) -> Score:
        return self.end + self.handoffs


def score_ticket(ticket: SimTicket, snapshots: Sequence[Snapshot]) -> TicketScore:
    end, handoffs = Score(), Score()
    for snap in snapshots:
        s = score_snapshot(ticket, snap)
        if snap.checkpoint.kind is Moment.END:
            end += s
        else:
            handoffs += s
    return TicketScore(end, handoffs)


def score(ticket: SimTicket, card: Card, policy: AlertPolicy | None = None) -> Score:
    """The event memory's card at the end of the ticket, scored."""
    return score_snapshot(ticket, from_card(card, checkpoints(ticket)[-1], policy))


def score_snapshot(ticket: SimTicket, snap: Snapshot) -> Score:
    cp, gold = snap.checkpoint, ticket.gold
    planted = tuple(c for c in gold.commitments if c.seq <= cp.seq)
    pairs, spurious, missed = match(planted, snap.promises)

    deadlines = sum(1 for g, f in pairs if same_deadline(g.due_at, f.due_at))
    statuses = sum(1 for g, f in pairs if _state(f.status) == _gold_state(g, cp))
    closes = _closes(pairs, cp)

    alerted = [f for f in snap.promises if alerted_at(f, cp)]
    matched = {id(f): g for g, f in pairs}
    true_alerts = sum(1 for f in alerted if id(f) in matched and _breached(matched[id(f)], cp))
    breached = sum(1 for g in planted if _breached(g, cp))

    judged, stale, contested, unjudged = _freshness(ticket, snap)
    changes_right, changes_judged = _changes(ticket, snap)
    current, recalled = _recall(ticket, snap)

    return Score(
        commitments=Counts(len(pairs), len(spurious), len(missed)),
        deadlines_right=deadlines,
        deadlines_judged=len(pairs),
        statuses_right=statuses,
        statuses_judged=len(pairs),
        alerts=Counts(true_alerts, len(alerted) - true_alerts, breached - true_alerts),
        **closes,
        **_close_cases(ticket, pairs, cp),
        facts_judged=judged,
        facts_stale=stale,
        facts_contested=contested,
        facts_unjudged=unjudged,
        changes_right=changes_right,
        changes_judged=changes_judged,
        disputes=_disputes(ticket, snap),
        failed_steps=_failed_steps(ticket, snap),
        checkpoints=1,
        errors=int(snap.error is not None),
        facts_current=current,
        facts_recalled=recalled,
    )


# ------------------------------------------------------------- promises ----


def match(
    gold: Sequence[GoldCommitment], found: Sequence[Promise]
) -> tuple[list[tuple[GoldCommitment, Promise]], list[Promise], list[GoldCommitment]]:
    """Pair planted promises with found ones: same message, same side, one to one.

    When one message holds several promises of one side, a found promise with
    the right deadline is preferred, so that a correct reading is not paired
    with the wrong label and counted as an error.
    """
    remaining = list(found)
    pairs, missed = [], []
    for g in gold:
        candidates = [f for f in remaining if f.seq == g.seq and f.side is g.owner.side]
        if not candidates:
            missed.append(g)
            continue
        best = next((f for f in candidates if same_deadline(g.due_at, f.due_at)), candidates[0])
        remaining.remove(best)
        pairs.append((g, best))
    return pairs, remaining, missed


def same_deadline(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOLERANCE


def alerted_at(p: Promise, cp: Checkpoint) -> bool:
    """A company promise held open past a deadline the method trusts."""
    return (
        p.side is Side.COMPANY
        and p.status is PromiseStatus.OPEN
        and p.alertable
        and p.due_at is not None
        and p.due_at <= cp.at
    )


def _state(status: PromiseStatus) -> str:
    if status is PromiseStatus.OPEN:
        return "owed"
    return "done" if status.done else "dropped"


def _gold_state(g: GoldCommitment, cp: Checkpoint) -> str:
    if not g.closed(cp.seq):
        return "owed"
    return "done" if g.done(cp.seq) else "dropped"


def _closes(pairs, cp: Checkpoint) -> dict[str, int]:
    out = dict.fromkeys(("closes_right", "closes_false", "closes_wrong_target", "closes_missed",
                         "suggestions_right", "suggestions_wrong"), 0)  # fmt: skip
    for g, f in pairs:
        closed, due = f.status is not PromiseStatus.OPEN, g.closed(cp.seq)
        if closed and not due:
            out["closes_false"] += 1
        elif closed and f.closed_seq is not None and f.closed_seq != g.resolved_seq:
            out["closes_wrong_target"] += 1
        elif closed:
            out["closes_right"] += 1
        elif due:
            out["closes_missed"] += 1
        for seq in f.suggested:
            right = due and seq == g.resolved_seq
            out["suggestions_right" if right else "suggestions_wrong"] += 1
    return out


def _close_cases(
    ticket: SimTicket, pairs: Sequence[tuple[GoldCommitment, Promise]], cp: Checkpoint
) -> dict[str, Counts]:
    out: dict[str, Counts] = {}
    found = {g.seq: p for g, p in pairs}
    for case in ticket.gold.close_cases:
        if case.seq > cp.seq:
            continue
        right = all(
            seq in found and found[seq].status is PromiseStatus.OPEN
            for seq in case.promise_seqs if seq != case.target_seq
        )  # fmt: skip
        if case.target_seq is not None:
            target = found.get(case.target_seq)
            right = (
                right
                and target is not None
                and target.status is PromiseStatus.FULFILLED
                and target.closed_seq in (None, case.seq)
            )
        key = case.kind.value
        out[key] = out.get(key, Counts()) + Counts(tp=int(right), fn=int(not right))
    return out


def _breached(g: GoldCommitment, cp: Checkpoint) -> bool:
    return (
        g.owner.side is Side.COMPANY
        and g.due_at is not None
        and g.due_at <= cp.at
        and not g.closed(cp.seq)
    )


# ---------------------------------------------------------------- facts ----


def fact_status(ticket: SimTicket, fact: GoldFact, seq: int) -> str | None:
    """What a planted value is after event ``seq``: current, stale or contested.

    None when it had not been said yet.
    """
    if fact.seq > seq:
        return None
    gold = ticket.gold
    if any(c.slot == fact.slot and c.old == fact.value and c.seq <= seq for c in gold.changes):
        return "stale"
    for d in gold.contradictions:
        if d.slot != fact.slot or fact.value not in d.values:
            continue
        if d.open_at(seq):
            return "contested"
        if d.settled_seq is not None and d.settled_seq <= seq and d.winner != fact.value:
            return "stale"
    return "current"


def presented(ticket: SimTicket, text: str, seq: int) -> dict[str, GoldFact]:
    """Per slot, the planted value a piece of text presents.

    When an item mentions several values of one slot ("was Лайт, now
    Премиум"), it is taken to present the latest one said.
    """
    found: dict[str, GoldFact] = {}
    for g in ticket.gold.facts:
        newer = g.slot not in found or g.seq > found[g.slot].seq
        if g.seq <= seq and newer and mentions(text, g.key):
            found[g.slot] = g
    return found


def _freshness(ticket: SimTicket, snap: Snapshot) -> tuple[int, int, int, int]:
    judged = stale = contested = unjudged = 0
    for item in snap.facts:
        values = presented(ticket, item.text, snap.checkpoint.seq)
        if not values:
            unjudged += 1
        for g in values.values():
            status = fact_status(ticket, g, snap.checkpoint.seq)
            judged += 1
            stale += status == "stale"
            contested += status == "contested"
    return judged, stale, contested, unjudged


def _recall(ticket: SimTicket, snap: Snapshot) -> tuple[int, int]:
    """Planted values current at the checkpoint, and how many the snapshot presents."""
    seq = snap.checkpoint.seq
    current = [g for g in ticket.gold.facts if fact_status(ticket, g, seq) == "current"]
    shown = {(g.slot, g.value) for item in snap.facts
             for g in presented(ticket, item.text, seq).values()}  # fmt: skip
    return len(current), sum(1 for g in current if (g.slot, g.value) in shown)


def _changes(ticket: SimTicket, snap: Snapshot) -> tuple[int, int]:
    seq = snap.checkpoint.seq
    shown = [g for item in snap.facts for g in presented(ticket, item.text, seq).values()]
    values = {(g.slot, g.value) for g in shown}
    judged = [c for c in ticket.gold.changes if c.seq <= seq]
    right = sum(
        1 for c in judged if (c.slot, c.new) in values and (c.slot, c.old) not in values
    )  # fmt: skip
    return right, len(judged)


def _key(ticket: SimTicket, slot: str, value: str) -> tuple[str, ...]:
    return next(f.key for f in ticket.gold.facts if f.slot == slot and f.value == value)


def _disputes(ticket: SimTicket, snap: Snapshot) -> Counts:
    seq = snap.checkpoint.seq
    gold = [d for d in ticket.gold.contradictions if d.open_at(seq)]
    remaining: list[Dispute] = list(snap.disputes)
    tp = 0
    for d in gold:
        keys = [_key(ticket, d.slot, v) for v in d.values]
        hit = next((x for x in remaining if all(mentions(x.text, k) for k in keys)), None)
        if hit is not None:
            remaining.remove(hit)
            tp += 1
    return Counts(tp, len(remaining), len(gold) - tp)


def _failed_steps(ticket: SimTicket, snap: Snapshot) -> Counts:
    seq = snap.checkpoint.seq
    gold = [f for f in ticket.gold.repeated_fixes if f.failed_seq <= seq]
    remaining: list[FailedStep] = list(snap.failed_steps)
    tp = 0
    for fix in gold:
        seqs = {fix.suggested_seq, fix.failed_seq, fix.repeated_seq}

        def recognised(step: FailedStep, fix=fix, seqs=seqs) -> bool:
            return bool({step.seq, step.failed_seq} & seqs) or mentions_any(step.action, fix.key)

        hit = next((s for s in remaining if recognised(s)), None)
        if hit is not None:
            remaining.remove(hit)
            tp += 1
    return Counts(tp, len(remaining), len(gold) - tp)
