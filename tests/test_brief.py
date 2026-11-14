import re
from datetime import timedelta

import pytest
from helpers import ANNA, BOT, CAL, IVAN, OLGA, START, Ticket, story

from duebook import Card, Party
from duebook.brief import Brief, compose, estimate_tokens
from duebook.proposals import MakeCommitment, ResolveCommitment
from duebook.reducer import _norm
from duebook.temporal import WeekdayRef

NAMES = {IVAN.id: "Иван", OLGA.id: "Ольга", ANNA.id: "Анна"}
TUESDAY_NOON = START + timedelta(days=1, hours=2)
SUNDAY = START + timedelta(days=6)
REF = re.compile(r"\[№(\d+) ([^:\]]+): «([^»]*)»\]")


def brief(card, now=TUESDAY_NOON, **options) -> Brief:
    return compose(card, now, cal=CAL, names=NAMES, **options)


def titles(b: Brief) -> list[str]:
    return [s.key for s in b.sections]


def items(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("- ")]


# ---------------------------------------------------------------- order ----


def test_sections_come_in_order_of_what_hurts_most_if_missed():
    assert titles(brief(story().card)) == [
        "urgent", "moved", "contradictions", "facts", "changes", "failed", "questions",
    ]  # fmt: skip


def test_broken_promises_lead_and_the_company_s_come_before_the_customer_s():
    urgent = brief(story().card, SUNDAY).sections[0]
    assert [line.text.split(";")[0] for line in urgent.lines] == [
        "ПРОСРОЧЕНО на 4 дн 19 ч (срок был до вт 12.01 15:00)",
        "ПРОСРОЧЕНО на 1 дн 10 ч (срок был до конца пт 15.01)",
        "ПРОСРОЧЕНО на 5 дн 10 ч (срок был до конца пн 11.01)",
    ]
    assert "клиент: прислать скриншот" in urgent.lines[2].text


def test_a_promise_about_to_lapse_says_how_long_is_left():
    (soon,) = [line for line in brief(story().card).sections[0].lines if "осталось" in line.text]
    assert soon.text.startswith("осталось 3 ч (срок до вт 12.01 15:00); агент: перезвонить")


def test_an_inherited_promise_names_who_answers_now_and_who_did_before():
    (line,) = brief(story().card).sections[1].lines
    assert "компания: вернуть деньги" in line.text
    assert "отвечает Ольга; раньше отвечал: Иван" in line.text


def test_a_promise_nobody_answers_for_is_marked_and_listed_first():
    t = story()
    t.say(BOT, "Мы пришлём вам промокод.", MakeCommitment(Party.COMPANY, "прислать промокод",
                                                          "Мы пришлём вам промокод"))  # fmt: skip
    t.assign(OLGA.id, None)
    moved = brief(t.card, START + timedelta(hours=1)).sections[0]
    assert moved.key == "moved" and all("БЕЗ ОТВЕТСТВЕННОГО" in line.text for line in moved.lines)
    assert "учтено событий: 9" in brief(t.card).text


def test_a_promise_appears_once_under_the_most_urgent_heading_it_qualifies_for():
    text = brief(story().card, SUNDAY).text
    assert text.count("вернуть деньги") == 1 and "moved" not in titles(brief(story().card, SUNDAY))


def test_a_claim_of_completion_is_shown_as_unconfirmed_with_its_own_source():
    t = story()
    t.say(OLGA, "Перезвонила клиенту.", ResolveCommitment("e5#2", "Перезвонила клиенту"))
    (line,) = [x for x in items(brief(t.card).text) if "перезвонить" in x]
    assert "заявлено как выполненное, не подтверждено [№8 Ольга: «Перезвонила клиенту»]" in line
    assert "ПРОСРОЧЕНО" not in line and "осталось" not in line


def test_a_deadline_the_two_readers_disagreed_on_is_flagged_not_hidden():
    t = Ticket()
    t.say(IVAN, "Вернём деньги до пятницы.",
          MakeCommitment(Party.COMPANY, "вернуть деньги", "Вернём деньги до пятницы",
                         due=WeekdayRef(0), due_quote="до пятницы"))  # fmt: skip
    assert "срок распознан неуверенно" in brief(t.card, START).text


# -------------------------------------------------------------- content ----


def test_a_dispute_shows_both_sides_and_is_not_also_listed_as_a_fact():
    b = brief(story().card)
    (dispute,) = next(s for s in b.sections if s.key == "contradictions").lines
    assert dispute.text == (
        "order.status: клиент: «не пришёл» [№2 Анна: «он не пришёл»] — "
        "компания: «вручён» [№3 Иван: «заказ вручён»]"
    )
    facts = next(s for s in b.sections if s.key == "facts")
    assert not any("order.status" in line.text for line in facts.lines)


def test_a_change_reads_was_x_now_y_with_both_sources():
    (change,) = next(s for s in brief(story().card).sections if s.key == "changes").lines
    assert change.text == (
        "account.plan: было «Базовый» [№2 Анна: «Тариф Базовый»], "
        "стало «Про» [№5 Иван: «на тариф Про»]"
    )


def test_only_failed_steps_and_open_questions_are_listed():
    text = brief(story().card).text
    assert "- перезайти в приложение [№4 Анна: «Перезашла, не помогло»]" in text
    assert "Когда именно придут деньги? (ответа ждём от: компания)" in text
    assert "Какой адрес доставки?" not in text  # answered in message 6


def test_the_header_cites_the_events_that_set_assignee_and_status():
    assert brief(story().card).text.splitlines()[0] == (
        "Тикет T-1 на вт 12.01 12:00 · ответственный: Ольга [событие №7] · учтено событий: 7"
    )


def test_an_empty_card_says_so_instead_of_rendering_nothing():
    text = brief(Card("T-9"), START).text
    assert text == "Тикет T-9 на пн 11.01 10:00 · учтено событий: 0\n\n" + (
        "По тикету пока ничего не зафиксировано."
    )


def test_the_same_card_at_the_same_moment_gives_the_same_text():
    assert brief(story().card).text == brief(story().card).text


# -------------------------------------------------------------- sources ----


@pytest.mark.parametrize("now", [START + timedelta(hours=1), TUESDAY_NOON, SUNDAY])
def test_every_line_cites_a_message_and_the_quote_is_really_in_it(now):
    t = story()
    messages = {e.seq: (e.author.id, _norm(e.text)) for e, _ in t.log if hasattr(e, "text")}
    b = brief(t.card, now)
    lines = items(b.text)
    assert len(lines) == sum(len(s.lines) for s in b.sections) >= 9
    for line in lines:
        refs = REF.findall(line)
        assert refs, f"no source on: {line}"
        for seq, who, quote in refs:
            author, text = messages[int(seq)]
            assert NAMES[author] == who and _norm(quote) in text


def test_each_line_carries_its_sources_as_data_too():
    for section in brief(story().card).sections:
        for line in section.lines:
            assert line.sources and all(f"№{s.seq} " in line.text for s in line.sources)


def test_a_long_quote_is_cut_and_still_a_prefix_of_what_was_said():
    b = brief(story().card, quote_chars=10)
    assert "[№5 Иван: «Вернём ден…»]" in b.text


# --------------------------------------------------------------- budget ----


def test_without_a_budget_nothing_is_dropped():
    b = brief(story().card)
    assert b.complete and b.fits and b.tokens == estimate_tokens(b.text)


def test_a_tight_budget_drops_the_least_important_sections_first_and_says_so():
    full = brief(story().card)
    b = brief(story().card, budget=full.tokens - 1)
    assert titles(b) == titles(full)[:-1]
    assert [(d.key, d.items) for d in b.dropped] == [("questions", 1)]
    assert b.text.endswith(
        f"Не поместилось в лимит {full.tokens - 1} токенов: открытые вопросы (1)."
    )
    assert b.tokens <= b.budget


@pytest.mark.parametrize("budget", range(60, 520, 20))
def test_any_budget_is_respected_and_what_is_kept_is_always_a_prefix_of_the_order(budget):
    full = brief(story().card, SUNDAY)
    b = brief(story().card, SUNDAY, budget=budget)
    order = titles(full)
    assert titles(b) == order[: len(b.sections)]
    if b.fits:
        assert b.tokens <= budget
    # Everything is either shown or accounted for.
    shown = sum(len(s.lines) for s in b.sections)
    assert shown + sum(d.items for d in b.dropped) == sum(len(s.lines) for s in full.sections)
    assert b.complete == (b.text == full.text)


def test_when_only_the_top_section_fits_it_loses_its_least_urgent_lines_last():
    b = brief(story().card, SUNDAY, budget=200)
    (urgent,) = b.sections
    assert [line.text.split(";")[1].strip() for line in urgent.lines] == ["агент: перезвонить"]
    assert b.dropped[0].key == "urgent" and b.dropped[0].items == 2
    assert "просрочено и на подходе (2)" in b.text


def test_a_budget_too_small_for_anything_still_reports_what_is_missing():
    b = brief(story().card, SUNDAY, budget=10)
    assert not b.fits and b.sections == ()
    assert sum(d.items for d in b.dropped) == 10 and "Не поместилось" in b.text
    assert len({d.key for d in b.dropped}) == len(b.dropped)


def test_a_real_tokenizer_can_replace_the_estimate():
    words = brief(story().card, count=lambda text: len(text.split()), budget=60)
    assert words.tokens <= 60 and not words.complete
