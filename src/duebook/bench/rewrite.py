"""Putting planted events into a different model's words.

Template text is predictable, and an extractor measured only on sentences its
own authors wrote is measured too kindly. A rewriter hands each message to a
language model to rephrase — while the planted spans, the ones the gold labels
rest on, must survive word for word.

Three rules are enforced in code rather than hoped for:

* the rewriting model must not be the extracting model. A model reads its own
  phrasing more easily than anyone else's, so sharing one would flatter the
  result;
* a rewrite that lost a planted span is discarded and the template text is
  kept. The label stays true either way;
* so is a rewrite that is no longer one message. Given a one-word "Ладно.",
  a writer model sometimes invents a whole exchange, speaker labels and all,
  with a new promise in it — content no label accounts for. A rewrite with a
  line break, a speaker label, or several times the original's length is
  discarded on that ground.
* and so is a rewrite that ADDS something a label would have to account for.
  The cheap writers tried for round 2 append "мы разберёмся" to a status
  ping, "так и не привезли" to a thank-you, a complaint about bonuses to a
  confirmation. A rewrite is discarded when it has a number, a question, a
  negation or a promise-like verb more than the original had.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..events import MessagePosted
from ..extract.cost import Usage
from ..extract.extractor import JsonModel
from .generate import SimTicket

_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

_PROMPT = """\
Rewrite a customer support chat message in natural Russian, the way a real \
{role} would type it: a customer casually, a support agent politely. Keep the \
meaning and the grammatical gender of the speaker. Vary the wording and the \
sentence structure; do not just add a greeting, and do not start with one \
every time. Do not add promises, deadlines, facts or questions that are not \
in the original. Return this ONE message only: no reply from the other side, \
no speaker labels, no line breaks. A short message stays short.

These spans must appear in your text EXACTLY as written, character for \
character:
{anchors}

Return JSON: {{"text": "..."}}\
"""


class SameModel(ValueError):
    """The tickets would be written by the model that is about to read them."""


def with_rewritten_text(ticket: SimTicket, texts: dict[str, str]) -> SimTicket:
    """Use archived wording while keeping the events, times and labels aligned."""
    events = tuple(
        replace(e, text=texts[e.id]) if isinstance(e, MessagePosted) else e for e in ticket.events
    )
    return replace(ticket, events=events)


def require_different_models(writer: str, extractor: str) -> None:
    if _family(writer) == _family(extractor):
        raise SameModel(
            f"tickets written by {writer!r} must not be read by {extractor!r}: "
            "use a different model to generate than to extract"
        )


def _family(model: str) -> str:
    # "vendor/name:variant" and "vendor/name" are one model for this purpose.
    return model.strip().lower().split(":", 1)[0]


@dataclass(slots=True)
class Rewriter:
    model: JsonModel
    spent: Usage = Usage()
    #: Rewrites thrown away because a planted span did not survive.
    discarded: int = 0

    def rewrite(self, ticket: SimTicket) -> SimTicket:
        events = []
        for event in ticket.events:
            if isinstance(event, MessagePosted):
                event = replace(event, text=self.text(event, ticket.anchors.get(event.id, ())))
            events.append(event)
        return replace(ticket, events=tuple(events))

    def text(self, event: MessagePosted, anchors: tuple[str, ...]) -> str:
        """One message in the writer's words, or as it was if the rewrite is unusable."""
        if event.text in anchors:
            return event.text
        listed = "\n".join(f"- {a}" for a in anchors) or "- (none)"
        completion = self.model.complete_json(
            [
                {"role": "system",
                 "content": _PROMPT.format(role=event.author.role.value, anchors=listed)},
                {"role": "user", "content": event.text},
            ],
            _SCHEMA,
            name="rewrite",
        )  # fmt: skip
        self.spent += completion.usage
        text = completion.data.get("text")
        if not isinstance(text, str) or not acceptable(event.text, text.strip(), anchors):
            self.discarded += 1
            return event.text
        return text.strip()


_SPEAKER = re.compile(
    r"(?:^|\s)(?:клиент|агент|оператор|поддержка|customer|agent)\w*(?:\s+\w+)?\s*:", re.I
)


_NUMBER = re.compile(r"\d+")
_NEGATION = re.compile(r"(?:^|[^а-яё])(?:не|нет|ни)(?=[^а-яё]|$)", re.I)
#: Stems of verbs in which support promises, or reports, something.
_DEEDS = ("разбер", "постара", "исправ", "сдела", "провер", "свяж", "звон", "отправ", "верн",
          "пришл", "присл", "вышл", "высл", "уточн", "реш", "помо", "напиш", "сообщ", "оформ",
          "обещ", "компенс", "достав", "привез", "получ", "заберё", "забер", "начисл",
          "восстанов", "переда", "перевед", "перевёл", "перевел")  # fmt: skip


def acceptable(original: str, text: str, anchors: tuple[str, ...]) -> bool:
    """A rewrite is one message that keeps every planted span and adds nothing a label would
    have to account for."""
    if not text or any(a not in text for a in anchors):
        return False
    if "\n" in text or _SPEAKER.search(text):
        return False
    if len(text) > max(3 * len(original), len(original) + 80):
        return False
    if set(_NUMBER.findall(text)) - set(_NUMBER.findall(original)):
        return False
    if text.count("?") > original.count("?"):
        return False
    if len(_NEGATION.findall(text)) > len(_NEGATION.findall(original)):
        return False
    low, was = text.casefold(), original.casefold()
    return all(low.count(stem) <= was.count(stem) for stem in _DEEDS)
