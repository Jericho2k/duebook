"""Conditional offers (decision 0004): pending, due, activated, lapsed."""

from datetime import datetime, timedelta

from helpers import ANNA, BILLING, CAL, IVAN, MSK, Ticket

from duebook import NoticeKind, Party, Reason, next_wakeup, notices
from duebook.brief import compose
from duebook.card import Closure, ConditionalState
from duebook.extract.prompt import render_card
from duebook.proposals import MakeConditional, SettleConditional
from duebook.reducer import replay
from duebook.store.codec import decode_card, encode
from duebook.temporal import WeekdayRef

OFFER = "Если посылка не придёт до пятницы, оформим повторную отправку"
CONDITION = "Если посылка не придёт до пятницы"
FRIDAY_END = datetime(2027, 1, 15, 23, 59, 59, tzinfo=MSK)


def offered() -> Ticket:
    t = Ticket()
    t.assign(None, IVAN.id)
    t.say(IVAN, f"{OFFER}.", MakeConditional(Party.COMPANY, "оформить повторную отправку", OFFER,
                                             CONDITION, WeekdayRef(4), "до пятницы"))  # fmt: skip
    return t


def settle(t: Ticket, author, text: str, holds: bool, quote: str | None = None,
           target: str = "повторную отправку"):  # fmt: skip
    k = t.card.conditionals[0]
    return t.say(author, text, SettleConditional(k.id, holds, quote or text.rstrip("."), target))


def test_a_conditional_offer_is_pending_and_owes_nothing():
    t = offered()
    (k,) = t.card.conditionals
    assert k.state is ConditionalState.PENDING and k.condition == CONDITION
    assert k.due.deadline.due_at == FRIDAY_END
    assert t.card.commitments == ()
    assert notices(t.card, FRIDAY_END - timedelta(minutes=1)) == []


def test_a_condition_due_with_nothing_recorded_raises_one_notice_keyed_by_the_offer():
    t = offered()
    (k,) = t.card.conditionals
    assert next_wakeup(t.card, t.now) == FRIDAY_END
    (n,) = notices(t.card, FRIDAY_END)
    assert n.kind is NoticeKind.CONDITION_DUE and n.recipient == IVAN.id and n.seq == k.source.seq
    assert n.key == f"T-1:{k.id}:condition_due:{k.due.deadline.due_at.isoformat()}"
    assert notices(t.card, FRIDAY_END + timedelta(days=2)) == [n]


def test_activation_admits_a_commitment_sourced_from_both_messages():
    t = offered()
    t.wait(days=5)
    out = settle(t, ANNA, "Посылка так и не пришла.", holds=True)
    assert out.rejections == ()
    (k,), (c,) = t.card.conditionals, t.card.commitments
    assert k.state is ConditionalState.ACTIVATED and k.commitment_id == c.id
    assert c.action == "оформить повторную отправку" and c.responsible == IVAN.id
    assert c.source == k.source and c.activated_by.quote == "Посылка так и не пришла"
    assert [n.kind for n in notices(t.card, FRIDAY_END)] == []


def test_an_activated_offer_takes_the_deadline_its_action_was_given():
    t = Ticket()
    offer = "Если посылка не придёт до пятницы, вернём деньги в течение 3 дней"
    t.say(IVAN, offer, MakeConditional(Party.COMPANY, "вернуть деньги", offer, CONDITION))
    t.wait(days=5)
    settle(t, ANNA, "Посылка не пришла, верните деньги", holds=True, quote="Посылка не пришла",
           target="вернём деньги")  # fmt: skip
    (c,) = t.card.commitments
    assert c.due.deadline.due_at.date() == (t.now + timedelta(days=3)).date()


def test_a_lapse_from_the_other_side_ends_the_offer_silently():
    t = offered()
    out = settle(t, ANNA, "Посылка пришла, спасибо.", holds=False, quote="Посылка пришла")
    assert out.rejections == ()
    assert t.card.conditionals[0].state is ConditionalState.LAPSED
    assert notices(t.card, FRIDAY_END) == [] and t.card.commitments == ()


def test_a_lapse_worded_as_the_condition_itself_is_only_a_suggestion():
    t = offered()
    out = settle(t, ANNA, "Посылка так и не пришла.", holds=False)
    assert [r.reason for r in out.rejections] == [Reason.SAYS_CONDITION_HOLDS]
    (k,) = t.card.conditionals
    assert k.pending and k.suggestions[0].kind is Closure.LAPSED


def test_the_side_that_owes_the_offer_cannot_lapse_it():
    t = offered()
    out = settle(t, IVAN, "Посылка вручена.", holds=False)
    assert [r.reason for r in out.rejections] == [Reason.DEBTOR_CANNOT_CANCEL]
    assert t.card.conditionals[0].pending
    assert settle(t, BILLING, "Посылка вручена.", holds=False).rejections == ()


def test_a_lapse_must_name_the_offer_it_ends():
    t = offered()
    k = t.card.conditionals[0]
    out = t.say(ANNA, "Пришло, спасибо.", SettleConditional(k.id, False, "Пришло", "посылка"))
    assert [r.reason for r in out.rejections] == [Reason.NOT_ABOUT_TARGET]
    assert t.card.conditionals[0].pending


def test_a_pending_offer_shows_in_the_brief_and_the_prompt_with_its_source():
    t = offered()
    b = compose(t.card, FRIDAY_END + timedelta(hours=1), cal=CAL)
    (section,) = [s for s in b.sections if s.key == "conditional"]
    (line,) = section.lines
    assert "срок условия прошёл" in line.text and line.sources == (t.card.conditionals[0].source,)
    rendered = render_card(t.card, t.now, CAL)
    assert rendered.aliases == {"O1": t.card.conditionals[0].id}
    assert f'condition: "{CONDITION}"' in rendered.text


def test_a_card_with_offers_is_stored_and_replayed_whole():
    t = offered()
    settle(t, ANNA, "Посылка так и не пришла.", holds=True)
    assert decode_card(encode(t.card)) == t.card
    assert replay("T-1", t.log, CAL) == t.card
