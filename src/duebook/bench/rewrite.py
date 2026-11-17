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


def acceptable(original: str, text: str, anchors: tuple[str, ...]) -> bool:
    """A rewrite is one message that keeps every planted span, and nothing much more."""
    if not text or any(a not in text for a in anchors):
        return False
    if "\n" in text or _SPEAKER.search(text):
        return False
    return len(text) <= max(3 * len(original), len(original) + 80)
