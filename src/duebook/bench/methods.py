"""The methods under comparison, behind one interface.

    run = method.run(ticket)    # a Snapshot at every checkpoint, and what it cost

Every method sees the ticket's events in order and is asked, at each handoff
and at the end, what it holds. How it holds it is the method: the event
memory folds proposals into a card; the baselines (``bench.baselines``) keep
the transcript, a summary, or Mem0's store, and ask the model.

All of them read with the same model, so a difference in the score is a
difference in the memory, not in the reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..card import Card
from ..events import Event, MessagePosted
from ..extract.cost import Usage
from ..extract.extractor import Extraction, Extractor, JsonModel
from ..extract.prompt import Bound, build_messages
from ..lifecycle import AlertPolicy
from ..temporal import WorkCalendar
from .generate import SimTicket
from .meter import Ledger, Meter, Tally
from .result import Snapshot, checkpoints
from .run import fold, snapshots


@dataclass(frozen=True, slots=True)
class MethodRun:
    method: str
    ticket_id: str
    snapshots: tuple[Snapshot, ...]
    usage: Usage
    #: Model time while messages arrived, and while answering checkpoints.
    ingest_seconds: float = 0.0
    answer_seconds: float = 0.0
    #: Tokens and dollars split the same way.
    ingest_usage: Usage = field(default_factory=Usage)
    answer_usage: Usage = field(default_factory=Usage)
    #: Why the method stopped reading the ticket, if it did.
    failure: str | None = None
    #: Method-specific detail kept for the record (prompt sizes, raw answers).
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        method: str,
        ticket: SimTicket,
        snaps: tuple[Snapshot, ...],
        tally: Tally,
        failure: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> MethodRun:
        return cls(
            method, ticket.ticket_id, snaps, tally.usage, tally.ingest_seconds,
            tally.answer_seconds, tally.ingest, tally.answer, failure, detail or {},
        )  # fmt: skip


class Method(Protocol):
    name: str

    def run(self, ticket: SimTicket) -> MethodRun: ...


@dataclass(slots=True)
class EventMemory:
    """The proposed method: the extractor proposes per message, the reducer admits.

    Its answer at a checkpoint costs no model call: the card is already there
    and is read deterministically.
    """

    model: JsonModel
    ledger: Ledger
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    policy: AlertPolicy = field(default_factory=AlertPolicy)
    #: What the model is shown of a long card; None shows all of it.
    bound: Bound | None = None
    name: str = "event_memory"

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger)
        extractor = _Sizing(Extractor(meter, self.cal, bound=self.bound))
        folded = fold(ticket, extractor, [cp.seq for cp in checkpoints(ticket)], self.cal)
        snaps = snapshots(ticket, folded, self.policy)
        failure = folded.failure[2] if folded.failure else None
        detail = {
            # (seq, prompt tokens the provider counted) for every model call.
            "prompt_tokens": [list(p) for p in folded.prompts],
            # (seq, characters of the prompt shown in full, and bounded) for
            # every message: the same card rendered both ways, so the effect
            # of the bound is measured on identical input.
            "prompt_chars": extractor.sizes,
        }
        return MethodRun.of(self.name, ticket, snaps, meter.tally, failure, detail)


@dataclass(slots=True)
class _Sizing:
    """Notes how long the prompt would be with and without a bound, then extracts."""

    extractor: Extractor
    sizes: list[list[int]] = field(default_factory=list)

    def extract(self, card: Card, event: Event) -> Extraction:
        if isinstance(event, MessagePosted) and event.text.strip():
            x = self.extractor
            full, _ = build_messages(card, event, x.cal, x.vocab, None)
            bounded, _ = build_messages(card, event, x.cal, x.vocab, Bound())
            size = sum(len(m["content"]) for m in full), sum(len(m["content"]) for m in bounded)
            self.sizes.append([event.seq, *size])
        return self.extractor.extract(card, event)
