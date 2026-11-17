"""What the model is shown: fixed instructions, the card so far, one new message.

The model never sees the whole transcript. It sees the card — the system's own
bounded account of the ticket — and the single message that just arrived. That
is what keeps the cost of a long ticket linear, and it is also a discipline: if
the card is not enough to interpret the next message, the card is missing
something, which is a defect to find rather than to paper over with context.

The instructions come first and never change, so a provider's prompt cache can
serve them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..card import Card
from ..events import MessagePosted
from ..lifecycle import status_at
from ..slots import DEFAULT as DEFAULT_VOCABULARY
from ..slots import Vocabulary
from ..temporal import WorkCalendar

_INSTRUCTIONS = """\
You maintain the record of a customer support ticket. You are given the record \
so far and ONE new message. Report only what this new message adds or changes.

Hard rules:
- Every item needs "quote": a short span copied character for character from \
the new message. Never paraphrase, translate or merge spans in a quote. If you \
cannot quote it, do not report it.
- Report only what the message states. No inference, no advice, no summary.
- Write "action", "value" and "text" in the language of the message.
- When the message adds nothing, return empty lists. Most messages add little.

facts — stable details of the case (order number, product, plan, device, what \
is broken, current state of the problem).
- "slot" names what the fact is about. If the record already has a slot for \
the same thing, use exactly that slot. Otherwise use one from this list when \
it fits: {slots}. Only when none fits, make a short lowercase English key in \
the same "object.attribute" form. Never use two slots for one thing.
- "change": true only when the message says the value CHANGED ("I've upgraded \
you to Pro"). false when it just states or disputes a value.

commitments — a party promises a future action.
- "owner": "agent" for a personal promise ("I will call you back"), "company" \
for a promise on the company's behalf ("we will refund you"), "customer" for \
the customer's own promise ("I'll send the screenshot").
- Only the speaker can promise. A customer writing "you promised a refund" is \
NOT a company commitment — report nothing for it.
- A vague intention ("we'll look into it") is a commitment without a deadline.
- "due_quote": the deadline phrase copied from the message, or null.
- "due": the deadline in structured form. NEVER compute a calendar date \
yourself; describe what was said:
    "in 2 hours", "within 3 business days" -> kind "offset", amount, unit
    "today" / "tomorrow" / "the day after"   -> kind "day", days_ahead 0 / 1 / 2
    "by Friday"                               -> kind "weekday", weekday, week null
    "Friday next week"                        -> kind "weekday", weekday, week 1
    "15 March"                                -> kind "date", day, month, year or null
    "by end of day / week / month"            -> kind "period_end", period
    no deadline                               -> kind "none"
  "time" is "HH:MM" (24h) when a clock time was given, else null. Set every \
field that does not apply to null.
- "supersedes": the id of an unresolved commitment this one replaces (a new \
date for the same promise), else null.

resolved — the message says a listed commitment's action WAS DONE. Use its id.
cancelled — the message says a listed commitment is no longer needed.

steps — a fix or diagnostic action that was suggested or tried. "outcome" is \
"pending" unless this same message says whether it worked.
step_outcomes — the message reports that a listed step "worked" or "failed".

questions — the message asks something the other side must answer.
answers — the message answers a listed open question. Use its id.

Use only ids that appear in the record (C1, S1, Q1, ...).\
"""


def system_prompt(vocab: Vocabulary = DEFAULT_VOCABULARY) -> str:
    """The fixed instructions, with the deployment's slot vocabulary written in."""
    return _INSTRUCTIONS.replace("{slots}", ", ".join(vocab.slots()))


SYSTEM_PROMPT = system_prompt()

_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass(frozen=True, slots=True)
class Rendered:
    text: str
    #: Short handle shown to the model -> real id.
    aliases: dict[str, str] = field(default_factory=dict)


def render_card(card: Card, now: datetime, cal: WorkCalendar) -> Rendered:
    """The record so far, with short handles for anything the model may refer to."""
    aliases: dict[str, str] = {}
    lines = [
        "RECORD SO FAR",
        f"Assignee: {card.assignee or 'nobody'} | Status: {card.status or 'unknown'}",
    ]

    facts = card.current_facts()
    lines.append("Facts:" if facts else "Facts: none")
    lines += [f"- {f.slot} = {f.value} (said by {f.source.side.value})" for f in facts]

    commitments = [c for c in card.commitments if c.unresolved]
    lines.append("Unresolved commitments:" if commitments else "Unresolved commitments: none")
    for number, c in enumerate(commitments, 1):
        alias = f"C{number}"
        aliases[alias] = c.id
        due = ""
        if c.due is not None:
            local = c.due.deadline.due_at.astimezone(cal.tz)
            due = f" | due {local:%Y-%m-%d %H:%M}"
        lines.append(f"- {alias} [{c.owner.value}, {status_at(c, now).value}] {c.action}{due}")

    lines.append("Steps:" if card.steps else "Steps: none")
    for number, s in enumerate(card.steps, 1):
        alias = f"S{number}"
        aliases[alias] = s.id
        lines.append(f"- {alias} [{s.outcome.value}] {s.action}")

    questions = [q for q in card.questions if q.open]
    lines.append("Open questions:" if questions else "Open questions: none")
    for number, q in enumerate(questions, 1):
        alias = f"Q{number}"
        aliases[alias] = q.id
        lines.append(f"- {alias} [waiting on {q.waiting_on.value}] {q.text}")

    return Rendered("\n".join(lines), aliases)


def render_message(event: MessagePosted, cal: WorkCalendar) -> str:
    local = event.at.astimezone(cal.tz)
    visibility = "visible to the customer" if event.public else "internal note"
    name = f" ({event.author.name})" if event.author.name else ""
    return "\n".join(
        [
            "NEW MESSAGE",
            f"From: {event.author.role.value}{name}, id {event.author.id} | {visibility}",
            f"Sent: {_WEEKDAY_NAMES[local.weekday()]} {local:%Y-%m-%d %H:%M} ({cal.tz.key})",
            "Text:",
            event.text,
        ]
    )


def build_messages(
    card: Card, event: MessagePosted, cal: WorkCalendar, vocab: Vocabulary = DEFAULT_VOCABULARY
) -> tuple[list[dict[str, str]], dict[str, str]]:
    rendered = render_card(card, event.at, cal)
    user = f"{rendered.text}\n\n{render_message(event, cal)}"
    return (
        [{"role": "system", "content": system_prompt(vocab)}, {"role": "user", "content": user}],
        rendered.aliases,
    )
