"""Closing fails safe: a promise is closed only by a message that is about it.

Round 1 found the extractor attaching "done", "cancelled" or "replaced" to the
wrong promise, and the reducer admitting it because it checked WHO may close a
promise, never WHETHER the message was about it. These are the adversarial
cases: in each, a close that a careless reader would make must be refused, the
promise must stay open — still alerting — and the evidence must survive as a
suggestion for a person to confirm.
"""

from datetime import timedelta

import pytest
from helpers import ANNA, BILLING, CAL, IVAN, OLGA, Ticket

from duebook import UNGUARDED, CommitmentState, Party, Reason, apply, notices
from duebook.brief import compose
from duebook.card import Closure
from duebook.closing import confirms, overlap, quote_identifies
from duebook.proposals import CancelCommitment, MakeCommitment, ResolveCommitment
from duebook.store.codec import decode_card, encode
from duebook.temporal import DayRef, Offset, Unit, WeekdayRef


def reasons(outcome):
    return [r.reason for r in outcome.rejections]


def promise(t: Ticket, text: str, quote: str, action: str, owner=Party.COMPANY, by=IVAN, due=None,
            due_quote: str = "") -> str:  # fmt: skip
    t.say(by, text, MakeCommitment(owner, action, quote, due=due, due_quote=due_quote))
    return t.card.commitments[-1].id


def two_refunds(t: Ticket) -> tuple[str, str]:
    t.assign(None, IVAN.id)
    delivery = promise(t, "Мы вернём вам деньги за доставку до пятницы.",
                       "Мы вернём вам деньги за доставку до пятницы", "вернуть деньги за доставку",
                       due=WeekdayRef(4), due_quote="до пятницы")  # fmt: skip
    case = promise(t, "И вернём деньги за чехол до среды.", "вернём деньги за чехол до среды",
                   "вернуть деньги за чехол", due=WeekdayRef(2), due_quote="до среды")  # fmt: skip
    return delivery, case


# ------------------------------------------ two similar promises open ----


def test_money_arrived_with_two_refunds_open_closes_neither():
    t = Ticket()
    delivery, case = two_refunds(t)
    out = t.wait(hours=5).say(
        ANNA, "Деньги пришли, спасибо!",
        ResolveCommitment(case, "Деньги пришли", target_quote="вернём деньги за чехол"),
    )  # fmt: skip
    assert reasons(out) == [Reason.AMBIGUOUS_TARGET]
    assert all(c.state is CommitmentState.OPEN for c in t.card.commitments)
    (hint,) = t.card.commitment(case).suggestions
    assert (
        hint.kind is Closure.RESOLVED and hint.source.seq == 4 and hint.reason == "ambiguous_target"
    )


def test_money_for_the_named_refund_closes_that_refund_and_only_that_one():
    t = Ticket()
    delivery, case = two_refunds(t)
    out = t.wait(hours=5).say(
        ANNA, "Деньги за чехол пришли, спасибо!",
        ResolveCommitment(case, "Деньги за чехол пришли", target_quote="деньги за чехол"),
    )  # fmt: skip
    assert out.rejections == ()
    assert t.card.commitment(case).state is CommitmentState.FULFILLED
    assert t.card.commitment(delivery).state is CommitmentState.OPEN


def test_money_for_one_refund_pinned_on_the_other_is_refused_and_the_other_still_alerts():
    t = Ticket()
    delivery, case = two_refunds(t)
    out = t.wait(hours=5).say(
        ANNA, "Деньги за чехол пришли, спасибо!",
        ResolveCommitment(delivery, "Деньги за чехол пришли", target_quote="деньги за доставку"),
    )  # fmt: skip
    assert reasons(out) == [Reason.AMBIGUOUS_TARGET]
    c = t.card.commitment(delivery)
    assert c.state is CommitmentState.OPEN and c.suggestions
    friday_night = c.due.deadline.due_at + timedelta(hours=1)
    assert delivery in {n.commitment_id for n in notices(t.card, friday_night)}


def test_without_the_guards_the_same_wrong_close_silences_the_alert():
    # What round 1 did: the reducer checked who may close, not what.
    t = Ticket()
    delivery, case = two_refunds(t)
    wrong = ResolveCommitment(delivery, "Деньги за чехол пришли", target_quote="")
    event_text = "Деньги за чехол пришли, спасибо!"
    t.wait(hours=5)
    from duebook import MessagePosted

    event = MessagePosted(id="e4", ticket_id="T-1", seq=4, at=t.now, author=ANNA, text=event_text)
    card = apply(t.card, event, [wrong], CAL, guards=UNGUARDED).card
    assert card.commitment(delivery).state is CommitmentState.FULFILLED
    due = card.commitment(delivery).due.deadline.due_at
    assert delivery not in {n.commitment_id for n in notices(card, due + timedelta(hours=1))}


# ------------------------------------------------------ thanks for what ----


def callback_and_refund(t: Ticket) -> tuple[str, str]:
    t.assign(None, IVAN.id)
    refund = promise(t, "Вернём деньги до пятницы.", "Вернём деньги до пятницы", "вернуть деньги",
                     due=WeekdayRef(4), due_quote="до пятницы")  # fmt: skip
    call = promise(t, "Я перезвоню вам завтра до 15:00.", "Я перезвоню вам завтра до 15:00",
                   "перезвонить", owner=Party.AGENT, due=DayRef(1), due_quote="завтра")  # fmt: skip
    return refund, call


def test_thanks_for_the_call_does_not_close_the_refund():
    t = Ticket()
    refund, call = callback_and_refund(t)
    out = t.wait(hours=20).say(
        ANNA, "Спасибо за звонок!",
        ResolveCommitment(refund, "Спасибо за звонок", target_quote="Вернём деньги"),
    )  # fmt: skip
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]
    assert t.card.commitment(refund).state is CommitmentState.OPEN


def test_thanks_that_names_the_call_closes_the_call():
    t = Ticket()
    refund, call = callback_and_refund(t)
    out = t.wait(hours=20).say(
        ANNA, "Спасибо за звонок!",
        ResolveCommitment(call, "Спасибо за звонок", target_quote="перезвоню вам"),
    )  # fmt: skip
    assert out.rejections == ()
    assert t.card.commitment(call).state is CommitmentState.FULFILLED
    assert t.card.commitment(refund).state is CommitmentState.OPEN


BARE = ["Спасибо.", "Спасибо!", "Спасибо, что держите в курсе.", "Ок, спасибо за информацию.",
        "Хорошо, жду.", "thanks"]  # fmt: skip


@pytest.mark.parametrize("text", BARE)
def test_bare_thanks_never_fulfils_even_the_only_open_promise(text):
    t = Ticket()
    t.assign(None, IVAN.id)
    said = "Перезвоню вам через 2 часа"
    call = promise(t, f"{said}.", said, "перезвонить", owner=Party.AGENT,
                   due=Offset(2, Unit.HOUR), due_quote="через 2 часа")  # fmt: skip
    out = t.wait(hours=1).say(
        ANNA, text, ResolveCommitment(call, text.rstrip(".!"), target_quote="Перезвоню вам")
    )
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]
    assert t.card.commitment(call).state is CommitmentState.OPEN


def test_a_plain_confirmation_closes_the_only_open_promise_of_that_side_and_no_more():
    alone = Ticket()
    alone.assign(None, IVAN.id)
    refund = promise(alone, "Вернём деньги до пятницы.", "Вернём деньги до пятницы",
                     "вернуть деньги", due=WeekdayRef(4), due_quote="до пятницы")  # fmt: skip
    ok = alone.say(
        ANNA, "Всё получила!", ResolveCommitment(refund, "Всё получила", "Вернём деньги")
    )
    assert ok.rejections == ()

    crowded = Ticket()
    refund, call = callback_and_refund(crowded)
    out = crowded.say(ANNA, "Всё получила!", ResolveCommitment(refund, "Всё получила",
                                                                "Вернём деньги"))  # fmt: skip
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]


def test_a_customers_own_promise_is_not_closed_by_the_wrong_receipt():
    t = Ticket()
    t.assign(None, IVAN.id)
    shot = promise(t, "Пришлю скриншот ошибки сегодня.", "Пришлю скриншот ошибки сегодня",
                   "прислать скриншот", owner=Party.CUSTOMER, by=ANNA)  # fmt: skip
    receipt = promise(t, "И фото чека пришлю.", "фото чека пришлю", "прислать фото чека",
                      owner=Party.CUSTOMER, by=ANNA)  # fmt: skip
    out = t.say(IVAN, "Скриншот получили, спасибо.",
                ResolveCommitment(receipt, "Скриншот получили", "фото чека"))  # fmt: skip
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]
    assert t.card.commitment(receipt).unresolved and t.card.commitment(shot).unresolved


# ------------------------------------------------- cancel and replace ----


def two_replacements(t: Ticket) -> tuple[str, str]:
    t.assign(None, IVAN.id)
    said = "Отправим замену чайника до пятницы"
    kettle = promise(t, f"{said}.", said, "отправить замену чайника", due=WeekdayRef(4),
                     due_quote="до пятницы")  # fmt: skip
    iron = promise(t, "И замену утюга до среды.", "замену утюга до среды",
                   "отправить замену утюга", due=WeekdayRef(2), due_quote="до среды")  # fmt: skip
    return kettle, iron


def test_cancelling_one_of_two_similar_promises_cancels_the_one_named():
    t = Ticket()
    kettle, iron = two_replacements(t)
    words = "Замену утюга можно не отправлять"
    out = t.say(ANNA, f"{words}, он заработал.", CancelCommitment(iron, words, "замену утюга"))
    assert out.rejections == ()
    assert t.card.commitment(iron).state is CommitmentState.CANCELLED
    assert t.card.commitment(kettle).state is CommitmentState.OPEN


def test_a_cancel_pinned_on_the_other_similar_promise_is_refused():
    t = Ticket()
    kettle, iron = two_replacements(t)
    words = "Замену утюга можно не отправлять"
    out = t.say(ANNA, f"{words}.", CancelCommitment(kettle, words, "замену чайника"))
    assert reasons(out) == [Reason.AMBIGUOUS_TARGET]
    assert t.card.commitment(kettle).state is CommitmentState.OPEN
    assert t.card.commitment(kettle).suggestions[0].kind is Closure.CANCELLED


def test_renegotiation_pointing_at_the_wrong_promise_keeps_both_and_retires_neither():
    t = Ticket()
    refund, call = callback_and_refund(t)
    renewed = MakeCommitment(
        Party.COMPANY, "вернуть деньги к среде", "вернём деньги до среды", due=WeekdayRef(2),
        due_quote="до среды", supersedes=call, target_quote="Я перезвоню вам",
    )  # fmt: skip
    out = t.wait(hours=2).say(IVAN, "Не успеваем: вернём деньги до среды.", renewed)
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET] and out.rejections[0].partial
    old_call, new = t.card.commitment(call), t.card.commitments[-1]
    assert old_call.state is CommitmentState.OPEN and new.state is CommitmentState.OPEN
    (hint,) = old_call.suggestions
    assert hint.kind is Closure.SUPERSEDED and hint.replacement == new.id
    # The refund it really renegotiates is not retired on a reference to another.
    assert t.card.commitment(refund).state is CommitmentState.OPEN


def test_a_second_similar_promise_read_as_a_renegotiation_of_the_first_retires_nothing():
    # Found on dev: "I'll call about the instalments" taken to replace "I'll call about
    # the payment" retired a promise that was later broken.
    t = Ticket()
    t.assign(None, IVAN.id)
    said = "Я перезвоню вам насчёт оплаты до среды"
    payment = promise(t, f"{said}.", said, "перезвонить насчёт оплаты", owner=Party.AGENT,
                      due=WeekdayRef(2), due_quote="до среды")  # fmt: skip
    second = MakeCommitment(
        Party.AGENT, "перезвонить насчёт рассрочки", "Я перезвоню вам насчёт рассрочки до пятницы",
        due=WeekdayRef(4), due_quote="до пятницы", supersedes=payment, target_quote=said,
    )  # fmt: skip
    out = t.say(IVAN, "Я перезвоню вам насчёт рассрочки до пятницы.", second)
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]
    assert t.card.commitment(payment).state is CommitmentState.OPEN
    assert len(t.card.commitments) == 2


@pytest.mark.parametrize(
    "said",
    [
        "К сожалению, не успеваем: вернём деньги до среды",  # drops "за доставку"
        "Сроки сдвигаются, вернём деньги за доставку до среды",  # says why
        "Перенесём возврат денег за доставку до среды",  # another root for the same deed
    ],
)
def test_a_renegotiation_may_add_or_drop_detail_and_still_replace_the_promise(said):
    t = Ticket()
    delivery, case = two_refunds(t)
    arrived = ResolveCommitment(case, "Деньги за чехол пришли", "деньги за чехол")
    t.wait(hours=1).say(ANNA, "Деньги за чехол пришли!", arrived)
    renewed = MakeCommitment(Party.COMPANY, "вернуть деньги", said, due=WeekdayRef(2),
                             due_quote="до среды", supersedes=delivery,
                             target_quote="вернём вам деньги за доставку")  # fmt: skip
    out = t.say(IVAN, f"{said}.", renewed)
    assert out.rejections == ()
    assert t.card.commitment(delivery).state is CommitmentState.SUPERSEDED


def test_renegotiation_of_the_right_promise_replaces_it():
    t = Ticket()
    refund, call = callback_and_refund(t)
    renewed = MakeCommitment(
        Party.COMPANY, "вернуть деньги", "вернём деньги до среды", due=WeekdayRef(2),
        due_quote="до среды", supersedes=refund, target_quote="Вернём деньги",
    )  # fmt: skip
    out = t.wait(hours=2).say(IVAN, "Не успеваем: вернём деньги до среды.", renewed)
    assert out.rejections == ()
    assert t.card.commitment(refund).state is CommitmentState.SUPERSEDED


# ---------------------------------------- the second reference must agree ----


@pytest.mark.parametrize(
    "target_quote",
    ["", "   ", "Я перезвоню вам", "вернём деньги до понедельника", "до пятницы", "вам"],
)
def test_the_span_of_the_targets_wording_must_be_in_that_targets_wording(target_quote):
    t = Ticket()
    refund, call = callback_and_refund(t)
    out = t.say(ANNA, "Деньги пришли!", ResolveCommitment(refund, "Деньги пришли", target_quote))
    assert reasons(out) == [Reason.TARGET_MISMATCH]
    assert t.card.commitment(refund).state is CommitmentState.OPEN


def test_the_span_is_compared_ignoring_case_spacing_punctuation_and_yo():
    assert quote_identifies("ВЕРНЕМ  деньги,", "Вернём деньги до пятницы")
    assert not quote_identifies("деньги вернём", "Вернём деньги до пятницы")


def test_the_debtors_claim_and_a_system_of_record_are_guarded_too():
    t = Ticket()
    refund, call = callback_and_refund(t)
    claim = t.say(OLGA, "Перезвонила.", ResolveCommitment(refund, "Перезвонила", "Вернём деньги"))
    assert reasons(claim) == [Reason.NOT_ABOUT_TARGET]
    system = t.say(
        BILLING, "Call logged", ResolveCommitment(refund, "Call logged", "Вернём деньги")
    )
    assert reasons(system) == [Reason.NOT_ABOUT_TARGET]
    assert t.card.commitment(refund).state is CommitmentState.OPEN


def test_a_refused_close_does_not_stop_the_right_one_later():
    t = Ticket()
    delivery, case = two_refunds(t)
    t.say(ANNA, "Деньги пришли!", ResolveCommitment(case, "Деньги пришли", "деньги за чехол"))
    t.say(ANNA, "Это за чехол.", ResolveCommitment(case, "за чехол", "деньги за чехол"))
    c = t.card.commitment(case)
    assert c.state is CommitmentState.FULFILLED and len(c.suggestions) == 1


# ---------------------------------------- where a suggestion is visible ----


def test_the_brief_asks_a_person_to_confirm_a_possible_close():
    t = Ticket()
    delivery, case = two_refunds(t)
    t.say(ANNA, "Деньги пришли, спасибо!", ResolveCommitment(case, "Деньги пришли", "за чехол"))
    text = compose(t.card, t.now, cal=CAL, names={ANNA.id: "Анна"}).text
    (line,) = [x for x in text.splitlines() if "чехол" in x]
    assert "возможно, выполнено по сообщению №4 — подтвердите [№4 Анна: «Деньги пришли»]" in line


def test_a_suggestion_survives_storage_and_an_old_card_reads_without_one():
    t = Ticket()
    delivery, case = two_refunds(t)
    t.say(ANNA, "Деньги пришли!", ResolveCommitment(case, "Деньги пришли", "за чехол"))
    assert decode_card(encode(t.card)) == t.card
    old = encode(t.card)
    for c in old["commitments"]:
        del c["suggestions"]
    assert all(c.suggestions == () for c in decode_card(old).commitments)


# ------------------------------------------------------ the word matcher ----


def test_different_forms_and_roots_of_one_deed_are_recognised():
    assert overlap("Спасибо, что перезвонили", ["Я перезвоню вам"]) == 2
    assert overlap("Спасибо за звонок", ["перезвонить"]) == 2
    assert overlap("Возврат уже не нужен", ["вернём деньги"]) == 2
    assert overlap("Счёт получила", ["Пришлём исправленный счёт"]) == 2


def test_deadline_words_and_short_numbers_are_not_content_and_generic_verbs_count_less():
    assert overlap("Пришлю в течение 3 рабочих дней", ["Вернём в течение 3 рабочих дней"]) == 0
    assert overlap("Доступ к кабинету появился", ["вернём деньги за доставку"]) == 0
    assert overlap("Заказ 4815 пришёл", ["доставим заказ 4815"]) == 4
    # "пришли" (arrived) and "пришлём" (will send) share a stem and nothing else.
    assert overlap("Деньги пришли", ["Пришлём исправленный счёт"]) == 1
    assert overlap("Деньги пришли", ["Мы вернём вам деньги"]) == 2


def test_a_promise_worded_only_in_generic_verbs_can_still_be_renegotiated():
    t = Ticket()
    t.assign(None, IVAN.id)
    old = promise(t, "Напишу вам до пятницы.", "Напишу вам до пятницы", "написать",
                  owner=Party.AGENT, due=WeekdayRef(4), due_quote="до пятницы")  # fmt: skip
    renewed = MakeCommitment(
        Party.AGENT, "написать позже", "напишу вам в течение 5 рабочих дней",
        due=Offset(5, Unit.BUSINESS_DAY), due_quote="в течение 5 рабочих дней",
        supersedes=old, target_quote="Напишу вам до пятницы",
    )  # fmt: skip
    out = t.say(IVAN, "Не успеваю, напишу вам в течение 5 рабочих дней.", renewed)
    assert out.rejections == ()
    assert t.card.commitment(old).state is CommitmentState.SUPERSEDED


def test_thanks_is_not_a_confirmation_and_receipt_is():
    assert not confirms("Спасибо, что держите в курсе")
    assert confirms("Всё получила") and confirms("Деньги пришли")
