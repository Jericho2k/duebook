"""Why a method's deadlines were wrong: arithmetic, or a convention it was not told.

"До конца недели" has two honest readings: the end of the working week
(Friday — what ``temporal.resolve`` uses, and so the gold label) or the end of
the calendar week (Sunday). The baselines' question says a day-only deadline
means the end of that day, but not which day ends a week. Counting a Sunday
answer as wrong would charge a baseline for a convention, so the analysis
here separates the two: a deadline is *right under either convention* when it
matches the gold instant, or when the phrase was "end of week" and the answer
is the end of that week's Sunday.

Everything else that is wrong is wrong on arithmetic: business days counted
wrongly, a weekday placed in the wrong week.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from ..temporal import WorkCalendar
from .generate import SimTicket
from .methods import MethodRun
from .score import match, same_deadline


def classify(
    tickets: Sequence[SimTicket], runs: Sequence[MethodRun], cal: WorkCalendar | None = None
) -> dict[str, int]:
    """Counts at the end of each ticket: right, convention, arithmetic, missing, invented."""
    cal = cal or WorkCalendar()
    out = {"right": 0, "convention": 0, "arithmetic": 0, "missing": 0, "invented": 0}
    by_id = {t.ticket_id: t for t in tickets}
    for run in runs:
        ticket = by_id[run.ticket_id]
        end = run.snapshots[-1]
        gold = [c for c in ticket.gold.commitments if c.seq <= end.checkpoint.seq]
        for g, f in match(gold, end.promises)[0]:
            if same_deadline(g.due_at, f.due_at):
                out["right"] += 1
            elif f.due_at is None:
                out["missing"] += 1
            elif g.due_at is None:
                out["invented"] += 1
            elif _calendar_week_end(ticket, g.seq, g.due_at, f.due_at, cal):
                out["convention"] += 1
            else:
                out["arithmetic"] += 1
    return out


def _calendar_week_end(ticket: SimTicket, seq: int, gold, found, cal: WorkCalendar) -> bool:
    text = getattr(ticket.events[seq - 1], "text", "").lower()
    if "конца недели" not in text and "end of week" not in text:
        return False
    friday = gold.astimezone(cal.tz).date()
    sunday = friday + timedelta(days=6 - friday.weekday())
    return same_deadline(found, cal.end_of(sunday))
