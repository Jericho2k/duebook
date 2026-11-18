from dataclasses import replace
from datetime import date

import pytest
from helpers import ANNA, BILLING, BOT, CAL, IVAN, MSK, OLGA, Ticket

from duebook import CommitmentState, Confidence, Party, Reason, StepOutcome, apply, replay
from duebook.events import Side
from duebook.proposals import (
    AnswerQuestion,
    AskQuestion,
    AssertFact,
    CancelCommitment,
    MakeCommitment,
    RecordStep,
    ResolveCommitment,
    SetStepOutcome,
)
from duebook.reducer import Guards, OutOfOrder
from duebook.temporal import DayRef, WeekdayRef


def reasons(outcome):
    return [r.reason for r in outcome.rejections]


def promise_refund(t: Ticket, **overrides):
    proposal = MakeCommitment(
        owner=Party.COMPANY,
        action="вернуть деньги за заказ",
        quote="вернём деньги до пятницы",
        due=WeekdayRef(4),
        due_quote="до пятницы",
    )
    return t.say(IVAN, "Анна, мы вернём деньги до пятницы.", replace(proposal, **overrides))


# ------------------------------------------------------------ grounding ----


def test_a_proposal_must_quote_the_message_it_comes_from():
    t = Ticket()
    out = t.say(
        ANNA,
        "Заказ так и не пришёл.",
        AssertFact("order.status", "доставлен", quote="заказ доставлен"),
    )
    assert reasons(out) == [Reason.UNGROUNDED]
    assert t.card.facts == ()


def test_grounding_ignores_case_spacing_and_yo():
    t = Ticket()
    out = t.say(
        ANNA, "Заказ  ещё НЕ пришёл", AssertFact("order.status", "не пришёл", "еще не пришел")
    )
    assert out.rejections == ()


def test_proposals_on_a_non_message_event_are_refused():
    t = Ticket()
    from duebook import AssigneeChanged

    event = AssigneeChanged(id="e1", ticket_id="T-1", seq=1, at=t.now, previous=None, current="x")
    out = apply(t.card, event, [AssertFact("a", "b", "c")], CAL)
    assert reasons(out) == [Reason.NOT_A_MESSAGE]


# ------------------------------------------------------------- ordering ----


def test_redelivered_event_changes_nothing():
    t = Ticket()
    promise_refund(t)
    from duebook import MessagePosted

    text = "Анна, мы вернём деньги до пятницы."
    again = MessagePosted(id="e1", ticket_id="T-1", seq=1, at=t.now, author=IVAN, text=text)
    proposal = MakeCommitment(Party.COMPANY, "вернуть деньги", "вернём деньги до пятницы")
    out = apply(t.card, again, [proposal], CAL)
    assert out.replayed and out.card is t.card
    assert len(t.card.commitments) == 1


def test_a_gap_in_the_stream_is_an_error():
    t = Ticket()
    from duebook import MessagePosted

    skipped = MessagePosted(id="e5", ticket_id="T-1", seq=5, at=t.now, author=ANNA, text="?")
    with pytest.raises(OutOfOrder):
        apply(t.card, skipped, [], CAL)


def test_replay_reproduces_the_card_and_can_stop_at_any_point():
    from duebook import MessagePosted

    def message(seq, author, text):
        return MessagePosted(
            id=f"e{seq}", ticket_id="T-1", seq=seq, at=Ticket().now, author=author, text=text
        )

    stream = [
        (message(1, ANNA, "Тариф Базовый"), [AssertFact("plan", "Базовый", "Тариф Базовый")]),
        (message(2, ANNA, "Нет, тариф Про"), [AssertFact("plan", "Про", "тариф Про")]),
    ]
    assert replay("T-1", stream, CAL) == replay("T-1", stream, CAL)
    assert [f.value for f in replay("T-1", stream[:1], CAL).current_facts()] == ["Базовый"]
    assert [f.value for f in replay("T-1", stream, CAL).current_facts()] == ["Про"]


# ---------------------------------------------------------------- facts ----


def test_a_side_updating_its_own_statement_supersedes_it():
    t = Ticket()
    t.say(ANNA, "У меня iPhone 12", AssertFact("device", "iPhone 12", "iPhone 12"))
    t.wait(hours=1)
    t.say(ANNA, "Ошиблась, у меня iPhone 13", AssertFact("device", "iPhone 13", "iPhone 13"))

    old, new = t.card.facts
    assert old.valid_to == t.now and old.superseded_by == new.id
    assert t.card.current_facts() == (new,)
    assert t.card.changed_facts() == ((old, new),)
    assert t.card.contradictions == ()


def test_repeating_a_known_fact_is_a_duplicate():
    t = Ticket()
    t.say(ANNA, "У меня iPhone 12", AssertFact("device", "iPhone 12", "iPhone 12"))
    out = t.say(ANNA, "Да, iphone 12", AssertFact("Device", "iphone 12", "iphone 12"))
    assert reasons(out) == [Reason.DUPLICATE]
    assert len(t.card.facts) == 1


def test_the_two_sides_disagreeing_is_a_contradiction_not_a_change():
    t = Ticket()
    t.say(ANNA, "Заказ не доставлен", AssertFact("order.status", "не доставлен", "не доставлен"))
    t.say(IVAN, "По трекингу заказ вручён", AssertFact("order.status", "вручён", "заказ вручён"))

    assert len(t.card.current_facts()) == 2
    (conflict,) = t.card.open_contradictions()
    assert conflict.slot == "order.status"
    assert set(conflict.fact_ids) == {f.id for f in t.card.facts}


def test_a_contradiction_is_settled_when_one_side_concedes():
    t = Ticket()
    t.say(ANNA, "Заказ не доставлен", AssertFact("order.status", "не доставлен", "не доставлен"))
    t.say(IVAN, "По трекингу заказ вручён", AssertFact("order.status", "вручён", "заказ вручён"))
    out = t.say(ANNA, "Нашла, заказ вручён соседу", AssertFact("order.status", "вручён", "вручён"))

    assert out.rejections == ()
    assert t.card.open_contradictions() == ()
    assert [f.value for f in t.card.current_facts()] == ["вручён"]


def test_an_open_contradiction_follows_a_sides_latest_statement():
    t = Ticket()
    t.say(ANNA, "Списали 500", AssertFact("charge", "500", "500"))
    t.say(IVAN, "Вижу списание 300", AssertFact("charge", "300", "300"))
    t.say(ANNA, "Точнее, списали 550", AssertFact("charge", "550", "550"))

    (conflict,) = t.card.open_contradictions()
    current = {f.id: f.value for f in t.card.current_facts()}
    assert sorted(current[i] for i in conflict.fact_ids) == ["300", "550"]


def test_an_explicit_change_by_the_other_side_supersedes():
    t = Ticket()
    t.say(ANNA, "У меня тариф Базовый", AssertFact("plan", "Базовый", "тариф Базовый"))
    t.say(IVAN, "Перевёл вас на Про", AssertFact("plan", "Про", "на Про", change=True))
    assert [f.value for f in t.card.current_facts()] == ["Про"]
    assert t.card.contradictions == ()


def test_a_system_of_record_overrides_both_sides_and_settles_the_dispute():
    t = Ticket()
    t.say(ANNA, "Возврат не пришёл", AssertFact("refund", "не пришёл", "не пришёл"))
    t.say(IVAN, "Возврат отправлен", AssertFact("refund", "отправлен", "отправлен"))
    t.say(BILLING, "refund status: settled", AssertFact("refund", "settled", "settled"))
    assert [f.value for f in t.card.current_facts()] == ["settled"]
    assert t.card.open_contradictions() == ()


def test_a_second_order_number_is_another_order_not_a_dispute():
    t = Ticket()
    t.say(ANNA, "Проблема с заказом 512407", AssertFact("order.number", "512407", "512407"))
    t.say(IVAN, "Оформим возврат по заказу 538916",
          AssertFact("order.id", "538916", "538916"))  # fmt: skip
    assert sorted(f.value for f in t.card.current_facts()) == ["512407", "538916"]
    assert t.card.contradictions == ()


def test_an_identifier_is_disputed_only_when_one_side_corrects_the_other():
    t = Ticket()
    t.say(ANNA, "Проблема с заказом 538916", AssertFact("order.number", "538916", "538916"))
    t.say(IVAN, "Заказ не 538916, а 538961", AssertFact("order.number", "538961", "538961"))
    (conflict,) = t.card.open_contradictions()
    assert {f.value for f in t.card.facts if f.id in conflict.fact_ids} == {"538916", "538961"}
    t.say(ANNA, "Да, верно, 538961", AssertFact("order.number", "538961", "538961"))
    assert t.card.open_contradictions() == ()
    assert [f.value for f in t.card.current_facts()] == ["538961"]


def test_a_side_correcting_its_own_identifier_supersedes_it():
    t = Ticket()
    t.say(ANNA, "Заказ 538916", AssertFact("order.number", "538916", "538916"))
    t.say(ANNA, "Ой, не 538916, а 538961", AssertFact("order.number", "538961", "538961"))
    assert [f.value for f in t.card.current_facts()] == ["538961"]
    assert t.card.contradictions == ()


def test_before_decision_0005_an_identifier_held_one_value():
    t = Ticket()
    t.say(ANNA, "Проблема с заказом 512407", AssertFact("order.number", "512407", "512407"))
    from duebook import MessagePosted

    text = "Оформим возврат по заказу 538916"
    event = MessagePosted(id="e2", ticket_id="T-1", seq=2, at=t.now, author=IVAN, text=text)
    out = apply(t.card, event, [AssertFact("order.number", "538916", "538916")], CAL,
                guards=Guards(rules=4))  # fmt: skip
    assert len(out.card.open_contradictions()) == 1


# ---------------------------------------------------------- commitments ----


def test_a_promise_with_an_agreed_deadline_is_admitted_with_high_confidence():
    t = Ticket()
    out = promise_refund(t)
    (c,) = t.card.commitments
    assert out.rejections == ()
    assert c.state is CommitmentState.OPEN and c.responsible == IVAN.id
    assert c.due.confidence is Confidence.HIGH
    assert c.due.deadline.due_at.astimezone(MSK).date() == date(2027, 1, 15)
    assert c.source.quote == "вернём деньги до пятницы" and c.source.seq == 1


def test_model_and_parser_disagreeing_marks_the_deadline_low_confidence():
    t = Ticket()
    promise_refund(t, due=WeekdayRef(3))  # the model read "Thursday"
    assert t.card.commitments[0].due.confidence is Confidence.LOW


def test_a_deadline_only_the_model_can_read_is_medium_confidence():
    t = Ticket()
    t.say(
        IVAN,
        "Свяжусь с вами сразу после выходных.",
        MakeCommitment(
            Party.AGENT,
            "связаться с клиентом",
            quote="Свяжусь с вами сразу после выходных",
            due=WeekdayRef(0),
            due_quote="сразу после выходных",
        ),
    )
    assert t.card.commitments[0].due.confidence is Confidence.MEDIUM


def test_the_parser_alone_can_supply_a_deadline_the_model_missed():
    t = Ticket()
    t.say(IVAN, "Перезвоню завтра.", MakeCommitment(Party.AGENT, "перезвонить", "Перезвоню завтра"))
    assert t.card.commitments[0].due.expr == DayRef(1)
    assert t.card.commitments[0].due.confidence is Confidence.MEDIUM


def test_a_bad_deadline_drops_the_deadline_and_keeps_the_promise():
    t = Ticket()
    out = promise_refund(t, due_quote="до понедельника")
    assert reasons(out) == [Reason.DEADLINE_UNGROUNDED] and out.rejections[0].partial
    assert t.card.commitments[0].due is None

    t = Ticket()
    out = promise_refund(t, due=DayRef(-1))
    assert reasons(out) == [Reason.DEADLINE_INVALID]
    assert len(t.card.commitments) == 1


def test_nobody_can_bind_the_other_side():
    t = Ticket()
    out = t.say(
        ANNA,
        "Вы обещали вернуть деньги!",
        MakeCommitment(Party.COMPANY, "вернуть деньги", "обещали вернуть деньги"),
    )
    assert reasons(out) == [Reason.OWNER_AUTHOR_MISMATCH]
    assert t.card.commitments == ()


def test_the_customer_can_commit_themselves():
    t = Ticket()
    t.say(
        ANNA,
        "Пришлю скриншот завтра",
        MakeCommitment(Party.CUSTOMER, "прислать скриншот", "Пришлю скриншот завтра"),
    )
    (c,) = t.card.commitments
    assert c.owner is Party.CUSTOMER and c.responsible is None and not c.orphaned


def test_the_same_promise_twice_is_one_commitment():
    t = Ticket()
    promise_refund(t)
    assert reasons(promise_refund(t)) == [Reason.DUPLICATE]
    assert len(t.card.commitments) == 1


def restore_access(t: Ticket, workspace: str, due: str, weekday: int):
    quote = f"Восстановим доступ к рабочему пространству {workspace} {due}"
    proposal = MakeCommitment(Party.COMPANY, "восстановить доступ", quote, due=WeekdayRef(weekday),
                              due_quote=due)  # fmt: skip
    return t.say(IVAN, f"{quote}.", proposal)


def test_alike_promises_about_different_things_are_two_commitments():
    t = Ticket()
    restore_access(t, "Альфа", "до пятницы", 4)
    assert reasons(restore_access(t, "Бета", "до пятницы", 4)) == []
    assert len(t.card.commitments) == 2


def test_a_restatement_with_another_deadline_is_kept():
    t = Ticket()
    restore_access(t, "Альфа", "до пятницы", 4)
    assert reasons(restore_access(t, "Альфа", "до понедельника", 0)) == []
    assert len(t.card.commitments) == 2


def test_a_restatement_without_a_deadline_is_a_duplicate():
    t = Ticket()
    promise_refund(t)
    again = promise_refund(t, quote="вернём деньги", due=None, due_quote=None)
    assert reasons(again) == [Reason.DUPLICATE]


def test_promises_that_name_nothing_are_not_merged():
    t = Ticket()
    for _ in range(2):
        t.say(IVAN, "Сделаем всё.", MakeCommitment(Party.COMPANY, "сделать", "Сделаем всё"))
    assert len(t.card.commitments) == 2


def test_the_debtors_word_is_a_claim_until_the_other_side_confirms():
    t = Ticket()
    promise_refund(t)
    cid = t.card.commitments[0].id

    t.wait(days=1).say(
        IVAN,
        "Деньги отправили.",
        ResolveCommitment(cid, "Деньги отправили", target_quote="вернём деньги"),
    )
    assert t.card.commitment(cid).state is CommitmentState.CLAIMED
    claimed_at = t.now

    again = t.say(
        IVAN,
        "Деньги отправили, проверьте.",
        ResolveCommitment(cid, "Деньги отправили", target_quote="вернём деньги"),
    )
    assert reasons(again) == [Reason.ALREADY_CLAIMED]

    t.wait(days=1).say(
        ANNA,
        "Да, деньги пришли, спасибо",
        ResolveCommitment(cid, "деньги пришли", target_quote="вернём деньги"),
    )
    c = t.card.commitment(cid)
    assert c.state is CommitmentState.FULFILLED
    assert c.claimed_at == claimed_at and c.resolved_at == t.now
    assert c.resolution.side is Side.CUSTOMER


def test_a_system_event_verifies_directly():
    t = Ticket()
    promise_refund(t)
    cid = t.card.commitments[0].id
    t.say(
        BILLING,
        "refund settled",
        ResolveCommitment(cid, "refund settled", target_quote="вернём деньги"),
    )
    assert t.card.commitment(cid).state is CommitmentState.FULFILLED


def test_the_debtor_cannot_cancel_their_own_promise_but_the_customer_can():
    t = Ticket()
    promise_refund(t)
    cid = t.card.commitments[0].id

    out = t.say(
        IVAN,
        "Возврат не потребуется.",
        CancelCommitment(cid, "Возврат не потребуется", target_quote="вернём деньги"),
    )
    assert reasons(out) == [Reason.DEBTOR_CANNOT_CANCEL]
    assert t.card.commitment(cid).unresolved

    t.say(
        ANNA,
        "Возврат уже не нужен",
        CancelCommitment(cid, "Возврат уже не нужен", target_quote="вернём деньги"),
    )
    assert t.card.commitment(cid).state is CommitmentState.CANCELLED

    late = t.say(
        ANNA, "Точно не нужен", CancelCommitment(cid, "не нужен", target_quote="вернём деньги")
    )
    assert reasons(late) == [Reason.ALREADY_RESOLVED]


def test_renegotiating_supersedes_the_old_commitment():
    t = Ticket()
    promise_refund(t)
    old_id = t.card.commitments[0].id
    t.wait(days=2).say(
        IVAN,
        "Не успеваем, вернём деньги в понедельник.",
        MakeCommitment(
            Party.COMPANY,
            "вернуть деньги за заказ",
            quote="вернём деньги в понедельник",
            due=WeekdayRef(0),
            due_quote="в понедельник",
            supersedes=old_id,
            target_quote="вернём деньги",
        ),
    )
    old, new = t.card.commitments
    assert old.state is CommitmentState.SUPERSEDED and old.superseded_by == new.id
    assert new.state is CommitmentState.OPEN
    assert new.due.deadline.due_at.astimezone(MSK).date() == date(2027, 1, 18)


def test_unknown_targets_are_refused():
    t = Ticket()
    out = t.say(ANNA, "Спасибо, всё пришло", ResolveCommitment("nope", "всё пришло"))
    assert reasons(out) == [Reason.UNKNOWN_TARGET]


# -------------------------------------------------------------- handoff ----


def test_a_promise_follows_the_ticket_to_the_next_operator():
    t = Ticket()
    t.assign(None, IVAN.id)
    promise_refund(t)
    t.assign(IVAN.id, OLGA.id)

    (c,) = t.card.commitments
    assert c.responsible == OLGA.id and c.inherited_from == IVAN.id


def test_a_promise_on_an_unassigned_ticket_is_orphaned_until_someone_takes_it():
    t = Ticket()
    t.assign(None, IVAN.id)
    promise_refund(t)
    t.assign(IVAN.id, None)
    assert t.card.commitments[0].orphaned

    t.assign(None, OLGA.id)
    c = t.card.commitments[0]
    assert not c.orphaned and c.responsible == OLGA.id and c.inherited_from == IVAN.id


def test_a_second_line_agents_own_promise_does_not_move_with_the_assignee():
    t = Ticket()
    t.assign(None, IVAN.id)
    t.say(
        OLGA,
        "Проверю логи завтра.",
        MakeCommitment(Party.AGENT, "проверить логи", "Проверю логи завтра"),
    )
    t.assign(IVAN.id, "agent-petr")
    assert t.card.commitments[0].responsible == OLGA.id


def test_a_bots_promise_lands_on_the_assignee_or_on_nobody():
    t = Ticket()
    proposal = MakeCommitment(Party.COMPANY, "перезвонить", "Мы перезвоним вам завтра")
    t.say(BOT, "Мы перезвоним вам завтра.", proposal)
    assert t.card.commitments[0].orphaned

    t = Ticket()
    t.assign(None, IVAN.id)
    t.say(BOT, "Мы перезвоним вам завтра.", proposal)
    assert t.card.commitments[0].responsible == IVAN.id


def test_resolved_commitments_do_not_move():
    t = Ticket()
    t.assign(None, IVAN.id)
    promise_refund(t)
    cid = t.card.commitments[0].id
    t.say(
        BILLING,
        "refund settled",
        ResolveCommitment(cid, "refund settled", target_quote="вернём деньги"),
    )
    t.assign(IVAN.id, OLGA.id)
    assert t.card.commitment(cid).responsible == IVAN.id


# -------------------------------------------------- steps and questions ----


def test_a_step_keeps_its_outcome_so_a_failed_fix_is_not_offered_twice():
    t = Ticket()
    t.say(
        IVAN,
        "Попробуйте переустановить приложение",
        RecordStep("переустановить приложение", "переустановить приложение"),
    )
    step_id = t.card.steps[0].id
    t.say(
        ANNA,
        "Переустановила, не помогло",
        SetStepOutcome(step_id, StepOutcome.FAILED, "не помогло"),
    )
    assert t.card.steps[0].outcome is StepOutcome.FAILED
    assert t.card.steps[0].outcome_source.side is Side.CUSTOMER

    again = t.say(
        OLGA,
        "Попробуйте переустановить приложение",
        RecordStep("Переустановить приложение", "переустановить приложение"),
    )
    assert reasons(again) == [Reason.DUPLICATE]


def test_a_question_stays_open_until_the_side_that_owes_the_answer_gives_it():
    t = Ticket()
    t.say(
        IVAN, "Какой у вас номер заказа?", AskQuestion("номер заказа?", "Какой у вас номер заказа")
    )
    (q,) = t.card.questions
    assert q.waiting_on is Side.CUSTOMER and q.open

    wrong = t.say(OLGA, "Номер заказа 123?", AnswerQuestion(q.id, "Номер заказа 123"))
    assert reasons(wrong) == [Reason.WRONG_PARTY]

    t.say(ANNA, "Номер 4815", AnswerQuestion(q.id, "Номер 4815"))
    assert not t.card.questions[0].open


def test_event_from_another_ticket_is_refused():
    from duebook import Card, MessagePosted

    event = MessagePosted(id="e1", ticket_id="T-2", seq=1, at=Ticket().now, author=ANNA, text="?")
    with pytest.raises(ValueError):
        apply(Card("T-1"), event, [], CAL)
