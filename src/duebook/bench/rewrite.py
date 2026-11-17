"""Putting planted events into a different model's words.

Template text is predictable, and an extractor measured only on sentences its
own authors wrote is measured too kindly. A rewriter hands each message to a
language model to rephrase — while the planted spans, the ones the gold labels
rest on, must survive word for word.

Two rules are enforced in code rather than hoped for:

* the rewriting model must not be the extracting model. A model reads its own
  phrasing more easily than anyone else's, so sharing one would flatter the
  result;
* a rewrite that lost a planted span is discarded and the template text is
  kept. The label stays true either way.
"""

from __future__ import annotations

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
Rewrite a customer support chat message in natural, informal Russian, the way \
a real {role} would type it. Keep the meaning. You may add a greeting or a \
filler phrase. Do not add promises, deadlines, facts or questions that are not \
in the original.

These spans must appear in your text EXACTLY as written, character for \
character:
{anchors}

Return JSON: {{"text": "..."}}\
"""


class SameModel(ValueError):
    """The tickets would be written by the model that is about to read them."""


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
                event = replace(event, text=self._text(event, ticket.anchors.get(event.id, ())))
            events.append(event)
        return replace(ticket, events=tuple(events))

    def _text(self, event: MessagePosted, anchors: tuple[str, ...]) -> str:
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
        if not isinstance(text, str) or not text.strip() or any(a not in text for a in anchors):
            self.discarded += 1
            return event.text
        return text.strip()
