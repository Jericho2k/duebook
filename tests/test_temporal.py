from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from helpers import CAL, MSK, START

from duebook.temporal import (
    DateRef,
    DayRef,
    Offset,
    Period,
    PeriodEnd,
    Precision,
    Unit,
    WeekdayRef,
    WorkCalendar,
    parse_due,
    resolve,
)


def local(deadline):
    return deadline.due_at.astimezone(MSK)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("перезвоню завтра", DayRef(1)),
        ("ответим послезавтра", DayRef(2)),
        ("сегодня до 17:00", DayRef(0, time(17, 0))),
        ("завтра в 15 часов", DayRef(1, time(15, 0))),
        ("вернём деньги до пятницы", WeekdayRef(4)),
        ("к среде всё будет", WeekdayRef(2)),
        ("во вторник в 9:30", WeekdayRef(1, time(9, 30))),
        ("через 2 часа", Offset(2, Unit.HOUR)),
        ("через час", Offset(1, Unit.HOUR)),
        ("через полчаса", Offset(30, Unit.MINUTE)),
        ("через полтора часа", Offset(90, Unit.MINUTE)),
        ("через пару дней", Offset(2, Unit.DAY)),
        ("в течение 3 рабочих дней", Offset(3, Unit.BUSINESS_DAY)),
        ("в течении трёх рабочих дней", Offset(3, Unit.BUSINESS_DAY)),
        ("через неделю", Offset(1, Unit.WEEK)),
        ("до конца дня", PeriodEnd(Period.DAY)),
        ("до конца рабочего дня", PeriodEnd(Period.DAY)),
        ("до конца недели", PeriodEnd(Period.WEEK)),
        ("до конца месяца", PeriodEnd(Period.MONTH)),
        ("15 марта", DateRef(15, 3)),
        ("до 1 февраля 2027", DateRef(1, 2, 2027)),
        ("до 15.03", DateRef(15, 3)),
        ("до 15.03.2027", DateRef(15, 3, 2027)),
        ("by 2027-03-15", DateRef(15, 3, 2027)),
        ("I'll call you back tomorrow", DayRef(1)),
        ("by Friday 5 pm", WeekdayRef(4, time(17, 0))),
        ("within 2 business days", Offset(2, Unit.BUSINESS_DAY)),
        ("in an hour", Offset(1, Unit.HOUR)),
        ("by end of day", PeriodEnd(Period.DAY)),
        ("March 15th", DateRef(15, 3)),
    ],
)
def test_parse_due(phrase, expected):
    assert parse_due(phrase) == expected


@pytest.mark.parametrize("phrase", ["", "мы разберёмся", "как можно скорее", "we'll look into it"])
def test_parser_stays_silent_when_no_deadline_was_stated(phrase):
    assert parse_due(phrase) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "в следующую пятницу",
        "next Friday",
        "на следующей неделе",
        "в эту пятницу или в понедельник",
        "через 2-3 дня",
        "примерно через час",
        "не раньше среды",
        "после 15 марта",
    ],
)
def test_parser_stays_silent_on_phrases_it_cannot_place_with_certainty(phrase):
    assert parse_due(phrase) is None


def test_weekday_in_a_named_week_is_counted_from_that_weeks_monday():
    # Said on Monday 11 January: "Friday next week" is the 22nd, not the 15th.
    assert local(resolve(WeekdayRef(4, week=1), START, CAL)).date() == date(2027, 1, 22)
    saturday = datetime(2027, 1, 16, 12, 0, tzinfo=MSK)
    assert local(resolve(WeekdayRef(4, week=1), saturday, CAL)).date() == date(2027, 1, 22)


def test_business_qualifier_only_applies_to_days():
    assert parse_due("в течение 3 рабочих часов") is None


def test_day_precision_resolves_to_the_end_of_that_day():
    deadline = resolve(DayRef(1), START, CAL)
    assert deadline.precision is Precision.DAY
    assert local(deadline) == datetime(2027, 1, 12, 23, 59, 59, tzinfo=MSK)


def test_by_friday_means_the_end_of_friday_not_its_start():
    assert local(resolve(WeekdayRef(4), START, CAL)).date() == date(2027, 1, 15)


def test_weekday_named_on_that_weekday_is_today_while_the_day_lasts():
    monday = resolve(WeekdayRef(0), START, CAL)
    assert local(monday).date() == START.date()


def test_weekday_at_a_time_already_past_rolls_to_next_week():
    deadline = resolve(WeekdayRef(0, time(9, 0)), START, CAL)
    assert local(deadline) == datetime(2027, 1, 18, 9, 0, tzinfo=MSK)


def test_hour_offset_is_exact():
    deadline = resolve(Offset(2, Unit.HOUR), START, CAL)
    assert deadline.precision is Precision.TIME
    assert local(deadline) == datetime(2027, 1, 11, 12, 0, tzinfo=MSK)


def test_business_days_skip_weekends_and_holidays():
    thursday = datetime(2027, 1, 14, 16, 0, tzinfo=MSK)
    assert local(resolve(Offset(3, Unit.BUSINESS_DAY), thursday, CAL)).date() == date(2027, 1, 19)

    holiday = WorkCalendar(tz=MSK, holidays=frozenset({date(2027, 1, 15)}))
    assert local(resolve(Offset(1, Unit.BUSINESS_DAY), thursday, holiday)).date() == date(
        2027, 1, 18
    )


def test_end_of_week_is_the_last_working_day():
    assert local(resolve(PeriodEnd(Period.WEEK), START, CAL)).date() == date(2027, 1, 15)
    saturday = datetime(2027, 1, 16, 12, 0, tzinfo=MSK)
    assert local(resolve(PeriodEnd(Period.WEEK), saturday, CAL)).date() == date(2027, 1, 22)


def test_end_of_week_follows_the_calendars_working_week():
    cal = WorkCalendar(ZoneInfo("Asia/Amman"), frozenset({6, 0, 1, 2, 3}))
    sunday = datetime(2027, 1, 17, 12, tzinfo=cal.tz)
    friday = datetime(2027, 1, 22, 12, tzinfo=cal.tz)
    expr = PeriodEnd(Period.WEEK)
    assert resolve(expr, sunday, cal).due_at.astimezone(cal.tz).date() == date(2027, 1, 21)
    assert resolve(expr, friday, cal).due_at.astimezone(cal.tz).date() == date(2027, 1, 28)
    next_thursday = resolve(WeekdayRef(3, week=1), sunday, cal)
    assert next_thursday.due_at.astimezone(cal.tz).date() == date(2027, 1, 28)


def test_end_of_month_is_the_last_working_day():
    # 31 January 2027 is a Sunday.
    assert local(resolve(PeriodEnd(Period.MONTH), START, CAL)).date() == date(2027, 1, 29)


def test_date_without_year_is_the_next_occurrence():
    assert local(resolve(DateRef(15, 3), START, CAL)).year == 2027
    assert local(resolve(DateRef(5, 1), START, CAL)).year == 2028


def test_impossible_date_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        resolve(DateRef(31, 2), START, CAL)


def test_resolution_respects_the_ticket_timezone():
    # 23:30 UTC on Monday is already Tuesday in Moscow, so "tomorrow" is Wednesday.
    from datetime import UTC

    late = datetime(2027, 1, 11, 23, 30, tzinfo=UTC)
    assert local(resolve(DayRef(1), late, CAL)).date() == date(2027, 1, 13)


def test_naive_time_is_refused():
    with pytest.raises(ValueError):
        resolve(DayRef(1), datetime(2027, 1, 11, 10, 0), CAL)
