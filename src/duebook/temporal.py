"""Deadlines: from "by Friday" to an instant, without letting a model do arithmetic.

A language model is good at recognising that "до пятницы" is a deadline and bad
at computing which date that is. So the two jobs are split:

* the model (or the rule parser below) produces a ``DueExpr`` — a small
  structured description of what was said;
* ``resolve`` turns it into an instant, using the time the message was sent,
  the ticket's timezone and its working calendar.

The model never states an absolute time. Because there are two independent
readers of the same phrase — the model and ``parse_due`` — their agreement is a
free confidence signal, and the alerting layer uses it (see ``lifecycle``).

A deadline is resolved to the LATEST PLAUSIBLE moment the speaker could have
meant ("by Friday" is the end of Friday, not its start). A breach alert that
fires early is a false alarm, and false alarms are what make people stop
reading alerts.
"""

from __future__ import annotations

import calendar as _calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class Unit(StrEnum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    BUSINESS_DAY = "business_day"


class Period(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class Offset:
    """ "in 2 hours", "через неделю", "в течение 3 рабочих дней"."""

    amount: int
    unit: Unit


@dataclass(frozen=True, slots=True)
class DayRef:
    """ "today" (0), "tomorrow" (1), "послезавтра" (2), optionally at a time."""

    days_ahead: int
    at: time | None = None


@dataclass(frozen=True, slots=True)
class WeekdayRef:
    """A weekday, Monday = 0.

    With ``week`` unset it is the next occurrence ("by Friday"). ``week=1`` is
    that weekday in the following calendar week ("Friday next week").
    """

    weekday: int
    at: time | None = None
    week: int | None = None


@dataclass(frozen=True, slots=True)
class DateRef:
    """A calendar date; the year is inferred as the next occurrence when absent."""

    day: int
    month: int
    year: int | None = None
    at: time | None = None


@dataclass(frozen=True, slots=True)
class PeriodEnd:
    """ "by end of day / week / month"."""

    period: Period


DueExpr = Offset | DayRef | WeekdayRef | DateRef | PeriodEnd


class Precision(StrEnum):
    #: The speaker named a moment ("by 17:00", "in two hours").
    TIME = "time"
    #: The speaker named a day; the instant is that day's end.
    DAY = "day"


@dataclass(frozen=True, slots=True)
class Deadline:
    due_at: datetime
    precision: Precision


@dataclass(frozen=True, slots=True)
class WorkCalendar:
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Moscow"))
    workdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    #: When a day-precision deadline lapses. End of the calendar day by default.
    day_end: time = time(23, 59, 59)
    holidays: frozenset[date] = frozenset()

    def is_workday(self, day: date) -> bool:
        return day.weekday() in self.workdays and day not in self.holidays

    def end_of(self, day: date) -> datetime:
        return datetime.combine(day, self.day_end, tzinfo=self.tz)

    def add_workdays(self, start: date, count: int) -> date:
        day = start
        remaining = count
        while remaining > 0:
            day += timedelta(days=1)
            if self.is_workday(day):
                remaining -= 1
        return day

    def last_workday_on_or_before(self, day: date) -> date:
        for _ in range(366):
            if self.is_workday(day):
                return day
            day -= timedelta(days=1)
        raise ValueError("calendar has no working days")


def resolve(expr: DueExpr, said_at: datetime, cal: WorkCalendar) -> Deadline:
    """Turn what was said into an instant. Raises ValueError on an impossible date."""
    if said_at.tzinfo is None:
        raise ValueError("said_at must be timezone-aware")
    local = said_at.astimezone(cal.tz)
    today = local.date()

    def on(day: date, at: time | None) -> Deadline:
        if at is None:
            return _deadline(cal.end_of(day), Precision.DAY)
        return _deadline(datetime.combine(day, at, tzinfo=cal.tz), Precision.TIME)

    match expr:
        case Offset(amount=amount, unit=unit):
            if amount < 0:
                raise ValueError("offset must not be negative")
            if unit is Unit.MINUTE:
                return _deadline(said_at + timedelta(minutes=amount), Precision.TIME)
            if unit is Unit.HOUR:
                return _deadline(said_at + timedelta(hours=amount), Precision.TIME)
            if unit is Unit.BUSINESS_DAY:
                return on(cal.add_workdays(today, amount), None)
            days = amount * 7 if unit is Unit.WEEK else amount
            return on(today + timedelta(days=days), None)

        case DayRef(days_ahead=days_ahead, at=at):
            if days_ahead < 0:
                raise ValueError("day reference must not point backwards")
            return on(today + timedelta(days=days_ahead), at)

        case WeekdayRef(weekday=weekday, at=at, week=week):
            if not 0 <= weekday <= 6:
                raise ValueError("weekday must be 0..6")
            if week is not None:
                if week < 0:
                    raise ValueError("week must not point backwards")
                monday = today - timedelta(days=today.weekday())
                return on(monday + timedelta(days=7 * week + weekday), at)
            ahead = (weekday - today.weekday()) % 7
            if ahead == 0 and on(today, at).due_at <= said_at:
                ahead = 7
            return on(today + timedelta(days=ahead), at)

        case DateRef(day=day, month=month, year=year, at=at):
            if year is not None:
                return on(date(year, month, day), at)
            candidate = on(date(today.year, month, day), at)
            if candidate.due_at <= said_at:
                candidate = on(date(today.year + 1, month, day), at)
            return candidate

        case PeriodEnd(period=period):
            if period is Period.DAY:
                return on(today, None)
            if period is Period.WEEK:
                sunday = today + timedelta(days=6 - today.weekday())
                last = cal.last_workday_on_or_before(sunday)
                if last < today:  # said on a weekend: the working week is over
                    last = cal.last_workday_on_or_before(sunday + timedelta(days=7))
                return on(last, None)
            month_end = date(
                today.year, today.month, _calendar.monthrange(today.year, today.month)[1]
            )
            last = cal.last_workday_on_or_before(month_end)
            if last < today:
                last = month_end
            return on(last, None)

    raise TypeError(f"not a due expression: {expr!r}")


def _deadline(moment: datetime, precision: Precision) -> Deadline:
    return Deadline(due_at=moment.astimezone(UTC), precision=precision)


# --------------------------------------------------------------------------
# Rule parser: an independent, deterministic reading of the same phrase.
#
# Deliberately modest. It does not need to understand every phrasing — the
# model does that. It needs to be RIGHT when it does answer, because its job is
# to confirm or dispute the model's reading.
# --------------------------------------------------------------------------

_NUMBER_WORDS = {
    "один": 1, "одного": 1, "одну": 1, "одной": 1, "одни": 1,
    "два": 2, "две": 2, "двух": 2, "пару": 2, "пары": 2, "пара": 2,
    "три": 3, "трех": 3, "четыре": 4, "четырех": 4, "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8,
    "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
    "a": 1, "an": 1, "one": 1, "two": 2, "couple of": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip
_NUM = (
    r"(?P<num>\d+|" + "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True)) + r")"
)
_UNIT = (
    r"(?P<unit>минут\w*|мин\b|час\w*|дн\w+|день|сут\w+|недел\w+|"
    r"minutes?|mins?\b|hours?|hrs?\b|days?|weeks?)"
)
_BUSINESS = r"(?P<biz>рабоч\w+|business|working)"

_OFFSET = re.compile(
    rf"(?:через|в течени[еи]|за|in|within)\s+(?:{_NUM}\s+)?(?:{_BUSINESS}\s+)?{_UNIT}"
)
_HALF_HOUR = re.compile(r"(?:через|в течени[еи]|in|within)\s+(?:полчаса|half an hour)")
_HOUR_AND_HALF = re.compile(
    r"(?:через|в течени[еи]|in|within)\s+(?:полтора часа|an hour and a half|1[.,]5 (?:часа|hours))"
)
# Phrases the rules cannot place with certainty. "В следующую пятницу" may mean
# the coming Friday or the one after; "Friday or Monday" names two deadlines.
# The parser answers nothing rather than guess, because a wrong second opinion
# disputes a correct first one.
_AMBIGUOUS = re.compile(
    r"следующ|ближайш|\bnext\b|\bэт(?:у|от|о|ой)\b|\bthis\b|\bили\b|\bor\b|примерно|около|"
    r"\babout\b|\baround\b|не раньше|не ранее|\bпосле\b|\bafter\b|"
    r"(?<![\d-])\d{1,2}\s*[-–—]\s*\d{1,2}(?![\d-])"
)
_PERIOD_END = re.compile(
    r"(?:кон(?:ца|ец|цу)\s+(?:рабочего\s+)?(?P<ru>дня|недели|месяца)"
    r"|end of (?:the\s+)?(?:business\s+|working\s+)?(?P<en>day|week|month)"
    r"|\b(?P<abbr>eod|eow)\b)"
)
_PERIODS = {
    "дня": Period.DAY, "недели": Period.WEEK, "месяца": Period.MONTH,
    "day": Period.DAY, "week": Period.WEEK, "month": Period.MONTH,
    "eod": Period.DAY, "eow": Period.WEEK,
}  # fmt: skip

_MONTHS_RU = ["январ", "феврал", "март", "апрел", "ма[йяюе]", "июн", "июл", "август",
              "сентябр", "октябр", "ноябр", "декабр"]  # fmt: skip
_MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DATE_RU = re.compile(
    r"\b(?P<day>\d{1,2})(?:-?го)?\s+(?P<month>"
    + "|".join(_MONTHS_RU)
    + r")\w*(?:\s+(?P<year>\d{4}))?"
)
_DATE_EN = re.compile(
    r"\b(?P<month>" + "|".join(_MONTHS_EN) + r")[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>\d{4}))?"
)
_DATE_ISO = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_DATE_DOT = re.compile(r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})(?:\.(?P<year>\d{4}|\d{2}))?\b")

_DAY_WORDS = [
    (re.compile(r"послезавтра|day after tomorrow"), 2),
    (re.compile(r"завтра|tomorrow"), 1),
    (re.compile(r"сегодня|today|tonight"), 0),
]
_WEEKDAYS = [
    r"понедельник\w*|monday|\bmon\b",
    r"вторник\w*|tuesday|\btue\b",
    r"сред[аеуыой]+\b|wednesday|\bwed\b",
    r"четверг\w*|thursday|\bthu\b",
    r"пятниц\w*|friday|\bfri\b",
    r"суббот\w*|saturday|\bsat\b",
    r"воскресень\w*|sunday|\bsun\b",
]
_WEEKDAY = [re.compile(pattern) for pattern in _WEEKDAYS]

_CLOCK = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\b(?:\s*(?P<ampm>am|pm)\b)?")
_CLOCK_AMPM = re.compile(r"\b(?P<h>\d{1,2})\s*(?P<ampm>am|pm)\b")
_CLOCK_RU = re.compile(r"(?:\bв|\bдо|\bк)\s+(?P<h>\d{1,2})\s*(?:час\w*|ч\b)")


def parse_due(text: str) -> DueExpr | None:
    """Read a deadline phrase in Russian or English. None when unsure."""
    s = re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()
    if not s:
        return None

    if _AMBIGUOUS.search(s):
        return None
    if _HALF_HOUR.search(s):
        return Offset(30, Unit.MINUTE)
    if _HOUR_AND_HALF.search(s):
        return Offset(90, Unit.MINUTE)
    if m := _OFFSET.search(s):
        raw = m.group("num")
        amount = 1 if raw is None else int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        unit = _unit(m.group("unit"))
        if m.group("biz"):
            if unit is not Unit.DAY:
                return None
            unit = Unit.BUSINESS_DAY
        return Offset(amount, unit)

    if m := _PERIOD_END.search(s):
        return PeriodEnd(_PERIODS[m.group("ru") or m.group("en") or m.group("abbr")])

    at = _clock(s)

    for pattern, months in ((_DATE_RU, _MONTHS_RU), (_DATE_EN, _MONTHS_EN)):
        if m := pattern.search(s):
            month = next(i for i, stem in enumerate(months, 1) if re.match(stem, m.group("month")))
            year = int(m.group("year")) if m.group("year") else None
            return DateRef(int(m.group("day")), month, year, at)
    if m := _DATE_ISO.search(s):
        return DateRef(int(m.group("day")), int(m.group("month")), int(m.group("year")), at)
    if m := _DATE_DOT.search(s):
        month = int(m.group("month"))
        if 1 <= month <= 12:
            year = int(m.group("year")) if m.group("year") else None
            if year is not None and year < 100:
                year += 2000
            return DateRef(int(m.group("day")), month, year, at)

    for pattern, days_ahead in _DAY_WORDS:
        if pattern.search(s):
            return DayRef(days_ahead, at)
    for weekday, pattern in enumerate(_WEEKDAY):
        if pattern.search(s):
            return WeekdayRef(weekday, at)

    if at is not None:
        return DayRef(0, at)
    return None


def _unit(word: str) -> Unit:
    if word.startswith(("мин", "min")):
        return Unit.MINUTE
    if word.startswith(("час", "hour", "hr")):
        return Unit.HOUR
    if word.startswith(("недел", "week")):
        return Unit.WEEK
    return Unit.DAY


def _clock(s: str) -> time | None:
    for pattern in (_CLOCK, _CLOCK_AMPM, _CLOCK_RU):
        if m := pattern.search(s):
            groups = m.groupdict()
            hour = int(groups["h"])
            minute = int(groups.get("m") or 0)
            ampm = groups.get("ampm")
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if hour < 24 and minute < 60:
                return time(hour, minute)
    return None
