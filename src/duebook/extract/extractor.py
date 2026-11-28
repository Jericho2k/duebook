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
from typing import Protocol

from ..card import Card
from ..events import Event, MessagePosted, PromiseCheck
from ..proposals import Proposal, Unreadable
from ..reducer import Outcome, apply
from ..slots import DEFAULT as DEFAULT_VOCABULARY
from ..slots import Vocabulary
from ..temporal import WorkCalendar
from .client import ChatClient, Completion, ModelCall, ModelError, call_kind, capture_calls
from .cost import Usage
from .prompt import Bound, build_messages
from .schema import OUTPUT_SCHEMA, Malformed, parse_output

#: Round 4 answered in 79 output tokens a message on average. An answer that
#: runs past this is looping, and is cut off rather than paid for to the end.
ANSWER_TOKENS = 800
#: The escalated read thinks first: reasoning "low" spends 1,000-2,800 hidden
#: tokens before an answer of the same size.
ESCALATED_TOKENS = 4_000
#: Failed attempts at one message before it is recorded as unreadable
#: (decision 0002): each attempt is a read and, if its answer was unreadable,
#: the escalated read; the hold's backoff separates them.
ATTEMPTS = 3


class JsonModel(Protocol):
    def complete_json(
        self, messages: list[dict[str, str]], schema: dict, name: str = ...
    ) -> Completion: ...


class Reader(Protocol):
    """Anything that turns an event into proposals. Raises when it cannot."""

    def extract(self, card: Card, event: Event) -> Extraction: ...


def read_event(reader: Reader, card: Card, event: Event,
               previous: MessagePosted | None = None) -> Extraction:  # fmt: skip
    """Supply the preceding turn to readers that use conversational context."""
    contextual = getattr(reader, "extract_with_context", None)
    with capture_calls() as calls:
        start = len(calls)
        try:
            result = (contextual(card, event, previous) if contextual
                      else reader.extract(card, event))
        except ModelError as error:
            error.model_calls = tuple(calls[start:])
            raise
        finally:
            for call in calls[start:]:
                call.event_id = event.id
        return replace(result, model_calls=tuple(calls[start:]))


def reading_clients(client: ChatClient) -> tuple[ChatClient, ChatClient]:
    """The extractor's client and its escalation.

    At temperature 0 the same request fails the same way, so an unreadable
    answer is not asked again: it is read once more with reasoning on.
    """
    first = replace(client, max_tokens=ANSWER_TOKENS, content_retries=0)
    return first, replace(first, reasoning="low", max_tokens=ESCALATED_TOKENS)


def gives_up(error: Exception, attempts: int) -> bool:
    """Whether this failure, the ``attempts``-th at one message, ends the reading of it.

    Only an answer that could not be read counts against the message: a
    timeout or an outage says nothing about it, and the ticket waits.
    """
    return getattr(error, "unreadable", False) and attempts >= ATTEMPTS


def unread(event: Event, error: Exception) -> Extraction:
    """What is recorded for a message no attempt could read (decision 0002)."""
    text = event.text if isinstance(event, MessagePosted) else ""
    return Extraction((Unreadable(text, f"{type(error).__name__}: {error}"[:500]),),
                      usage=getattr(error, "usage", None) or Usage(),
                      model_calls=getattr(error, "model_calls", ()))  # fmt: skip


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
    model_calls: tuple[ModelCall, ...] = ()

    @property
    def total_usage(self) -> Usage:
        return self.usage if self.verifier_usage is None else self.usage + self.verifier_usage.usage


def mark_useful(calls: tuple[ModelCall, ...], before: Card, outcome: Outcome, event: Event,
                proposals, cal, vocab=None, guards=None, previous=None) -> None:
    answered = set()
    for call in reversed(calls):
        if not call.kind.startswith("reconcile_"):
            continue
        index = call.proposal
        call.useful = False
        if index in answered:
            continue
        answered.add(index)
        if isinstance(event, PromiseCheck):
            call.useful = before != outcome.card
        elif call.original is not None and index is not None:
            p = proposals[index]
            original = call.original
            if original.supersedes == p.supersedes:
                original = replace(original, verification=p.verification)
            baseline = (*proposals[:index], original, *proposals[index + 1:])
            card = apply(before, event, baseline, cal, vocab, guards, previous=previous).card
            call.useful = card != outcome.card


@dataclass(slots=True)
class Extractor:
    model: JsonModel
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    vocab: Vocabulary = DEFAULT_VOCABULARY
    #: Caps what the model is shown of a long card (see ``prompt.Bound``).
    #: None shows everything.
    bound: Bound | None = None
    #: Asked once when ``model``'s answer is cut off or unreadable.
    escalation: JsonModel | None = None

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
        completion = self._complete(messages)
        proposals, malformed = parse_output(completion.data, aliases)
        return Extraction(tuple(proposals), tuple(malformed), completion.usage, completion.retried)

    def _complete(self, messages: list[dict[str, str]]) -> Completion:
        try:
            return self.model.complete_json(messages, OUTPUT_SCHEMA, name="ticket_update")
        except ModelError as error:
            if not error.unreadable or self.escalation is None:
                raise
            first = error
        try:
            with call_kind("escalate"):
                second = self.escalation.complete_json(
                    messages, OUTPUT_SCHEMA, name="ticket_update"
                )
        except ModelError as error:
            error.usage += first.usage
            raise
        return replace(second, usage=first.usage + second.usage,
                       retried=(str(first), *second.retried))  # fmt: skip
