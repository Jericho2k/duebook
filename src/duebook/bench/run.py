"""Running the event memory over simulated tickets and adding up the score.

    tickets = generate(seed=1, count=50)
    report = evaluate(tickets, Extractor(ChatClient(...)))

The run is in memory — reducer only, no database — because what is measured is
reading, and the store adds nothing to that but time.

The card is read at every checkpoint (each handoff and the end) through
``result.from_card`` and scored by the same code that scores the baselines.

The fold keeps what the extractor proposed for every event, so the same
proposals can be folded again under other rules — the reducer without its
closing guards, say — at no cost and on exactly the same model output. It also
keeps every close the guards refused.

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
from ..events import Event, MessagePosted, other_side, remember_turn
from ..extract.cost import Usage
from ..extract.extractor import (
    BATCH_TIMEOUT,
    MAX_BATCH_SIZE,
    Extraction,
    Reader,
    read_batch,
    read_event,
)
from ..lifecycle import AlertPolicy, next_wakeup
from ..proposals import CancelCommitment, MakeCommitment, Proposal, ResolveCommitment
from ..reducer import GUARDED, Guards, apply
from ..temporal import WorkCalendar
from .generate import CloseCase, SimTicket
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
            f"  fact recall: {every.fact_recall:.3f} "
            f"({every.facts_recalled}/{every.facts_current})",
            f"  fact changes right: {every.change_accuracy:.3f} "
            f"({every.changes_right}/{every.changes_judged})",
            f"  disputes: precision {every.disputes.precision:.3f}  "
            f"recall {every.disputes.recall:.3f}",
            f"  failed steps: precision {every.failed_steps.precision:.3f}  "
            f"recall {every.failed_steps.recall:.3f}",
            f"extraction failures: {len(self.failures)}",
        ]
        for case in CloseCase:
            cases = getattr(s, case.value)
            if cases.tp or cases.fn:
                lines.append(f"{case.value}: {cases.recall:.3f} "
                             f"({cases.tp}/{cases.tp + cases.fn})")  # fmt: skip
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
    #: What the extractor proposed, per event read, in order.
    proposals: tuple[tuple[Proposal, ...], ...] = ()
    #: Every close the guards refused.
    refused: tuple[Refused, ...] = ()
    #: Batch size, wall time, and messages needing individual extraction.
    batches: tuple[tuple[int, float, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Refused:
    """A close the reducer's guards did not admit."""

    #: The message that would have closed it.
    seq: int
    reason: str
    #: "resolved", "cancelled" or "superseded".
    kind: str
    #: The message the target commitment was made in.
    target_seq: int


def fold(
    ticket: SimTicket,
    extractor: Reader,
    stops: Iterable[int] = (),
    cal: WorkCalendar | None = None,
    guards: Guards | None = None,
    *,
    batch: bool = False,
) -> Folded:
    """Read a ticket event by event, keeping the card at each of ``stops``."""
    wanted = set(stops)
    card, spent, seconds = Card(ticket.ticket_id), Usage(), 0.0
    cards: dict[int, Card] = {}
    prompts: list[tuple[int, int]] = []
    read: list[tuple[Proposal, ...]] = []
    refused: list[Refused] = []
    failure = None
    turns, prepared, batches = {}, {}, []
    for index, event in enumerate(ticket.events):
        started = time.perf_counter()
        previous = turns.get(other_side(event))
        try:
            wakeup = next_wakeup(card, event.at) if batch and not prepared else None
            if batch and not prepared and (wakeup is None or wakeup > event.at + BATCH_TIMEOUT):
                joined, upcoming = [], dict(turns)
                for e in ticket.events[index : index + MAX_BATCH_SIZE]:
                    if not isinstance(e, MessagePosted) or not e.text.strip():
                        break
                    joined.append((e, upcoming.get(other_side(e))))
                    remember_turn(upcoming, e)
                prepared = read_batch(extractor, card, joined)
                if prepared:
                    retries = sum(
                        e.seq not in prepared or prepared[e.seq].reread for e, _ in joined
                    )
                    batches.append((len(joined), time.perf_counter() - started, retries))
            extraction = read_event(extractor, card, event, previous,
                                    prepared.pop(event.seq, None))  # fmt: skip
        except Exception as error:  # held at this event, as the pipeline would
            seconds += time.perf_counter() - started
            spent += getattr(error, "usage", None) or Usage()
            failure = (ticket.ticket_id, event.seq, f"{type(error).__name__}: {error}")
            break
        seconds += time.perf_counter() - started
        usage = extraction.total_usage
        spent += usage
        if usage.calls:
            prompts.append((event.seq, usage.prompt_tokens))
        read.append(extraction.proposals)
        outcome = apply(card, event, extraction.proposals, cal, guards=guards, previous=previous)
        remember_turn(turns, event)
        refused += [_refused(card, event.seq, r.proposal, r.reason)
                    for r in outcome.rejections if r.reason in GUARDED]  # fmt: skip
        card = outcome.card
        if event.seq in wanted:
            cards[event.seq] = card
    # A ticket that stopped early is judged on the card it reached.
    for seq in wanted - set(cards):
        cards[seq] = card
    return Folded(card, cards, spent, failure, seconds, tuple(prompts), tuple(read),
                  tuple(refused), tuple(batches))  # fmt: skip


def _refused(card: Card, seq: int, proposal: Proposal, reason) -> Refused:
    match proposal:
        case ResolveCommitment():
            kind, target = "resolved", proposal.commitment_id
        case CancelCommitment():
            kind, target = "cancelled", proposal.commitment_id
        case MakeCommitment():
            kind, target = "superseded", proposal.supersedes
    found = card.commitment(target)
    return Refused(seq, reason.value, kind, found.source.seq if found else 0)


@dataclass(frozen=True, slots=True)
class Replayed:
    """Proposals already made, read again: no model is asked."""

    answers: dict[str, tuple[Proposal, ...]]

    def extract(self, card: Card, event: Event) -> Extraction:
        return Extraction(self.answers.get(event.id, ()))


def refold(
    ticket: SimTicket,
    proposals: Sequence[Sequence[Proposal]],
    guards: Guards | None = None,
    cal: WorkCalendar | None = None,
) -> Folded:
    """Fold a ticket again from the proposals a run made, under other rules."""
    answers = {e.id: tuple(p) for e, p in zip(ticket.events, proposals, strict=False)}
    stops = [cp.seq for cp in checkpoints(ticket)]
    return fold(ticket, Replayed(answers), stops, cal, guards)


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
