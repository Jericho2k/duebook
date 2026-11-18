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

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from ..card import Card
from ..events import Event, MessagePosted
from ..extract.cost import Usage
from ..extract.extractor import Extraction, Extractor, JsonModel, Reader, read_event
from ..extract.prompt import Bound, build_messages
from ..extract.verifier import verifying_reader
from ..lifecycle import AlertPolicy
from ..proposals import Proposal
from ..store.codec import encode
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


@dataclass(frozen=True, slots=True)
class Recorded:
    """What an earlier run of the event memory proposed for one ticket, and what it cost."""

    #: Per event, in order; a proposal keeps the verifier's verdict if it had one.
    proposals: tuple[tuple[Proposal, ...], ...]
    extraction: Usage
    verifier: Usage
    #: Model seconds spent extracting, and verifying.
    seconds: float
    verifier_seconds: float
    providers: dict[str, int]


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
    #: Asked about every proposal that would close a promise, as the product
    #: does (``extract.verifier``); None reads without a second opinion.
    verifier: JsonModel | None = None
    #: Per ticket id, proposals an earlier run made: folded again instead of
    #: asking ``model``. Only proposals without a verdict go to the verifier.
    recorded: dict[str, Recorded] | None = None
    #: Reads a message again, with reasoning, when ``model``'s answer is unreadable.
    escalation: JsonModel | None = None
    #: Per ticket id, the messages read again live while the rest is replayed
    #: from ``recorded``: a targeted re-extraction.
    reread: dict[str, frozenset[int]] | None = None

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger)
        sizes: list[list[int]] = []
        escalation = Meter(self.escalation, self.ledger, meter.tally) if self.escalation else None
        extractor = Extractor(meter, self.cal, bound=self.bound, escalation=escalation)
        reread: list[list] = []
        if self.recorded is None:
            reader = _Sizing(extractor, sizes)
            before = Recorded((), Usage(), Usage(), 0.0, 0.0, {})
        else:
            before = self.recorded[ticket.ticket_id]
            answers = {e.id: p for e, p in zip(ticket.events, before.proposals, strict=False)}
            seqs = (self.reread or {}).get(ticket.ticket_id, frozenset())
            reader = _Spliced(answers, extractor, seqs, reread)
        checker = Meter(self.verifier, self.ledger) if self.verifier is not None else None
        if checker is not None:
            reader = verifying_reader(reader, checker, self.cal)
        folded = fold(ticket, reader, [cp.seq for cp in checkpoints(ticket)], self.cal)
        snaps = snapshots(ticket, folded, self.policy)
        failure = folded.failure[2] if folded.failure else None
        extraction = before.extraction + meter.tally.usage
        verifier = before.verifier + (checker.tally.usage if checker else Usage())
        verifying = before.verifier_seconds + (checker.tally.ingest_seconds if checker else 0.0)
        tally = Tally(extraction + verifier,
                      before.seconds + meter.tally.ingest_seconds + verifying)  # fmt: skip
        tally.cache_hits = meter.tally.cache_hits + (checker.tally.cache_hits if checker else 0)
        for source in (before.providers, meter.tally.providers):
            for host, n in source.items():
                tally.providers[host] = tally.providers.get(host, 0) + n
        detail = {
            # (seq, prompt tokens the provider counted) for every model call.
            "prompt_tokens": [list(p) for p in folded.prompts],
            # (seq, characters of the prompt shown in full, and bounded) for
            # every message: the same card rendered both ways, so the effect
            # of the bound is measured on identical input.
            "prompt_chars": sizes,
            # Every proposal, with its verdict, so the run can be folded again
            # under other rules or a later reducer without a model.
            "proposals": [encode(list(p)) for p in folded.proposals],
            # Closes the guards refused: (seq, reason, kind, target's seq).
            "refused": [[r.seq, r.reason, r.kind, r.target_seq] for r in folded.refused],
            # Messages no attempt could read (decision 0002): (seq, error).
            "unread": [[u.source.seq, u.error] for u in folded.card.unread],
            "cache_hits": tally.cache_hits,
            "providers": tally.providers,
            "extraction_usage": asdict(extraction),
        }
        if self.reread is not None:
            # (seq, what the record proposed, what the live read proposes).
            detail["reread"] = reread
        if checker is not None:
            detail["verifier"] = {"usage": asdict(verifier), "usd": verifier.usd(),
                                  "seconds": verifying,
                                  "providers": checker.tally.providers,
                                  "cache_hits": checker.tally.cache_hits}  # fmt: skip
        return MethodRun.of(self.name, ticket, snaps, tally, failure, detail)


@dataclass(slots=True)
class _Spliced:
    """Recorded proposals, except for the messages read again live."""

    answers: dict[str, tuple[Proposal, ...]]
    live: Reader
    seqs: frozenset[int]
    log: list[list]

    def extract(self, card: Card, event: Event) -> Extraction:
        return self.extract_with_context(card, event, None)

    def extract_with_context(
        self, card: Card, event: Event, previous: MessagePosted | None,
    ) -> Extraction:
        old = self.answers.get(event.id, ())
        if event.seq not in self.seqs:
            return Extraction(old)
        new = read_event(self.live, card, event, previous)
        self.log.append([event.seq, encode(list(old)), encode(list(new.proposals))])
        return new


@dataclass(slots=True)
class _Sizing:
    """Notes how long the prompt would be with and without a bound, then extracts."""

    extractor: Extractor
    sizes: list[list[int]]

    def extract(self, card: Card, event: Event) -> Extraction:
        return self.extract_with_context(card, event, None)

    def extract_with_context(
        self, card: Card, event: Event, previous: MessagePosted | None,
    ) -> Extraction:
        if isinstance(event, MessagePosted) and event.text.strip():
            x = self.extractor
            full, _ = build_messages(card, event, x.cal, x.vocab, None, previous=previous)
            bounded, _ = build_messages(card, event, x.cal, x.vocab, Bound(), previous=previous)
            size = sum(len(m["content"]) for m in full), sum(len(m["content"]) for m in bounded)
            self.sizes.append([event.seq, *size])
        return self.extractor.extract_with_context(card, event, previous)
