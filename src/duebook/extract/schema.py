"""The contract between the model and the reducer.

``OUTPUT_SCHEMA`` is what the model is asked to produce; ``parse_output`` turns
it into proposals. The schema is flat and every field is required (nullable
where optional), which is the form strict structured-output modes accept across
providers.

Parsing is per item: one malformed entry is dropped and counted, and the rest
of the response is still used. A model that garbles one field should not cost
the ticket everything else it noticed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any

from ..card import Party, StepOutcome
from ..proposals import (
    AnswerQuestion,
    AskQuestion,
    AssertFact,
    CancelCommitment,
    MakeCommitment,
    Proposal,
    RecordStep,
    ResolveCommitment,
    SetStepOutcome,
)
from ..temporal import DateRef, DayRef, DueExpr, Offset, Period, PeriodEnd, Unit, WeekdayRef

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_STR = {"type": "string"}
_OPT_STR = {"type": ["string", "null"]}
_OPT_INT = {"type": ["integer", "null"]}


def _enum(values: list[str], nullable: bool = False) -> dict[str, Any]:
    if nullable:
        return {"type": ["string", "null"], "enum": [*values, None]}
    return {"type": "string", "enum": values}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _list(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": _obj(properties)}


_DUE = _obj(
    {
        "kind": _enum(["none", "offset", "day", "weekday", "date", "period_end"]),
        "amount": _OPT_INT,
        "unit": _enum([u.value for u in Unit], nullable=True),
        "days_ahead": _OPT_INT,
        "weekday": _enum(WEEKDAYS, nullable=True),
        "week": _OPT_INT,
        "day": _OPT_INT,
        "month": _OPT_INT,
        "year": _OPT_INT,
        "period": _enum([p.value for p in Period], nullable=True),
        "time": _OPT_STR,
    }
)

OUTPUT_SCHEMA: dict[str, Any] = _obj(
    {
        "facts": _list({"slot": _STR, "value": _STR, "quote": _STR, "change": {"type": "boolean"}}),
        "commitments": _list(
            {
                "owner": _enum([p.value for p in Party]),
                "action": _STR,
                "quote": _STR,
                "due_quote": _OPT_STR,
                "due": _DUE,
                "supersedes": _OPT_STR,
                "target_quote": _OPT_STR,
            }
        ),
        "resolved": _list({"id": _STR, "target_quote": _STR, "quote": _STR,
                           "context_quote": _OPT_STR}),  # fmt: skip
        "cancelled": _list({"id": _STR, "target_quote": _STR, "quote": _STR}),
        "steps": _list(
            {"action": _STR, "quote": _STR, "outcome": _enum([o.value for o in StepOutcome])}
        ),
        "step_outcomes": _list({"id": _STR, "outcome": _enum(["worked", "failed"]), "quote": _STR}),
        "questions": _list({"text": _STR, "quote": _STR}),
        "answers": _list({"id": _STR, "quote": _STR}),
    }
)


@dataclass(frozen=True, slots=True)
class Malformed:
    """An entry the model produced that could not be read."""

    section: str
    item: Any
    error: str


def parse_output(
    raw: Mapping[str, Any], aliases: Mapping[str, str] | None = None
) -> tuple[list[Proposal], list[Malformed]]:
    """Turn a model response into proposals.

    ``aliases`` maps the short handles shown to the model (C1, S2, Q1) back to
    real ids. An unknown handle is passed through, and the reducer refuses it
    as an unknown target — a hallucinated reference is a rejection to count,
    not an error to hide.
    """
    aliases = aliases or {}

    def ref(value: Any) -> str:
        key = _text(value)
        return aliases.get(key, aliases.get(key.upper(), key))

    builders = {
        "facts": lambda i: AssertFact(
            _text(i["slot"]), _text(i["value"]), _text(i["quote"]), bool(i.get("change", False))
        ),
        "steps": lambda i: RecordStep(
            _text(i["action"]), _text(i["quote"]), StepOutcome(i.get("outcome") or "pending")
        ),
        "step_outcomes": lambda i: SetStepOutcome(
            ref(i["id"]), StepOutcome(i["outcome"]), _text(i["quote"])
        ),
        "answers": lambda i: AnswerQuestion(ref(i["id"]), _text(i["quote"])),
        "resolved": lambda i: ResolveCommitment(
            ref(i["id"]), _text(i["quote"]), _text(i.get("target_quote") or ""),
            context_quote=_text(i.get("context_quote") or ""),
        ),
        "cancelled": lambda i: CancelCommitment(
            ref(i["id"]), _text(i["quote"]), _text(i.get("target_quote") or "")
        ),
        "commitments": lambda i: MakeCommitment(
            owner=Party(i["owner"]),
            action=_text(i["action"]),
            quote=_text(i["quote"]),
            due=parse_due_expr(i.get("due")),
            due_quote=_text(i.get("due_quote") or ""),
            supersedes=ref(i["supersedes"]) if i.get("supersedes") else None,
            target_quote=_text(i.get("target_quote") or ""),
        ),
        "questions": lambda i: AskQuestion(_text(i["text"]), _text(i["quote"])),
    }

    proposals: list[Proposal] = []
    malformed: list[Malformed] = []
    for section, build in builders.items():
        items = raw.get(section) or []
        if not isinstance(items, list):
            malformed.append(Malformed(section, items, "not a list"))
            continue
        for item in items:
            try:
                proposals.append(build(item))
            except (KeyError, ValueError, TypeError, AttributeError) as error:
                malformed.append(Malformed(section, item, f"{type(error).__name__}: {error}"))
    return proposals, malformed


def parse_due_expr(raw: Any) -> DueExpr | None:
    """Read the model's structured deadline. Raises ValueError when it is malformed."""
    if raw is None:
        return None
    kind = raw.get("kind") or "none"
    at = _time(raw.get("time"))
    match kind:
        case "none":
            return None
        case "offset":
            return Offset(_int(raw, "amount"), Unit(raw["unit"]))
        case "day":
            return DayRef(_int(raw, "days_ahead"), at)
        case "weekday":
            week = raw.get("week")
            return WeekdayRef(WEEKDAYS.index(raw["weekday"]), at, int(week) if week else None)
        case "date":
            year = raw.get("year")
            return DateRef(_int(raw, "day"), _int(raw, "month"), int(year) if year else None, at)
        case "period_end":
            return PeriodEnd(Period(raw["period"]))
    raise ValueError(f"unknown due kind: {kind!r}")


def _int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if value is None or isinstance(value, bool):
        raise ValueError(f"{key} is required")
    return int(value)


def _time(value: Any) -> time | None:
    if not value:
        return None
    hour, _, minute = str(value).partition(":")
    return time(int(hour), int(minute or 0))


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected text, got {type(value).__name__}")
    return value.strip()
