import json
from datetime import datetime

import pytest
from helpers import story

from duebook import Card
from duebook.store.codec import decode_card, decode_event, decode_proposals, encode


def through_json(value):
    return json.loads(json.dumps(encode(value), ensure_ascii=False))


def test_a_card_survives_storage_unchanged():
    card = story().card
    assert card.facts and card.contradictions and card.commitments and card.steps
    assert decode_card(through_json(card)) == card


def test_events_and_proposals_survive_storage_unchanged():
    for event, proposals in story().log:
        assert decode_event(through_json(event)) == event
        assert decode_proposals(through_json(proposals)) == proposals


def test_a_stored_object_names_its_own_type():
    event, proposals = story().log[1]
    assert encode(event)["type"] == "MessagePosted"
    assert encode(proposals)[0] == {
        "type": "AssertFact", "slot": "order.number", "value": "4815", "quote": "Заказ 4815",
        "change": False,
    }  # fmt: skip


def test_a_field_added_later_takes_its_default_when_reading_an_old_row():
    old = encode(Card("T-1"))
    del old["questions"]
    assert decode_card(old) == Card("T-1")


def test_a_naive_time_is_refused_rather_than_stored_ambiguously():
    with pytest.raises(ValueError):
        encode(datetime(2027, 1, 11, 10, 0))


def test_an_unknown_object_is_refused():
    with pytest.raises(ValueError):
        decode_card({"type": "Nonsense"})
    with pytest.raises(ValueError):
        decode_card(encode(story().log[0][0]))
