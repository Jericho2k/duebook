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

from duebook import (
    UNGUARDED,
    CommitmentState,
    Guards,
    MessagePosted,
    Party,
    Reason,
    apply,
    notices,
)
from duebook.brief import compose
from duebook.card import Closure
from duebook.closing import confirms, overlap, quote_identifies, vouches
from duebook.extract import Completion, ModelError, Usage, Verifier, VerifyingReader
from duebook.extract.extractor import Extraction
from duebook.proposals import (
    AssertFact,
    CancelCommitment,
    MakeCommitment,
    ResolveCommitment,
    Verdict,
    Verification,
)
from duebook.reducer import replay
from duebook.store.codec import decode_card, decode_proposals, encode
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


# ------------------------------------------------- the second opinion ----
#
# A verifier is asked about each close, cancel or replacement, and its verdict
# rides on the proposal. Its yes can stand in for the LEXICAL relevance check
# (shared words) and for nothing else: who may close, the span of the
# target's wording, ambiguity and a renegotiation naming another thing are
# hard. The model below is a stub; what matters is what the reducer does with
# each kind of answer.


def said(verdict: str, evidence: str = "") -> Verification:
    return Verification(Verdict(verdict), evidence, model="stub")


def received_with_a_callback_open(verification: Verification | None):
    t = Ticket()
    refund, call = callback_and_refund(t)
    text = "Спасибо, всё получила!"
    close = ResolveCommitment(refund, "Спасибо, всё получила", "Вернём деньги", verification)
    return t, refund, call, t.wait(hours=30).say(ANNA, text, close)


def test_a_verified_receipt_closes_the_refund_that_shares_no_word_with_it():
    t, refund, call, out = received_with_a_callback_open(said("yes", "всё получила"))
    assert out.rejections == ()
    assert t.card.commitment(refund).state is CommitmentState.FULFILLED
    assert t.card.commitment(call).state is CommitmentState.OPEN


@pytest.mark.parametrize(
    "verification",
    [
        None,  # nobody was asked
        Verification(Verdict.NO, "", "stub"),
        Verification(Verdict.UNSURE, "", "stub"),
        # A yes whose evidence is not in the message is the verifier talking.
        Verification(Verdict.YES, "деньги пришли", "stub"),
        Verification(Verdict.YES, "", "stub"),
        # A verifier that could not be asked counts as unsure.
        Verification(Verdict.UNSURE, "", "stub", error="ModelError: timeout"),
    ],
)
def test_without_a_vouching_yes_the_same_receipt_stays_a_suggestion(verification):
    t, refund, call, out = received_with_a_callback_open(verification)
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]
    c = t.card.commitment(refund)
    assert c.state is CommitmentState.OPEN and c.suggestions[0].kind is Closure.RESOLVED


@pytest.mark.parametrize(("text", "evidence"), [("Спасибо!", "Спасибо"),
                                                 ("Хорошо, жду.", "Хорошо, жду"),
                                                 ("Ок, понял, спасибо.", "Ок, понял")])  # fmt: skip
def test_bare_thanks_cannot_close_even_when_the_verifier_says_yes(text, evidence):
    t = Ticket()
    refund, call = callback_and_refund(t)
    close = ResolveCommitment(refund, evidence, "Вернём деньги", said("yes", evidence))
    out = t.say(ANNA, text, close)
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]
    assert t.card.commitment(refund).state is CommitmentState.OPEN


def test_a_verifier_yes_cannot_override_the_span_of_the_targets_wording():
    t = Ticket()
    refund, call = callback_and_refund(t)
    close = ResolveCommitment(refund, "Деньги пришли", "Я перезвоню", said("yes", "Деньги пришли"))
    out = t.say(ANNA, "Деньги пришли!", close)
    assert reasons(out) == [Reason.TARGET_MISMATCH]
    assert t.card.commitment(refund).state is CommitmentState.OPEN


def test_a_verifier_yes_cannot_override_ambiguity_between_two_similar_promises():
    t = Ticket()
    delivery, case = two_refunds(t)
    close = ResolveCommitment(case, "Деньги пришли", "вернём деньги за чехол",
                              said("yes", "Деньги пришли"))  # fmt: skip
    out = t.wait(hours=5).say(ANNA, "Деньги пришли, спасибо!", close)
    assert reasons(out) == [Reason.AMBIGUOUS_TARGET]
    assert all(c.state is CommitmentState.OPEN for c in t.card.commitments)


def test_the_verifiers_evidence_counts_toward_ambiguity_too():
    # The proposal quotes thanks; the verifier's evidence names money, which
    # fits both refunds: ambiguous, not certain.
    t = Ticket()
    delivery, case = two_refunds(t)
    close = ResolveCommitment(case, "Спасибо", "вернём деньги за чехол",
                              said("yes", "деньги на карте"))  # fmt: skip
    out = t.wait(hours=5).say(ANNA, "Спасибо, деньги на карте.", close)
    assert reasons(out) == [Reason.AMBIGUOUS_TARGET]


def test_a_verifier_yes_cannot_let_the_debtor_cancel_their_own_promise():
    t = Ticket()
    refund, call = callback_and_refund(t)
    words = "Возврат отменяем"
    out = t.say(IVAN, f"{words}.", CancelCommitment(refund, words, "Вернём деньги",
                                                    said("yes", words)))  # fmt: skip
    assert reasons(out) == [Reason.DEBTOR_CANNOT_CANCEL]
    assert t.card.commitment(refund).state is CommitmentState.OPEN


def test_a_verifier_yes_cannot_make_a_promise_about_another_thing_a_renegotiation():
    t = Ticket()
    t.assign(None, IVAN.id)
    old = "Я перезвоню вам насчёт оплаты до среды"
    payment = promise(t, f"{old}.", old, "перезвонить насчёт оплаты", owner=Party.AGENT,
                      due=WeekdayRef(2), due_quote="до среды")  # fmt: skip
    new = "Я перезвоню вам насчёт рассрочки до пятницы"
    second = MakeCommitment(Party.AGENT, "перезвонить насчёт рассрочки", new, due=WeekdayRef(4),
                            due_quote="до пятницы", supersedes=payment, target_quote=old,
                            verification=said("yes", new))  # fmt: skip
    out = t.say(IVAN, f"{new}.", second)
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]
    assert t.card.commitment(payment).state is CommitmentState.OPEN


def message(t: Ticket, text: str) -> MessagePosted:
    """The next message of the ticket, built but not applied."""
    seq = t.card.last_seq + 1
    return MessagePosted(id=f"e{seq}", ticket_id="T-1", seq=seq, at=t.now, author=ANNA, text=text)


def test_a_verifier_yes_does_nothing_when_the_guards_are_told_to_ignore_it():
    t = Ticket()
    refund, call = callback_and_refund(t)
    close = ResolveCommitment(refund, "всё получила", "Вернём деньги", said("yes", "всё получила"))
    msg = message(t, "Спасибо, всё получила!")
    out = apply(t.card, msg, [close], CAL, guards=Guards(verifier=False))
    assert reasons(out) == [Reason.NOT_ABOUT_TARGET]


def test_vouches_requires_a_yes_with_content_literally_in_the_message():
    msg = "Спасибо,   ВСЁ получила!"
    assert vouches(said("yes", "всё получила"), msg)
    assert not vouches(said("yes", "все получил"), msg)
    assert not vouches(said("no", "всё получила"), msg)
    assert not vouches(said("unsure", "всё получила"), msg)
    assert not vouches(said("yes", "Спасибо"), msg)
    assert not vouches(None, msg)


# ------------------------------------------- the verifier between the two ----


class StubJudge:
    """Answers each verification from a list and records what it was shown."""

    model = "stub/judge"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[list[dict]] = []

    def complete_json(self, messages, schema, name="output"):
        self.asked.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Completion(answer, Usage(1, 400, 20, 0, 0.00002, reasoning_tokens=300))


class Fixed:
    """An extractor that proposes what it is given, per message."""

    def __init__(self, *per_message):
        self.per_message = list(per_message)

    def extract(self, card, event):
        return Extraction(tuple(self.per_message.pop(0)), usage=Usage(1, 1000, 100, 0, 0.0001))


def test_only_proposals_that_would_close_an_open_promise_are_put_to_the_verifier():
    t = Ticket()
    refund, call = callback_and_refund(t)
    judge = StubJudge({"verdict": "yes", "evidence_quote": "всё получила"})
    fact = AssertFact("order.number", "4815", "заказ 4815")
    close = ResolveCommitment(refund, "всё получила", "Вернём деньги")
    stale = ResolveCommitment("no-such-promise", "всё получила", "Вернём деньги")
    reader = VerifyingReader(Fixed([fact, close, stale], []), Verifier(judge, CAL))
    msg = message(t, "Спасибо, всё получила! заказ 4815")
    extraction = reader.extract(t.card, msg)
    assert len(judge.asked) == 1
    system, user = judge.asked[0]
    # Fixed instructions first; then the commitment and the message alone.
    assert system["role"] == "system" and "вернуть деньги" not in system["content"]
    assert user["content"].index("COMMITMENT") < user["content"].index("NEW MESSAGE")
    assert '"Вернём деньги до пятницы"' in user["content"]
    assert "PROPOSED CHANGE: done" in user["content"] and "Иван" not in user["content"]
    got_fact, got_close, got_stale = extraction.proposals
    assert got_fact == fact and got_stale.verification is None
    assert got_close.verification == Verification(Verdict.YES, "всё получила", "stub/judge")
    assert extraction.usage.calls == 1 and extraction.usage.reasoning_tokens == 0
    assert extraction.total_usage.calls == 2 and extraction.total_usage.reasoning_tokens == 300
    out = apply(t.card, msg, extraction.proposals, CAL)
    assert t.card.commitment(refund).unresolved
    assert out.card.commitment(refund).state is CommitmentState.FULFILLED

    # An ordinary message costs no verification.
    quiet = message(t, "Жду.")
    assert reader.extract(t.card, quiet).proposals == () and len(judge.asked) == 1


@pytest.mark.parametrize(
    "answer",
    [
        ModelError("timeout, not retried"),
        ModelError("http 503"),
        {"verdict": "maybe"},
        {"verdict": "yes", "evidence_quote": 7},
    ],
)
def test_a_verifier_that_fails_or_garbles_counts_as_unsure(answer):
    t = Ticket()
    refund, call = callback_and_refund(t)
    close = ResolveCommitment(refund, "всё получила", "Вернём деньги")
    reader = VerifyingReader(Fixed([close]), Verifier(StubJudge(answer), CAL))
    msg = message(t, "Спасибо, всё получила!")
    (got,) = reader.extract(t.card, msg).proposals
    assert got.verification.verdict is Verdict.UNSURE and got.verification.error
    assert reasons(apply(t.card, msg, [got], CAL)) == [Reason.NOT_ABOUT_TARGET]


def test_a_verdict_is_stored_with_its_proposal_and_old_proposals_read_without_one():
    close = ResolveCommitment("e2#0", "всё получила", "Вернём деньги", said("yes", "всё получила"))
    renewed = MakeCommitment(Party.COMPANY, "вернуть", "вернём до среды", supersedes="e2#0",
                             target_quote="Вернём", verification=said("unsure"))  # fmt: skip
    assert decode_proposals(encode([close, renewed])) == (close, renewed)
    old = encode([close, renewed])
    for p in old:
        del p["verification"]
    assert all(p.verification is None for p in decode_proposals(old))


def test_replaying_stored_proposals_reproduces_the_verified_card_without_a_model():
    t, refund, call, _ = received_with_a_callback_open(said("yes", "всё получила"))
    stored = [(e, decode_proposals(encode(list(p)))) for e, p in t.log]
    assert replay("T-1", stored, CAL) == t.card
