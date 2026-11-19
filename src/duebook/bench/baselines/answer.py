"""What every baseline is asked at a checkpoint, and how its answer is read.

The baselines differ only in WHAT the model is shown when asked — the whole
transcript, a running summary, what Mem0 returns. The question, its rules and
the answer format are shared, so the baselines differ from each other, and
from the event memory, in their memory and nothing else.

The rules in the question are the same rules the event memory's extractor is
given: only the speaker can promise; "you promised me" is not a new promise;
a vague intention is a promise without a deadline; a disputed value is a
dispute, not a fact; bare thanks confirms nothing, and a confirmation closes
only the promise it names. A baseline that was not told the rules would lose for
the wrong reason.

One thing is deliberately different. The event memory never lets the model
compute a date; a baseline has no such machinery, so it is asked for the
deadline as an absolute local time, which is what a system built on a model's
reading would store. How often that goes wrong is part of what is measured.
A day-only deadline ("by Friday") is read as the end of that day, the same
convention the resolver uses, and the model is told so.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from ...events import AssigneeChanged, Event, MessagePosted, Side, StatusChanged
from ...temporal import WorkCalendar
from ..result import Checkpoint, Dispute, FactItem, FailedStep, Promise, PromiseStatus, Snapshot

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_STR = {"type": "string"}
_OPT_STR = {"type": ["string", "null"]}
_OPT_INT = {"type": ["integer", "null"]}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _list(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": _obj(properties)}


ANSWER_SCHEMA: dict[str, Any] = _obj(
    {
        "commitments": _list(
            {
                "owner": {"type": "string", "enum": ["company", "customer"]},
                "source_seq": {"type": "integer"},
                "action": _STR,
                "due": _OPT_STR,
                "status": {"type": "string", "enum": [s.value for s in PromiseStatus]},
                "closed_seq": _OPT_INT,
            }
        ),
        "current_facts": _list({"name": _STR, "value": _STR, "source_seq": _OPT_INT}),
        "failed_steps": _list({"action": _STR, "suggested_seq": _OPT_INT, "failed_seq": _OPT_INT}),
        "disputes": _list(
            {
                "topic": _STR,
                "customer_says": _STR,
                "company_says": _STR,
                "customer_seq": _OPT_INT,
                "company_seq": _OPT_INT,
            }
        ),
    }
)

INSTRUCTIONS = """\
You keep the account of a customer support ticket for an operator who is \
taking it over now. From the material below, report the state of the ticket \
as of NOW. Messages are numbered (#N); refer to them by that number.

commitments — every promise made in the ticket, kept or not: a party \
promises a future action.
- "owner": "company" for the support side (an agent's personal promise \
counts as the company's), "customer" for the customer's own promises.
- Only the speaker can promise. A customer writing "you promised me a \
refund" is NOT a new promise; it refers to an earlier one.
- A vague intention ("we'll look into it") is a promise without a deadline. \
A status report ("your request is being processed") is not a promise.
- "source_seq": the number of the message in which the promise was made — \
not a later message that mentions it.
- "due": the deadline as an absolute local time in {tz}, resolved against \
the time the promise was made. "YYYY-MM-DD HH:MM" when a time of day follows \
from what was said ("in 2 hours" said at 10:15 is 12:15 the same day; \
"tomorrow by 15:00"). "YYYY-MM-DD" when only a day is meant ("by Friday", \
"within 3 business days", "by end of week", "до 20 января"): the deadline is \
the end of that day. Business days are Monday to Friday. null when no \
deadline was given.
- "status": "fulfilled" when the OTHER side confirmed it was done, in words \
that name the deed ("thanks for calling back", "the money arrived"); bare \
thanks or acknowledgement ("thanks", "спасибо", "ok, waiting") confirms \
nothing. "claimed" when the party that owes it says it is done but the other \
side has not confirmed; "cancelled" or "superseded" when it was called off or \
replaced by a renegotiated promise; otherwise "open" — including when the \
deadline has passed. When two similar promises are open, a confirmation \
closes only the one it names; if it names neither, both stay "open".
- "closed_seq": the number of the message that confirmed, claimed, cancelled \
or replaced it; null while it is open.

current_facts — stable details of the case as they stand NOW: order number, \
plan, address, amounts, product, and so on.
- The current value only. If a value changed, give the new one and not the \
old one.
- Do not list a value the two sides disagree about, not even marked with who \
said it: list the dispute instead. Once one side concedes, list the value \
that stood.
- Copy values as written in the messages (numbers, names, addresses).

failed_steps — fixes or diagnostic steps that were tried and reported NOT to \
work. "suggested_seq": where it was suggested; "failed_seq": where the \
failure was reported.

disputes — disagreements still open: the customer states one value and the \
company another for the same thing, and neither has accepted the other's. \
Leave out a disagreement once one side has conceded. A value one side \
CHANGED (the company moved the customer to another plan, the customer gave a \
new address) is a change, not a dispute.

Return empty lists where there is nothing to report.\
"""


def instructions(cal: WorkCalendar) -> str:
    return INSTRUCTIONS.replace("{tz}", cal.tz.key)


def moment(at: datetime, cal: WorkCalendar) -> str:
    local = at.astimezone(cal.tz)
    return f"{_WEEKDAYS[local.weekday()]} {local:%Y-%m-%d %H:%M}"


def render_event(event: Event, cal: WorkCalendar) -> str:
    """One line per event, numbered by seq and stamped with local time."""
    head = f"#{event.seq} · {moment(event.at, cal)}"
    if isinstance(event, MessagePosted):
        name = f" ({event.author.name})" if event.author.name else ""
        note = "" if event.public else " [internal note]"
        return f"{head} · {event.author.role.value}{name}{note}: {event.text}"
    if isinstance(event, AssigneeChanged):
        return f"{head} · [ticket reassigned: {event.previous or 'nobody'} -> {event.current}]"
    if isinstance(event, StatusChanged):
        return f"{head} · [status changed: {event.previous} -> {event.current}]"
    return f"{head} · [{type(event).__name__}]"


def question(
    material_title: str, material: str, cp: Checkpoint, cal: WorkCalendar, now_last: bool = False
) -> list:
    """The messages for one checkpoint question, given what the method remembers.

    ``now_last`` puts the moment after the material, so that a question asked
    after every message shares its prefix with the one before and hits the
    provider's prompt cache; the checkpoint questions of Round 4 put it first.
    """
    now = f"NOW: {moment(cp.at, cal)} ({cal.tz.key}). Events up to #{cp.seq} have happened."
    material = f"{material_title}\n{material}"
    user = f"{material}\n\n{now}" if now_last else f"{now}\n\n{material}"
    return [{"role": "system", "content": instructions(cal)}, {"role": "user", "content": user}]


@dataclass(frozen=True, slots=True)
class Read:
    snapshot: Snapshot
    #: Entries that could not be read, with the reason. Counted, not hidden.
    malformed: tuple[str, ...] = ()


def parse_answer(raw: Mapping[str, Any], cp: Checkpoint, cal: WorkCalendar) -> Read:
    """Turn a model's answer into a snapshot. One bad entry costs only itself."""
    bad: list[str] = []
    promises: list[Promise] = []
    for item in _items(raw, "commitments", bad):
        try:
            due_text = item.get("due")
            promises.append(
                Promise(
                    side=Side.CUSTOMER if item["owner"] == "customer" else Side.COMPANY,
                    seq=int(item["source_seq"]),
                    action=str(item.get("action") or ""),
                    due_at=parse_due_at(due_text, cal) if due_text else None,
                    status=PromiseStatus(item.get("status") or "open"),
                    closed_seq=_int(item.get("closed_seq")),
                )
            )
        except (KeyError, ValueError, TypeError) as error:
            bad.append(f"commitment {item!r}: {error}")
    facts = [
        FactItem(str(i.get("name") or ""), str(i.get("value") or ""), _int(i.get("source_seq")))
        for i in _items(raw, "current_facts", bad)
    ]
    steps = [
        FailedStep(str(i.get("action") or ""), _int(i.get("suggested_seq")),
                   _int(i.get("failed_seq")))
        for i in _items(raw, "failed_steps", bad)
    ]  # fmt: skip
    disputes = [
        Dispute(
            str(i.get("topic") or ""),
            (str(i.get("customer_says") or ""), str(i.get("company_says") or "")),
            tuple(s for s in (_int(i.get("customer_seq")), _int(i.get("company_seq"))) if s),
        )
        for i in _items(raw, "disputes", bad)
    ]
    snap = Snapshot(cp, tuple(promises), tuple(facts), tuple(steps), tuple(disputes))
    return Read(snap, tuple(bad))


_DUE = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2})(?:[T ](?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?"
    r"(?:\.\d+)?(?P<tz>Z|[+-]\d{2}:?\d{2})?)?\s*$"
)


def parse_due_at(text: str, cal: WorkCalendar) -> datetime:
    """An absolute deadline as a baseline states it. Date only means end of that day."""
    m = _DUE.match(text)
    if not m:
        raise ValueError(f"unreadable deadline {text!r}")
    day = date.fromisoformat(m.group("date"))
    if m.group("h") is None:
        return cal.end_of(day)
    at = time(int(m.group("h")), int(m.group("m")), int(m.group("s") or 0))
    zone = m.group("tz")
    if zone:
        return datetime.fromisoformat(f"{day.isoformat()}T{at.isoformat()}{zone}")
    return datetime.combine(day, at, tzinfo=cal.tz)


def _items(raw: Mapping[str, Any], key: str, bad: list[str]) -> Sequence[Mapping[str, Any]]:
    items = raw.get(key) or []
    if not isinstance(items, list):
        bad.append(f"{key} is not a list")
        return []
    good = [i for i in items if isinstance(i, Mapping)]
    bad += [f"{key} entry is not an object: {i!r}" for i in items if not isinstance(i, Mapping)]
    return good


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
