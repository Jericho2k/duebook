"""Scoring a card against a ticket's gold labels.

Three things are measured, all of them about promises, because promises are
what the system exists to keep track of.

* **Commitments** — precision, recall and F1 of the promises on the card
  against the planted ones. A promise is identified by the message it was made
  in and the side that owes it; the wording of the action is free text and is
  not compared.
* **Deadlines** — of the promises found, the share whose deadline is the
  planted instant exactly (or correctly absent). A deadline an hour off is
  wrong: an alert would fire at the wrong time.
* **Breach alerts** — precision and recall of the "promise broken" alerts the
  card produces at the end of the ticket, against the promises planted as
  broken. This is the number an operator feels, and precision is the half of
  it that matters most.

Counts add up across tickets, so a benchmark result is a micro-average:
every promise weighs the same wherever it occurred.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..card import Card, Commitment
from ..lifecycle import AlertPolicy, NoticeKind, notices
from .generate import Fate, GoldCommitment, SimTicket


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
    #: Found promises whose deadline is exactly right, and how many were found.
    deadlines_right: int = 0
    deadlines_judged: int = 0
    alerts: Counts = field(default_factory=Counts)

    def __add__(self, other: Score) -> Score:
        return Score(
            self.commitments + other.commitments,
            self.deadlines_right + other.deadlines_right,
            self.deadlines_judged + other.deadlines_judged,
            self.alerts + other.alerts,
        )

    @property
    def deadline_accuracy(self) -> float:
        return self.deadlines_right / self.deadlines_judged if self.deadlines_judged else 1.0


def score(ticket: SimTicket, card: Card, policy: AlertPolicy | None = None) -> Score:
    pairs, spurious, missed = match(ticket.gold.commitments, card.commitments)

    right = sum(1 for gold, found in pairs if _due_at(found) == gold.due_at)

    alerted = {
        n.commitment_id
        for n in notices(card, ticket.end, policy)
        if n.kind is NoticeKind.BREACHED
    }  # fmt: skip
    matched = {found.id: gold for gold, found in pairs}
    true_alerts = sum(1 for i in alerted if i in matched and matched[i].fate is Fate.BROKEN)
    broken = sum(1 for gold in ticket.gold.commitments if gold.fate is Fate.BROKEN)

    return Score(
        commitments=Counts(len(pairs), len(spurious), len(missed)),
        deadlines_right=right,
        deadlines_judged=len(pairs),
        alerts=Counts(true_alerts, len(alerted) - true_alerts, broken - true_alerts),
    )


def match(
    gold: tuple[GoldCommitment, ...], found: tuple[Commitment, ...]
) -> tuple[list[tuple[GoldCommitment, Commitment]], list[Commitment], list[GoldCommitment]]:
    """Pair planted promises with found ones: same message, same side, one to one.

    When one message holds several promises of one side, a found promise with
    the right deadline is preferred, so that a correct reading is not paired
    with the wrong label and counted as an error.
    """
    remaining = list(found)
    pairs, missed = [], []
    for g in gold:
        candidates = [
            c for c in remaining if c.source.seq == g.seq and c.owner.side is g.owner.side
        ]
        if not candidates:
            missed.append(g)
            continue
        best = next((c for c in candidates if _due_at(c) == g.due_at), candidates[0])
        remaining.remove(best)
        pairs.append((g, best))
    return pairs, remaining, missed


def _due_at(c: Commitment):
    return c.due.deadline.due_at if c.due else None
