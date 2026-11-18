"""From one ticket event to proposals.

    proposals = extractor.extract(card, event).proposals
    card = apply(card, event, proposals).card

A failure RAISES. It never returns an empty list, because "the model found
nothing in this message" and "the model could not be asked" must not look the
same downstream: applying an event with no proposals advances the card past a
message nobody read. The caller holds the ticket at that event and retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Protocol

from ..card import Card
from ..events import Event, MessagePosted
from ..proposals import Proposal
from ..slots import DEFAULT as DEFAULT_VOCABULARY
from ..slots import Vocabulary
from ..temporal import WorkCalendar
from .client import Completion, ModelError
from .cost import Usage
from .prompt import Bound, build_batch, build_messages
from .schema import BATCH_SCHEMA, OUTPUT_SCHEMA, Malformed, parse_output

MAX_BATCH_SIZE = 4
BATCH_TIMEOUT = timedelta(seconds=10)
ReadTurn = tuple[MessagePosted, MessagePosted | None]


class JsonModel(Protocol):
    def complete_json(
        self, messages: list[dict[str, str]], schema: dict, name: str = ...
    ) -> Completion: ...


class Reader(Protocol):
    """Anything that turns an event into proposals. Raises when it cannot."""

    def extract(self, card: Card, event: Event) -> Extraction: ...


def read_event(reader: Reader, card: Card, event: Event, previous: MessagePosted | None = None,
               prepared: Extraction | None = None) -> Extraction:  # fmt: skip
    """Supply the preceding turn to readers that use conversational context."""
    if prepared is not None and not prepared.reread:
        verify = getattr(reader, "verify_extraction", None)
        return verify(card, event, previous, prepared) if verify else prepared
    contextual = getattr(reader, "extract_with_context", None)
    try:
        outcome = contextual(card, event, previous) if contextual else reader.extract(card, event)
    except ModelError as error:
        if prepared is not None:
            error.usage += prepared.usage
        raise
    return outcome if prepared is None else replace(outcome, usage=prepared.usage + outcome.usage)


def read_batch(reader: Reader, card: Card, turns: list[ReadTurn]) -> dict[int, Extraction]:
    """Read adjacent messages together, falling back individually on batch failure."""
    extract = getattr(reader, "extract_batch", None)
    if extract is None or len(turns) < 2:
        return {}
    try:
        return extract(card, turns)
    except ModelError as error:
        # Fall back for the whole group. Account for the attempted call once.
        prepared = {event.seq: Extraction(reread=True) for event, _ in turns}
        first = turns[0][0].seq
        prepared[first] = replace(prepared[first], usage=error.usage)
        return prepared


@dataclass(frozen=True, slots=True)
class VerifierUsage:
    #: One call per proposal, including failures that report no usage.
    calls: int
    failures: int
    usage: Usage
    providers: dict[str, int]


@dataclass(frozen=True, slots=True)
class Extraction:
    proposals: tuple[Proposal, ...] = ()
    #: Entries the model produced that could not be read. Counted, not hidden.
    malformed: tuple[Malformed, ...] = ()
    usage: Usage = field(default_factory=Usage)
    #: Why earlier model attempts were discarded, if any were.
    retried: tuple[str, ...] = ()
    verifier_usage: VerifierUsage | None = None
    reread: bool = False

    @property
    def total_usage(self) -> Usage:
        return self.usage if self.verifier_usage is None else self.usage + self.verifier_usage.usage


@dataclass(slots=True)
class Extractor:
    model: JsonModel
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    vocab: Vocabulary = DEFAULT_VOCABULARY
    #: Caps what the model is shown of a long card (see ``prompt.Bound``).
    #: None shows everything.
    bound: Bound | None = None
    batch_model: JsonModel | None = None

    def extract(self, card: Card, event: Event) -> Extraction:
        return self.extract_with_context(card, event, None)

    def extract_with_context(
        self, card: Card, event: Event, previous: MessagePosted | None,
    ) -> Extraction:
        """Read one event against the card. Raises ``ModelError`` on failure."""
        if not isinstance(event, MessagePosted) or not event.text.strip():
            # Assignee and status changes carry no text; the reducer handles them.
            return Extraction()
        messages, aliases = build_messages(card, event, self.cal, self.vocab, self.bound,
                                           previous=previous)  # fmt: skip
        completion = self.model.complete_json(messages, OUTPUT_SCHEMA, name="ticket_update")
        proposals, malformed = parse_output(completion.data, aliases)
        return Extraction(tuple(proposals), tuple(malformed), completion.usage, completion.retried)

    def extract_batch(self, card: Card, turns: list[ReadTurn]) -> dict[int, Extraction]:
        if self.batch_model is None:
            return {}
        messages, aliases = build_batch(card, turns, self.cal, self.vocab, self.bound)
        completed = self.batch_model.complete_json(messages, BATCH_SCHEMA, name="ticket_batch")
        rows = completed.data.get("messages")
        if not isinstance(rows, list) or len(rows) != len(turns) or any(
            not isinstance(row, dict) or row.get("seq") != turn[0].seq
            or not isinstance(row.get("reread"), bool)
            for row, turn in zip(rows, turns, strict=True)
        ):
            raise ModelError("batch response did not cover the requested messages", completed.usage)
        result = {}
        for row in rows:
            proposals, malformed = parse_output(row, aliases)
            result[row["seq"]] = Extraction(
                tuple(proposals), tuple(malformed), reread=row["reread"] or bool(malformed)
            )
        first = turns[0][0].seq
        result[first] = replace(result[first], usage=completed.usage, retried=completed.retried)
        return result
