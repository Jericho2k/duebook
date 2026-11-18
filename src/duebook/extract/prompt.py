"""What the model is shown: fixed instructions, the card so far, one new message.

The card bounds the context. The preceding turn can identify the subject of
a short reply, while proposed updates quote the message being processed.

The instructions come first and never change, so a provider's prompt cache can
serve them.

Each unresolved commitment is shown with the words it was made in. A close
must name its target twice — by handle and by a span copied from those words
— and the reducer admits it only when the two agree (see ``closing``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..card import Card, StepOutcome
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
- When the message gives a different value for something the record already \
holds — even from the other side, or "according to our records" — use the \
record's slot for it. A disagreement is only visible if both values share a \
slot.
- "change": true only when the speaker says the value was MADE different \
("I've upgraded you to Pro", "I've moved, my new address is..."). false when \
the message states a value, reports what records show, or disputes what the \
other side said ("according to our data you were charged 2900", "we shipped \
model X") — those are not changes.

commitments — the speaker's side becomes accountable for something \
happening, whoever does the work.
- "quote": the whole clause that makes the promise, with what it is about \
("Уточню на складе и напишу вам до пятницы", not "напишу вам до пятницы"): \
it is the promise's wording in the record from then on.
- "action": what will happen AND what it affects — the order, item, \
workspace, refund or document by name ("вернуть деньги за чехол", \
"восстановить доступ к пространству Альфа"), never just "вернуть деньги". \
Two promises about different things must have different actions.
- "owner": "agent" for a personal promise ("I will call you back"), "company" \
for a promise on the company's behalf ("we will refund you"), including work \
someone else on the company's side will do: "Курьер заберёт товар завтра", \
"бухгалтерия вернёт деньги", "инженеры выпустят исправление", "дежурный \
инженер перезвонит" are company commitments. "customer" for the customer's \
own promise ("I'll send the screenshot").
- Only the speaker's side can promise. A customer writing "you promised a \
refund" is NOT a company commitment — report nothing for it.
- A vague intention ("we'll look into it") is a commitment without a deadline.
- NOT commitments: a status report ("your request is being processed", "we \
are working on it"); a prediction about what someone outside the speaker's \
side will do ("банк обычно зачисляет деньги за 3 дня") — at most a fact; a \
conditional offer — report it under conditionals.
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
date for the same promise), else null. A promise about something else — \
another item, another topic — is a new commitment even when it is worded \
alike: "supersedes" null. "target_quote": when "supersedes" is \
set, words copied from THAT commitment's "said" text in the record that show \
which one it is; else null.

conditionals — an offer owed only if a condition comes true: "если посылка не \
придёт до пятницы, вернём деньги". A promise with no "if" is a commitment. \
"quote": the whole offer; "condition": the condition copied from it ("если \
посылка не придёт до пятницы"); "owner" and "action" as for a commitment; \
"due_quote" and "due": the condition's deadline, in the same form, else null.

resolved — the message says a listed commitment's action WAS DONE, in words \
that name the deed: "the money arrived", "got the screenshot", "thanks for \
calling back", "thanks for the call about the delivery". Either side may say \
it: the customer confirming what the company did, the company confirming \
what the customer sent. Bare thanks or acknowledgement ("thanks", \
"спасибо", "ok, waiting", "thanks for keeping me posted") names no deed and \
confirms nothing.
cancelled — the message says a listed commitment is no longer needed.
For both: "id" is the commitment; "target_quote" is copied character for \
character from THAT commitment's "said" text in the record (not from the new \
message) and shows which one it is; "quote" is the words of the new message \
that name what was done or called off. Report every such statement: the \
record checks each close against the commitment's own words before it \
accepts it, so do not hold one back.
For resolved only, a brief reply that confirms receipt or completion can \
refer to something named in the preceding turn. Propose the possible target \
and copy the words naming it from that turn into "context_quote". Use null \
when the current message identifies the target itself. If a reply confirms \
one of several things without saying which, propose a possible target for \
review. Never extract facts, promises or other changes from context alone.

condition_outcomes — the message says whether a listed conditional offer's \
condition came true: "holds" ("посылка так и не пришла") or "failed" \
("посылка пришла"). "id" is the offer; "target_quote" is copied from ITS \
"said" text; "quote" is the words of the new message that say it.

steps — a fix or diagnostic action that was suggested or tried. "outcome" is \
"pending" unless this same message says whether it worked.
step_outcomes — the message reports that a listed step "worked" or "failed".

questions — the message asks something the other side must answer.
answers — the message answers a listed open question. Use its id.

Use only ids that appear in the record (C1, O1, S1, Q1, ...).\
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


@dataclass(frozen=True, slots=True)
class Bound:
    """How much of the card the model is shown on a long ticket.

    The card itself is never cut — this bounds only the prompt. What the model
    can act on is always shown in full: every unresolved commitment (it may be
    resolved, cancelled or renegotiated), every pending step (its outcome may
    be reported) and every fact a dispute rests on. What is capped is history
    the model only reads: steps whose outcome is known, the oldest open
    questions, facts beyond the most recent. A capped list says how many items
    it leaves out, so the model is not told the record is complete when it is
    not.

    Hiding an item removes its handle from the prompt, so the model cannot
    refer to it; the reducer's rules are unchanged, and a reference to a
    hidden handle is refused as an unknown target like any other.
    """

    #: Current facts shown, most recent first; disputed ones always.
    facts: int = 12
    #: Steps with a known outcome shown, most recent first; pending ones always.
    settled_steps: int = 6
    #: Open questions shown, most recent first.
    questions: int = 6


def render_card(
    card: Card, now: datetime, cal: WorkCalendar, bound: Bound | None = None
) -> Rendered:
    """The record so far, with short handles for anything the model may refer to."""
    aliases: dict[str, str] = {}
    lines = [
        "RECORD SO FAR",
        f"Assignee: {card.assignee or 'nobody'} | Status: {card.status or 'unknown'}",
    ]

    facts = list(card.current_facts())
    hidden_facts = 0
    if bound is not None and len(facts) > bound.facts:
        disputed = {i for c in card.open_contradictions() for i in c.fact_ids}
        keep = {f.id for f in sorted(facts, key=lambda f: f.valid_from)[-bound.facts :]}
        shown = [f for f in facts if f.id in keep or f.id in disputed]
        hidden_facts, facts = len(facts) - len(shown), shown
    lines.append("Facts:" if facts else "Facts: none")
    lines += [f"- {f.slot} = {f.value} (said by {f.source.side.value})" for f in facts]
    lines += _more(hidden_facts, "older facts")

    commitments = [c for c in card.commitments if c.unresolved]
    lines.append("Unresolved commitments:" if commitments else "Unresolved commitments: none")
    for number, c in enumerate(commitments, 1):
        alias = f"C{number}"
        aliases[alias] = c.id
        due = ""
        if c.due is not None:
            local = c.due.deadline.due_at.astimezone(cal.tz)
            due = f" | due {local:%Y-%m-%d %H:%M}"
        said = f' | said: "{c.source.quote}"'
        lines.append(
            f"- {alias} [{c.owner.value}, {status_at(c, now).value}] {c.action}{due}{said}"
        )

    offers = [k for k in card.conditionals if k.pending]
    lines.append("Conditional offers:" if offers else "Conditional offers: none")
    for number, k in enumerate(offers, 1):
        alias = f"O{number}"
        aliases[alias] = k.id
        due = ""
        if k.due is not None:
            due = f" | condition due {k.due.deadline.due_at.astimezone(cal.tz):%Y-%m-%d %H:%M}"
        lines.append(f'- {alias} [{k.owner.value}] {k.action} | condition: "{k.condition}"'
                     f'{due} | said: "{k.source.quote}"')  # fmt: skip

    steps = list(enumerate(card.steps, 1))
    hidden_steps = 0
    if bound is not None:
        settled = [n for n, s in steps if s.outcome is not StepOutcome.PENDING]
        keep = set(settled[-bound.settled_steps :]) if bound.settled_steps else set()
        shown = [(n, s) for n, s in steps if s.outcome is StepOutcome.PENDING or n in keep]
        hidden_steps, steps = len(steps) - len(shown), shown
    lines.append("Steps:" if steps else "Steps: none")
    for number, s in steps:
        # Handles keep their position in the full list, so a step is called
        # the same whether or not older ones are shown.
        alias = f"S{number}"
        aliases[alias] = s.id
        lines.append(f"- {alias} [{s.outcome.value}] {s.action}")
    lines += _more(hidden_steps, "earlier steps with a known outcome")

    questions = [q for q in card.questions if q.open]
    hidden_questions = 0
    if bound is not None and len(questions) > bound.questions:
        hidden_questions = len(questions) - bound.questions
        questions = questions[hidden_questions:]
    lines.append("Open questions:" if questions else "Open questions: none")
    for number, q in enumerate(questions, hidden_questions + 1):
        alias = f"Q{number}"
        aliases[alias] = q.id
        lines.append(f"- {alias} [waiting on {q.waiting_on.value}] {q.text}")
    lines += _more(hidden_questions, "older open questions")

    return Rendered("\n".join(lines), aliases)


def _more(count: int, what: str) -> list[str]:
    return [f"- (+{count} {what} not shown)"] if count else []


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
    card: Card,
    event: MessagePosted,
    cal: WorkCalendar,
    vocab: Vocabulary = DEFAULT_VOCABULARY,
    bound: Bound | None = None,
    *,
    previous: MessagePosted | None = None,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    rendered = render_card(card, event.at, cal, bound)
    user = "\n\n".join((rendered.text, render_turn(event, cal, previous)))
    return (
        [{"role": "system", "content": system_prompt(vocab)}, {"role": "user", "content": user}],
        rendered.aliases,
    )


def render_turn(event: MessagePosted, cal: WorkCalendar, previous: MessagePosted | None) -> str:
    message = render_message(event, cal)
    if previous is None:
        return message
    return (f"PRECEDING TURN (read-only, message {previous.seq}, "
            f"{previous.author.role.value}):\n{previous.text}\n\n{message}")
