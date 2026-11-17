"""Running the event memory over simulated tickets and adding up the score.

    tickets = generate(seed=1, count=50)
    report = evaluate(tickets, Extractor(ChatClient(...)))

The run is in memory — reducer only, no database — because what is measured is
reading, and the store adds nothing to that but time.

The card is read at every checkpoint (each handoff and the end) through
``result.from_card`` and scored by the same code that scores the baselines.

A ticket whose extraction fails is stopped at that event, as in production,
and scored on the card it reached: the promises it never got to count as
missed. Failures are reported alongside the score, not folded into it
silently.
"""

from __future__ import annotations

import time
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
from .result import Snapshot, checkpoints, from_card
from .score import Score, TicketScore, score_ticket


@dataclass(frozen=True, slots=True)
class Report:
    tickets: int
    #: At the end of every ticket.
    score: Score
    usage: Usage = field(default_factory=Usage)
    #: (ticket id, seq, error) for every ticket that stopped early.
    failures: tuple[tuple[str, int, str], ...] = ()
    #: True when the run was cut short by the spending limit.
    stopped_on_budget: bool = False
    #: At every handoff, added up.
    handoffs: Score = field(default_factory=Score)

    def summary(self) -> str:
        s, every = self.score, self.score + self.handoffs
        lines = [
            f"tickets: {self.tickets}",
            "at the end of each ticket:",
            f"  commitments: precision {s.commitments.precision:.3f}  "
            f"recall {s.commitments.recall:.3f}  F1 {s.commitments.f1:.3f}  "
            f"(tp {s.commitments.tp}, fp {s.commitments.fp}, fn {s.commitments.fn})",
            f"  deadline accuracy: {s.deadline_accuracy:.3f} "
            f"({s.deadlines_right}/{s.deadlines_judged})",
            f"  status accuracy: {s.status_accuracy:.3f} ({s.statuses_right}/{s.statuses_judged})",
            f"  breach alerts: precision {s.alerts.precision:.3f}  "
            f"recall {s.alerts.recall:.3f}  (tp {s.alerts.tp}, fp {s.alerts.fp}, "
            f"fn {s.alerts.fn})",
            f"at every handoff and the end ({every.checkpoints} checkpoints):",
            f"  freshness error: {every.freshness_error:.3f} (stale {every.facts_stale}, "
            f"contested {every.facts_contested} of {every.facts_judged} facts judged)",
            f"  fact changes right: {every.change_accuracy:.3f} "
            f"({every.changes_right}/{every.changes_judged})",
            f"  disputes: precision {every.disputes.precision:.3f}  "
            f"recall {every.disputes.recall:.3f}",
            f"  failed steps: precision {every.failed_steps.precision:.3f}  "
            f"recall {every.failed_steps.recall:.3f}",
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


@dataclass(frozen=True, slots=True)
class Folded:
    """A ticket read through the event memory."""

    card: Card
    #: The card as it stood after each of the requested seqs.
    cards: dict[int, Card]
    usage: Usage
    #: (ticket id, seq, error) when extraction failed and the ticket stopped.
    failure: tuple[str, int, str] | None = None
    #: Wall-clock seconds spent in the extractor, and per message.
    seconds: float = 0.0
    #: (seq, prompt tokens) for every model call, to see how the prompt grows.
    prompts: tuple[tuple[int, int], ...] = ()


def fold(
    ticket: SimTicket,
    extractor: Reader,
    stops: Iterable[int] = (),
    cal: WorkCalendar | None = None,
) -> Folded:
    """Read a ticket event by event, keeping the card at each of ``stops``."""
    wanted = set(stops)
    card, spent, seconds = Card(ticket.ticket_id), Usage(), 0.0
    cards: dict[int, Card] = {}
    prompts: list[tuple[int, int]] = []
    failure = None
    for event in ticket.events:
        started = time.perf_counter()
        try:
            extraction = extractor.extract(card, event)
        except Exception as error:  # held at this event, as the pipeline would
            seconds += time.perf_counter() - started
            spent += getattr(error, "usage", None) or Usage()
            failure = (ticket.ticket_id, event.seq, f"{type(error).__name__}: {error}")
            break
        seconds += time.perf_counter() - started
        spent += extraction.usage
        if extraction.usage.calls:
            prompts.append((event.seq, extraction.usage.prompt_tokens))
        card = apply(card, event, extraction.proposals, cal).card
        if event.seq in wanted:
            cards[event.seq] = card
    # A ticket that stopped early is judged on the card it reached.
    for seq in wanted - set(cards):
        cards[seq] = card
    return Folded(card, cards, spent, failure, seconds, tuple(prompts))


def run_ticket(
    ticket: SimTicket, extractor: Reader, cal: WorkCalendar | None = None
) -> tuple[Card, Usage, tuple[str, int, str] | None]:
    """Fold one ticket. Returns the card reached, the cost, and the failure if any."""
    folded = fold(ticket, extractor, (), cal)
    return folded.card, folded.usage, folded.failure


def snapshots(
    ticket: SimTicket, folded: Folded, policy: AlertPolicy | None = None
) -> tuple[Snapshot, ...]:
    """The event memory's answer at every checkpoint of the ticket."""
    error = folded.failure[2] if folded.failure else None
    return tuple(
        from_card(
            folded.cards[cp.seq],
            cp,
            policy,
            error if folded.failure and folded.failure[1] <= cp.seq else None,
        )
        for cp in checkpoints(ticket)
    )


def evaluate(
    tickets: Sequence[SimTicket],
    extractor: Reader,
    *,
    cal: WorkCalendar | None = None,
    policy: AlertPolicy | None = None,
    max_usd: float | None = None,
) -> Report:
    """Score an extractor. ``max_usd`` stops the run once that much was charged."""
    end, handoffs, spent, failures, done = Score(), Score(), Usage(), [], 0
    for ticket in tickets:
        if max_usd is not None and (spent.usd() or 0.0) >= max_usd:
            return Report(done, end, spent, tuple(failures), True, handoffs)
        folded = fold(ticket, extractor, [cp.seq for cp in checkpoints(ticket)], cal)
        scored: TicketScore = score_ticket(ticket, snapshots(ticket, folded, policy))
        end, handoffs = end + scored.end, handoffs + scored.handoffs
        spent += folded.usage
        done += 1
        if folded.failure:
            failures.append(folded.failure)
    return Report(done, end, spent, tuple(failures), handoffs=handoffs)
