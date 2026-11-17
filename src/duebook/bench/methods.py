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

from ..extract.cost import Usage
from ..extract.extractor import Extractor, JsonModel
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
    #: Passed to the extractor; see ``extract.prompt`` for what it bounds.
    extractor_options: dict[str, Any] = field(default_factory=dict)
    name: str = "event_memory"

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger)
        extractor = Extractor(meter, self.cal, **self.extractor_options)
        folded = fold(ticket, extractor, [cp.seq for cp in checkpoints(ticket)], self.cal)
        snaps = snapshots(ticket, folded, self.policy)
        failure = folded.failure[2] if folded.failure else None
        detail = {"prompt_tokens": [list(p) for p in folded.prompts]}
        return MethodRun.of(self.name, ticket, snaps, meter.tally, failure, detail)
