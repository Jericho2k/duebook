import pytest
from helpers import ANNA, BILLING, CAL, IVAN, START, Ticket
from test_extract import FakeModel, output

from duebook import Card, MessagePosted, Reason, apply
from duebook.extract import Extractor
from duebook.extract.prompt import SYSTEM_PROMPT, system_prompt
from duebook.proposals import AssertFact
from duebook.slots import DEFAULT, Vocabulary, fold


def slots(card) -> list[str]:
    return [f.slot for f in card.current_facts()]


# ---------------------------------------------------- the observed drift ----


def test_a_refund_named_two_ways_by_the_model_is_one_fact_not_two():
    """Reproduces the live run: ``order.refund``, then ``order.refund_status``."""
    t = Ticket()
    t.say(IVAN, "Оформил возврат.", AssertFact("order.refund", "оформлен", "Оформил возврат"))
    t.wait(days=1)
    t.say(
        IVAN,
        "Возврат отправлен в банк.",
        AssertFact("order.refund_status", "отправлен в банк", "Возврат отправлен в банк"),
    )
    (current,) = t.card.current_facts()
    assert (current.slot, current.value) == ("refund.status", "отправлен в банк")
    ((old, new),) = t.card.changed_facts()
    assert (old.value, new.value) == ("оформлен", "отправлен в банк")


def test_without_normalisation_the_same_ticket_splits_into_two_unrelated_facts():
    """The fault itself, kept as a test so the fix is seen to be what removes it."""
    t = Ticket(vocab=Vocabulary(canonical={}, filler=frozenset()))
    t.say(IVAN, "Оформил возврат.", AssertFact("order.refund", "оформлен", "Оформил возврат"))
    t.say(
        IVAN,
        "Возврат отправлен в банк.",
        AssertFact("order.refund_status", "отправлен в банк", "Возврат отправлен в банк"),
    )
    assert slots(t.card) == ["order.refund", "order.refund_status"]
    assert t.card.changed_facts() == ()


def test_a_disagreement_is_still_caught_when_the_two_sides_are_filed_under_drifted_names():
    t = Ticket()
    t.say(ANNA, "Возврат не пришёл.", AssertFact("order.refund", "не пришёл", "не пришёл"))
    t.say(IVAN, "Возврат отправлен.", AssertFact("refund_status", "отправлен", "отправлен"))
    (conflict,) = t.card.open_contradictions()
    assert conflict.slot == "refund.status"
    t.say(BILLING, "refund: settled", AssertFact("Refund.State", "settled", "settled"))
    assert t.card.open_contradictions() == () and slots(t.card) == ["refund.status"]


def test_repeating_a_fact_under_a_drifted_name_is_still_a_duplicate():
    t = Ticket()
    t.say(ANNA, "Тариф Про", AssertFact("plan", "Про", "Тариф Про"))
    out = t.say(ANNA, "Да, тариф Про", AssertFact("account.tariff", "Про", "тариф Про"))
    assert [r.reason for r in out.rejections] == [Reason.DUPLICATE]


# ------------------------------------------------------------ the rules ----


@pytest.mark.parametrize(
    ("proposed", "expected"),
    [
        ("order.status", "order.status"),
        ("Order.Status", "order.status"),
        ("order_status", "order.status"),
        ("orderStatus", "order.status"),
        ("order status", "order.status"),
        ("order.refund", "refund.status"),
        ("order.refund_status", "refund.status"),
        ("order.refund.status", "refund.status"),
        ("refund", "refund.status"),
        ("order.id", "order.number"),
        ("plan", "account.plan"),
        ("shipping.address", "delivery.address"),
        ("  ", ""),
    ],
)
def test_spelling_and_known_aliases_lead_to_one_canonical_slot(proposed, expected):
    assert DEFAULT.normalise(proposed) == expected


def test_an_unlisted_slot_joins_the_one_the_card_already_has_for_the_same_thing():
    existing = {"order.gift", "order.number"}
    assert DEFAULT.normalise("order.gift_status", existing) == "order.gift"
    assert DEFAULT.normalise("order.current_gift", existing) == "order.gift"
    assert DEFAULT.normalise("gift.order", existing) == "order.gift"


def test_different_things_are_not_merged_for_sharing_a_word():
    existing = {"order.gift", "delivery.window"}
    assert DEFAULT.normalise("order.gift_wrap", existing) == "order.gift_wrap"
    assert DEFAULT.normalise("delivery.courier", existing) == "delivery.courier"
    assert DEFAULT.normalise("order.number", {"order.status"}) == "order.number"


def test_an_ambiguous_match_is_left_alone_rather_than_guessed():
    assert DEFAULT.normalise("gift", {"gift.status", "gift.state"}) == "gift"


def test_an_unknown_slot_is_kept_in_its_folded_spelling():
    assert DEFAULT.normalise("Warranty-Period") == "warranty.period"
    assert fold("order.Refund  Status") == "order.refund_status"


def test_an_unlisted_slot_does_not_drift_on_a_real_card_either():
    t = Ticket()
    t.say(ANNA, "Гарантия год.", AssertFact("warranty", "год", "Гарантия год"))
    t.say(ANNA, "Точнее, гарантия два года.", AssertFact("warranty_info", "два года", "два года"))
    assert slots(t.card) == ["warranty"] and len(t.card.changed_facts()) == 1


def test_a_vocabulary_that_lists_one_name_under_two_slots_is_refused():
    with pytest.raises(ValueError):
        Vocabulary(canonical={"a.b": ("x",), "c.d": ("x",)})
    with pytest.raises(ValueError):
        Vocabulary(canonical={"Order Status": ()})


# ------------------------------------------------------------ the model ----


def test_the_model_is_shown_the_vocabulary_and_told_to_reuse_slots():
    for slot in DEFAULT.slots():
        assert slot in SYSTEM_PROMPT
    assert "Never use two slots for one thing" in SYSTEM_PROMPT
    assert "{slots}" not in SYSTEM_PROMPT
    custom = Vocabulary(canonical={"policy.number": ("policy.id",)})
    assert "policy.number" in system_prompt(custom) and "refund.status" not in system_prompt(custom)


def test_drifted_names_in_recorded_model_output_end_as_one_slot_on_the_card():
    def fact(slot, value, quote):
        return output(facts=[{"slot": slot, "value": value, "quote": quote, "change": False}])

    model = FakeModel(
        fact("order.refund", "оформлен", "Оформил возврат"),
        fact("order.refund_status", "отправлен в банк", "Возврат отправлен в банк"),
    )
    extractor = Extractor(model, CAL)
    card = Card("T-1")
    for seq, text in enumerate(("Оформил возврат.", "Возврат отправлен в банк."), 1):
        event = MessagePosted(id=f"e{seq}", ticket_id="T-1", seq=seq, at=START, author=IVAN,
                              text=text)  # fmt: skip
        card = apply(card, event, extractor.extract(card, event).proposals, CAL).card
    assert slots(card) == ["refund.status"]
    # The second request shows the model the canonical name, not the one it used.
    assert "refund.status = оформлен" in model.requests[1][1]["content"]
