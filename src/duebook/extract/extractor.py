"""From one ticket event to proposals.

    proposals = extractor.extract(card, event).proposals
    card = apply(card, event, proposals).card

A failure RAISES. It never returns an empty list, because "the model found
nothing in this message" and "the model could not be asked" must not look the
same downstream: applying an event with no proposals advances the card past a
message nobody read. The caller holds the ticket at that event and retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..card import Card
from ..events import Event, MessagePosted
from ..proposals import Proposal
from ..temporal import WorkCalendar
from .client import Completion
from .cost import Usage
from .prompt import build_messages
from .schema import OUTPUT_SCHEMA, Malformed, parse_output


class JsonModel(Protocol):
    def complete_json(
        self, messages: list[dict[str, str]], schema: dict, name: str = ...
    ) -> Completion: ...


@dataclass(frozen=True, slots=True)
class Extraction:
    proposals: tuple[Proposal, ...] = ()
    #: Entries the model produced that could not be read. Counted, not hidden.
    malformed: tuple[Malformed, ...] = ()
    usage: Usage = field(default_factory=Usage)
    #: Why earlier model attempts were discarded, if any were.
    retried: tuple[str, ...] = ()


@dataclass(slots=True)
class Extractor:
    model: JsonModel
    cal: WorkCalendar = field(default_factory=WorkCalendar)

    def extract(self, card: Card, event: Event) -> Extraction:
        """Read one event against the card. Raises ``ModelError`` on failure."""
        if not isinstance(event, MessagePosted) or not event.text.strip():
            # Assignee and status changes carry no text; the reducer handles them.
            return Extraction()
        messages, aliases = build_messages(card, event, self.cal)
        completion = self.model.complete_json(messages, OUTPUT_SCHEMA, name="ticket_update")
        proposals, malformed = parse_output(completion.data, aliases)
        return Extraction(tuple(proposals), tuple(malformed), completion.usage, completion.retried)
