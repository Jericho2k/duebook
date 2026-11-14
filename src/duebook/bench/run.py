"""Running an extractor over simulated tickets and adding up the score.

    tickets = generate(seed=1, count=50)
    report = evaluate(tickets, Extractor(ChatClient(...)))

The run is in memory — reducer only, no database — because what is measured is
reading, and the store adds nothing to that but time.

A ticket whose extraction fails is stopped at that event, as in production,
and scored on the card it reached: the promises it never got to count as
missed. Failures are reported alongside the score, not folded into it
silently.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..card import Card
from ..events import Event
from ..extract.cost import Usage
from ..extract.extractor import Extraction, Reader
from ..lifecycle import AlertPolicy
from ..reducer import apply
from ..temporal import WorkCalendar
from .generate import SimTicket
from .score import Score, score


@dataclass(frozen=True, slots=True)
class Report:
    tickets: int
    score: Score
    usage: Usage = field(default_factory=Usage)
    #: (ticket id, seq, error) for every ticket that stopped early.
    failures: tuple[tuple[str, int, str], ...] = ()
    #: True when the run was cut short by the spending limit.
    stopped_on_budget: bool = False

    def summary(self) -> str:
        s = self.score
        lines = [
            f"tickets: {self.tickets}",
            f"commitments: precision {s.commitments.precision:.3f}  "
            f"recall {s.commitments.recall:.3f}  F1 {s.commitments.f1:.3f}  "
            f"(tp {s.commitments.tp}, fp {s.commitments.fp}, fn {s.commitments.fn})",
            f"deadline accuracy: {s.deadline_accuracy:.3f} "
            f"({s.deadlines_right}/{s.deadlines_judged})",
            f"breach alerts: precision {s.alerts.precision:.3f}  recall {s.alerts.recall:.3f}  "
            f"(tp {s.alerts.tp}, fp {s.alerts.fp}, fn {s.alerts.fn})",
            f"extraction failures: {len(self.failures)}",
        ]
        cost = self.usage.usd()
        if self.usage.calls:
            charged = f"${cost:.4f}" if cost is not None else "not reported"
            lines.append(f"model calls: {self.usage.calls}, charged: {charged}")
        if self.stopped_on_budget:
            lines.append("STOPPED EARLY: spending limit reached")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Oracle:
    """The perfect extractor: proposes exactly what was planted.

    Not a baseline. It exists to prove the benchmark is self-consistent — the
    planted proposals, pushed through the real reducer, must score 1.0.
    """

    answers: dict[tuple[str, str], tuple]

    @classmethod
    def of(cls, tickets: Iterable[SimTicket]) -> Oracle:
        return cls(
            {
                (t.ticket_id, e.id): p
                for t in tickets
                for e, p in zip(t.events, t.proposals, strict=True)
            }
        )

    def extract(self, card: Card, event: Event) -> Extraction:
        return Extraction(self.answers[event.ticket_id, event.id])


def run_ticket(
    ticket: SimTicket, extractor: Reader, cal: WorkCalendar | None = None
) -> tuple[Card, Usage, tuple[str, int, str] | None]:
    """Fold one ticket. Returns the card reached, the cost, and the failure if any."""
    card, spent = Card(ticket.ticket_id), Usage()
    for event in ticket.events:
        try:
            extraction = extractor.extract(card, event)
        except Exception as error:  # held at this event, as the pipeline would
            spent += getattr(error, "usage", None) or Usage()
            return card, spent, (ticket.ticket_id, event.seq, f"{type(error).__name__}: {error}")
        spent += extraction.usage
        card = apply(card, event, extraction.proposals, cal).card
    return card, spent, None


def evaluate(
    tickets: Sequence[SimTicket],
    extractor: Reader,
    *,
    cal: WorkCalendar | None = None,
    policy: AlertPolicy | None = None,
    max_usd: float | None = None,
) -> Report:
    """Score an extractor. ``max_usd`` stops the run once that much was charged."""
    total, spent, failures, done = Score(), Usage(), [], 0
    for ticket in tickets:
        if max_usd is not None and (spent.usd() or 0.0) >= max_usd:
            return Report(done, total, spent, tuple(failures), stopped_on_budget=True)
        card, usage, failure = run_ticket(ticket, extractor, cal)
        total += score(ticket, card, policy)
        spent += usage
        done += 1
        if failure:
            failures.append(failure)
    return Report(done, total, spent, tuple(failures))
