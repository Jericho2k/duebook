import json
from datetime import date, time

import httpx
import pytest
from helpers import ANNA, CAL, IVAN, MSK, Ticket

from duebook import CommitmentState, Confidence, MessagePosted, Party, Reason, StepOutcome, apply
from duebook.extract import (
    PRESETS,
    PRICES,
    ChatClient,
    Completion,
    Extractor,
    ModelError,
    ModelSettings,
    Pricing,
    ResponseCache,
    Usage,
    estimate,
    parse_output,
    settings_for,
)
from duebook.extract.extractor import reading_clients
from duebook.extract.prompt import SYSTEM_PROMPT, Bound, build_messages, render_card
from duebook.extract.schema import OUTPUT_SCHEMA, parse_due_expr
from duebook.proposals import (
    AskQuestion,
    AssertFact,
    CancelCommitment,
    MakeCommitment,
    MakeConditional,
    RecordStep,
    ResolveCommitment,
    SetStepOutcome,
    SettleConditional,
)
from duebook.temporal import DateRef, DayRef, Offset, Period, PeriodEnd, Unit, WeekdayRef

NO_DUE = {
    "kind": "none", "amount": None, "unit": None, "days_ahead": None, "weekday": None,
    "week": None, "day": None, "month": None, "year": None, "period": None, "time": None,
}  # fmt: skip


def due(**fields):
    return {**NO_DUE, **fields}


def output(**sections):
    empty = {key: [] for key in OUTPUT_SCHEMA["properties"]}
    return {**empty, **sections}


class FakeModel:
    """Replays prepared responses and records what it was asked."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def complete_json(self, messages, schema, name="output"):
        self.requests.append(messages)
        return Completion(self.responses.pop(0), Usage(1, 900, 80, reported_usd=0.0001))


# --------------------------------------------------------------- schema ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (due(), None),
        (due(kind="offset", amount=3, unit="business_day"), Offset(3, Unit.BUSINESS_DAY)),
        (due(kind="day", days_ahead=1, time="15:00"), DayRef(1, time(15, 0))),
        (due(kind="weekday", weekday="friday"), WeekdayRef(4)),
        (due(kind="weekday", weekday="friday", week=1), WeekdayRef(4, None, 1)),
        (due(kind="date", day=15, month=3), DateRef(15, 3)),
        (due(kind="period_end", period="week"), PeriodEnd(Period.WEEK)),
    ],
)
def test_structured_deadline_is_read(raw, expected):
    assert parse_due_expr(raw) == expected


def test_a_malformed_entry_is_dropped_and_the_rest_is_kept():
    raw = output(
        facts=[
            {"slot": "plan", "value": "Про", "quote": "тариф Про", "change": False},
            {"slot": "plan", "value": 5, "quote": "x", "change": False},
        ],
        commitments=[
            {
                "owner": "nobody", "action": "a", "quote": "q",
                "due_quote": None, "due": due(), "supersedes": None,
            },
            {
                "owner": "company", "action": "вернуть деньги", "quote": "вернём",
                "due_quote": None, "due": due(kind="offset", unit="hour"), "supersedes": None,
            },
        ],
    )  # fmt: skip
    proposals, malformed = parse_output(raw)
    assert [type(p).__name__ for p in proposals] == ["AssertFact"]
    assert [m.section for m in malformed] == ["facts", "commitments", "commitments"]


def test_a_close_names_its_target_twice_by_handle_and_by_its_wording():
    raw = output(
        resolved=[{"id": "C1", "target_quote": "вернём деньги", "quote": "деньги пришли"}],
        cancelled=[{"id": "C2", "target_quote": "замену", "quote": "не нужно"}],
    )
    proposals, malformed = parse_output(raw, {"C1": "e1#0", "C2": "e2#0"})
    assert malformed == [] and proposals == [
        ResolveCommitment("e1#0", "деньги пришли", "вернём деньги"),
        CancelCommitment("e2#0", "не нужно", "замену"),
    ]
    required = OUTPUT_SCHEMA["properties"]["resolved"]["items"]["required"]
    assert required == ["id", "target_quote", "quote", "context_quote"]


def test_the_instructions_say_bare_thanks_confirms_nothing():
    assert "Bare thanks" in SYSTEM_PROMPT and "confirms nothing" in SYSTEM_PROMPT
    assert "thanks that imply" not in SYSTEM_PROMPT


def test_the_instructions_make_a_side_accountable_for_work_it_delegates():
    assert "whoever does the work" in SYSTEM_PROMPT and "Курьер заберёт товар" in SYSTEM_PROMPT
    assert "NOT commitments" in SYSTEM_PROMPT and "offer.conditional" not in SYSTEM_PROMPT


def test_a_conditional_offer_and_its_outcome_are_read_into_their_own_proposals():
    offer = {"owner": "company", "action": "вернуть деньги", "quote": "если не придёт, вернём",
             "condition": "если не придёт", "due_quote": None, "due": NO_DUE}
    outcome = {"id": "O1", "outcome": "failed", "target_quote": "вернём", "quote": "пришла"}
    raw = output(conditionals=[offer], condition_outcomes=[outcome])
    proposals, malformed = parse_output(raw, {"O1": "e1#0"})
    assert malformed == []
    assert proposals == [
        MakeConditional(Party.COMPANY, "вернуть деньги", "если не придёт, вернём",
                        "если не придёт"),
        SettleConditional("e1#0", False, "пришла", "вернём"),
    ]  # fmt: skip


def test_handles_shown_to_the_model_map_back_to_real_ids():
    raw = output(resolved=[{"id": "c1", "quote": "готово"}, {"id": "C9", "quote": "готово"}])
    proposals, _ = parse_output(raw, {"C1": "e1#0"})
    assert proposals == [ResolveCommitment("e1#0", "готово"), ResolveCommitment("C9", "готово")]


def test_schema_is_strict_everywhere():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(OUTPUT_SCHEMA)


# --------------------------------------------------------------- prompt ----


def test_the_model_sees_the_card_and_one_message_never_the_transcript():
    t = Ticket()
    t.assign(None, IVAN.id)
    t.say(
        IVAN,
        "СЕКРЕТНОЕ-СЛОВО. Мы вернём деньги до пятницы. Переустановите приложение. Какой заказ?",
        MakeCommitment(Party.COMPANY, "вернуть деньги", "вернём деньги до пятницы"),
        RecordStep("переустановить приложение", "Переустановите приложение"),
        AskQuestion("какой заказ?", "Какой заказ?"),
    )
    rendered = render_card(t.card, t.now, CAL)
    assert set(rendered.aliases) == {"C1", "S1", "Q1"}
    assert rendered.aliases["C1"] == t.card.commitments[0].id
    assert "C1 [company, open] вернуть деньги | due 2027-01-15 23:59" in rendered.text
    # The words it was made in, for the model to copy when it closes it.
    assert '| said: "вернём деньги до пятницы"' in rendered.text

    event = MessagePosted(
        id="e9", ticket_id="T-1", seq=9, at=t.now, author=ANNA, text="Заказ 4815", public=True
    )
    messages, _ = build_messages(t.card, event, CAL)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "Sent: Monday 2027-01-11 10:00 (Europe/Moscow)" in messages[1]["content"]
    assert messages[1]["content"].endswith("Заказ 4815")
    assert "СЕКРЕТНОЕ-СЛОВО" not in messages[1]["content"]


def long_card():
    """A card grown long: many settled steps, many open questions, many facts."""
    t = Ticket()
    t.assign(None, IVAN.id)
    for n in range(1, 21):
        t.say(ANNA, f"Заказ {n}000. Есть новости {n}?",
              AssertFact(f"order.item_{n}", f"{n}000", f"Заказ {n}000"),
              AskQuestion(f"Есть новости {n}?", f"Есть новости {n}?"))  # fmt: skip
        t.say(IVAN, f"Попробуйте шаг {n}.", RecordStep(f"шаг {n}", f"Попробуйте шаг {n}"))
        step = t.card.steps[-1].id
        if n < 18:  # the last three stay pending
            t.say(ANNA, "Не помогло.", SetStepOutcome(step, StepOutcome.FAILED, "Не помогло"))
    t.say(IVAN, "Вернём деньги до пятницы.",
          MakeCommitment(Party.COMPANY, "вернуть деньги", "Вернём деньги до пятницы"))  # fmt: skip
    t.say(ANNA, "У меня тариф Лайт.", AssertFact("account.plan", "Лайт", "тариф Лайт"))
    t.say(IVAN, "У вас тариф Про.", AssertFact("account.plan", "Про", "тариф Про"))
    return t


def test_a_bound_caps_history_and_says_how_much_it_left_out():
    t = long_card()
    full = render_card(t.card, t.now, CAL)
    bounded = render_card(t.card, t.now, CAL, Bound(facts=5, settled_steps=4, questions=3))
    assert len(bounded.text) < len(full.text) / 2
    assert "(+17 older facts not shown)" in bounded.text  # 22 current, the 5 latest kept
    assert "(+13 earlier steps with a known outcome not shown)" in bounded.text
    assert "(+17 older open questions not shown)" in bounded.text


def test_a_bound_never_hides_what_the_model_can_act_on_or_a_disputed_fact():
    t = long_card()
    bounded = render_card(t.card, t.now, CAL, Bound(facts=1, settled_steps=0, questions=1))
    # Every unresolved commitment and every pending step keeps its handle.
    commitment = next(c for c in t.card.commitments if c.unresolved)
    assert bounded.aliases["C1"] == commitment.id
    pending = [s for s in t.card.steps if s.outcome is StepOutcome.PENDING]
    assert len(pending) == 3 and all(s.id in bounded.aliases.values() for s in pending)
    # Both sides of the open dispute are shown, though only one fact fits.
    assert "account.plan = Лайт" in bounded.text and "account.plan = Про" in bounded.text


def test_a_handle_names_the_same_item_with_or_without_a_bound():
    t = long_card()
    full = render_card(t.card, t.now, CAL)
    bounded = render_card(t.card, t.now, CAL, Bound(settled_steps=2, questions=2))
    for alias, target in bounded.aliases.items():
        assert full.aliases[alias] == target


def test_without_a_bound_the_card_is_shown_whole():
    t = long_card()
    rendered = render_card(t.card, t.now, CAL)
    assert "not shown" not in rendered.text
    assert len([a for a in rendered.aliases if a.startswith("S")]) == 20


# ------------------------------------------------------------ extractor ----


def test_extraction_flows_through_the_reducer_end_to_end():
    promise = output(
        commitments=[
            {
                "owner": "company", "action": "вернуть деньги",
                "quote": "вернём деньги до пятницы", "due_quote": "до пятницы",
                "due": due(kind="weekday", weekday="friday"), "supersedes": None,
            },
            {
                "owner": "company", "action": "подарить промокод",
                "quote": "мы подарим вам промокод", "due_quote": None,
                "due": due(), "supersedes": None,
            },
        ]
    )  # fmt: skip
    confirmation = output(
        resolved=[{"id": "C1", "target_quote": "вернём деньги", "quote": "деньги пришли"}]
    )
    extractor = Extractor(FakeModel(promise, confirmation), CAL)

    t = Ticket()
    first = MessagePosted(
        id="e1", ticket_id="T-1", seq=1, at=t.now, author=IVAN,
        text="Анна, мы вернём деньги до пятницы.",
    )  # fmt: skip
    extraction = extractor.extract(t.card, first)
    outcome = apply(t.card, first, extraction.proposals, CAL)

    # The model invented a promise the message never made; the reducer refused it.
    assert [r.reason for r in outcome.rejections] == [Reason.UNGROUNDED]
    (c,) = outcome.card.commitments
    assert c.due.confidence is Confidence.HIGH
    assert c.due.deadline.due_at.astimezone(MSK).date() == date(2027, 1, 15)

    second = MessagePosted(
        id="e2", ticket_id="T-1", seq=2, at=t.now, author=ANNA, text="Спасибо, деньги пришли!"
    )
    extraction = extractor.extract(outcome.card, second)
    card = apply(outcome.card, second, extraction.proposals, CAL).card
    assert card.commitments[0].state is CommitmentState.FULFILLED
    assert extraction.usage.reported_usd == 0.0001


def test_events_without_text_cost_nothing():
    from duebook import AssigneeChanged

    model = FakeModel()
    t = Ticket()
    event = AssigneeChanged(id="e1", ticket_id="T-1", seq=1, at=t.now, previous=None, current="x")
    assert Extractor(model, CAL).extract(t.card, event).proposals == ()
    assert model.requests == []


# --------------------------------------------------------------- client ----


def chat_response(content, cost=0.00012, provider="DeepInfra", reasoning=0):
    usage = {"prompt_tokens": 1000, "completion_tokens": 50, "cost": cost}
    usage["prompt_tokens_details"] = {"cached_tokens": 800}
    usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    body = {"choices": [{"message": {"content": content}}], "usage": usage, "provider": provider}
    return httpx.Response(200, json=body)


def client_for(handler, **options):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ChatClient(api_key="test-key", http=http, sleep=lambda _: None, **options)


def test_client_sends_a_strict_schema_and_reads_usage():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return chat_response('{"facts": []}')

    completion = client_for(handler).complete_json([{"role": "user", "content": "x"}], {"a": 1})
    assert completion.data == {"facts": []}
    assert completion.usage == Usage(1, 1000, 50, 800, 0.00012)
    assert completion.provider == "DeepInfra" and completion.usage.cache_share == 0.8
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert seen["body"]["usage"] == {"include": True}
    # The default reader, with reasoning off and locked to one host.
    assert seen["body"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert seen["body"]["provider"] == {
        "require_parameters": True, "order": ["deepinfra/fp8"], "allow_fallbacks": False,
    }  # fmt: skip
    assert seen["body"]["reasoning"] == {"enabled": False}
    assert seen["body"]["temperature"] == 0.0


def test_each_model_carries_its_own_reasoning_and_routing():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return chat_response("{}")

    for model in ("z-ai/glm-5.3-flash", "some/other-model"):
        client_for(handler, model=model).complete_json([{"role": "user", "content": "x"}], {})
    glm, other = bodies
    assert glm["reasoning"] == {"effort": "low", "exclude": True}
    assert glm["provider"] == {"require_parameters": True}
    assert "reasoning" not in other and other["provider"] == {"require_parameters": True}
    custom = ModelSettings(reasoning={"effort": "high"}, provider={"order": ["x"]})
    client_for(handler, settings=custom).complete_json([{"role": "user", "content": "x"}], {})
    assert bodies[-1]["reasoning"] == {"effort": "high"}
    assert bodies[-1]["provider"] == {"require_parameters": True, "order": ["x"]}
    assert settings_for("deepseek/deepseek-v4.1-flash").reasoning == {"enabled": False}


def test_one_model_can_be_asked_at_another_reasoning_effort_and_stays_locked():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return chat_response("{}", reasoning=900)

    completion = client_for(handler, reasoning="low").complete_json([], {})
    assert bodies[0]["reasoning"] == {"effort": "low", "exclude": True}
    assert bodies[0]["provider"]["order"] == ["deepinfra/fp8"]
    assert bodies[0]["provider"]["allow_fallbacks"] is False
    assert completion.usage.reasoning_tokens == 900
    client_for(handler, reasoning="off").complete_json([], {})
    assert bodies[1]["reasoning"] == {"enabled": False}
    with pytest.raises(ValueError, match="unknown reasoning"):
        client_for(handler, reasoning="medium").complete_json([], {})


def test_the_writer_is_locked_to_its_three_hosts():
    writer = settings_for("~z-ai/glm-flash-latest")
    assert dict(writer.provider) == {
        "order": ("gmicloud/fp8", "tenstorrent/fp8", "novita/fp8"), "allow_fallbacks": False,
    }  # fmt: skip
    assert writer.reasoning == {"enabled": False}
    assert set(PRESETS) >= {"deepseek/deepseek-v4.1-flash", "~z-ai/glm-flash-latest"}


def test_a_deterministic_request_is_answered_from_disk_the_second_time(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return chat_response('{"facts": [1]}', cost=0.0002)

    cache = ResponseCache(tmp_path)
    ask = [{"role": "user", "content": "x"}]
    first = client_for(handler, cache=cache).complete_json(ask, {})
    again = client_for(handler, cache=cache).complete_json(ask, {})
    assert len(calls) == 1 and not first.cached and again.cached
    assert again.data == first.data and again.usage == first.usage  # what it cost when made
    other = client_for(handler, cache=cache).complete_json([{"role": "user", "content": "y"}], {})
    assert len(calls) == 2 and not other.cached


def test_a_request_at_a_higher_temperature_is_never_cached(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return chat_response("{}")

    cache = ResponseCache(tmp_path)
    for _ in range(2):
        client_for(handler, cache=cache, temperature=0.8).complete_json([], {})
    assert len(calls) == 2 and not any(tmp_path.iterdir())


def test_a_failed_request_is_not_cached(tmp_path):
    answers = iter([httpx.Response(200, json={"choices": [{"message": {"content": "no"}}]}),
                    chat_response("{}")] * 2)  # fmt: skip
    client = client_for(lambda request: next(answers), cache=ResponseCache(tmp_path),
                        content_retries=0)  # fmt: skip
    with pytest.raises(ModelError):
        client.complete_json([], {})
    assert not any(tmp_path.iterdir())


def test_client_works_against_any_compatible_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return chat_response("```json\n{}\n```")

    client = client_for(
        handler, base_url="http://localhost:8000/v1/", model="local", structured="json_object"
    )
    assert client.complete_json([], {}).data == {}
    assert seen["url"] == "http://localhost:8000/v1/chat/completions"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert not {"usage", "provider", "reasoning"} & set(seen["body"])


def test_client_retries_a_429_once_and_a_bad_answer_once_and_counts_what_each_cost():
    replies = iter([httpx.Response(429, text="slow down"), chat_response(""),
                    chat_response('{"ok": true}')])  # fmt: skip
    completion = client_for(lambda _: next(replies)).complete_json([], {})
    assert completion.data == {"ok": True}
    assert completion.usage.calls == 2
    assert [p.split(":")[0] for p in completion.retried] == ["http 429", "empty content"]
    assert completion.usage.reported_usd == pytest.approx(0.00024)


def timeout(request):
    raise httpx.ReadTimeout("too slow", request=request)


@pytest.mark.parametrize(
    ("replies", "why", "calls"),
    [
        ([httpx.Response(503), httpx.Response(502)], "http 502", 2),
        ([chat_response(""), chat_response("not json")], "content is not valid JSON", 2),
        ([httpx.Response(200, json=[]), chat_response([1])], "message content is not text", 2),
        # A timeout may already have been generated and billed.
        ([timeout], "timeout, not retried", 1),
        ([httpx.Response(401, text="bad key")], "http 401", 1),
    ],
)
def test_a_fault_is_retried_once_unless_retrying_cannot_help(replies, why, calls):
    sent = []

    def handler(request):
        reply = replies[len(sent)]
        sent.append(1)
        return reply(request) if callable(reply) else reply

    with pytest.raises(ModelError, match=why):
        client_for(handler).complete_json([], {})
    assert len(sent) == calls


def test_an_answer_cut_off_at_the_output_limit_is_unreadable_and_not_asked_again():
    calls = []

    def handler(request):
        calls.append(1)
        response = chat_response('{"facts": [')
        body = response.json()
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    with pytest.raises(ModelError, match="cut off at 800 tokens") as failure:
        client_for(handler, max_tokens=800, content_retries=0).complete_json([], {})
    assert failure.value.unreadable and len(calls) == 1


def test_an_unreadable_answer_is_read_again_once_with_reasoning(monkeypatch):
    sent = []

    def post(client, body):
        sent.append((body["max_tokens"], body["reasoning"]))
        if len(sent) == 1:
            return chat_response("not json")
        return chat_response(json.dumps(output(questions=[{"text": "Какое первым?",
                                                           "quote": "какое получите первым"}])))

    monkeypatch.setattr(ChatClient, "_post", post)
    first, escalation = reading_clients(ChatClient("test-key"))
    t = Ticket()
    event = MessagePosted(id="e1", ticket_id="T-1", seq=1, at=t.now, author=IVAN,
                          text="Напишите, какое получите первым.")  # fmt: skip
    extraction = Extractor(first, escalation=escalation).extract(t.card, event)
    assert sent == [(800, {"enabled": False}), (4000, {"effort": "low", "exclude": True})]
    assert [type(p).__name__ for p in extraction.proposals] == ["AskQuestion"]
    assert extraction.usage.calls == 2 and "not valid JSON" in extraction.retried[0]


def test_a_failure_that_is_not_about_the_answer_is_not_escalated(monkeypatch):
    sent = []

    def post(client, body):
        sent.append(body["max_tokens"])
        raise httpx.ReadTimeout("stub timeout")

    monkeypatch.setattr(ChatClient, "_post", post)
    first, escalation = reading_clients(ChatClient("test-key"))
    t = Ticket()
    event = MessagePosted(id="e1", ticket_id="T-1", seq=1, at=t.now, author=IVAN,
                          text="Проверю и отвечу.")  # fmt: skip
    with pytest.raises(ModelError, match="timeout") as failure:
        Extractor(first, escalation=escalation).extract(t.card, event)
    assert sent == [800] and not failure.value.unreadable


def test_client_gives_up_loudly_and_reports_the_spend():
    with pytest.raises(ModelError) as failure:
        client_for(lambda _: chat_response("")).complete_json([], {})
    assert "empty content" in str(failure.value)
    assert failure.value.usage.calls == 2
    assert failure.value.provider == "DeepInfra"


# ----------------------------------------------------------------- cost ----


def test_cost_prefers_what_was_charged_and_falls_back_to_the_price_list():
    pricing = Pricing(1.0, 2.0, cached_input_per_m=0.1)
    assert Usage(1, 1000, 100, reported_usd=0.5).usd(pricing) == 0.5
    assert Usage(1, 1_000_000, 500_000, cached_tokens=400_000).usd(pricing) == pytest.approx(
        0.6 + 0.04 + 1.0
    )
    assert Usage(1, 10, 10).usd() is None
    assert (Usage(1, 10, 5) + Usage(1, 20, 5, reported_usd=0.1)).reported_usd == 0.1


def test_incremental_cost_is_linear_and_rereading_is_quadratic():
    pricing = PRICES["z-ai/glm-5.3-flash"]
    short, long = estimate(50, pricing), estimate(500, pricing)
    assert long.incremental_usd == pytest.approx(short.incremental_usd * 10)
    assert long.reread_usd > short.reread_usd * 40
    assert long.ratio > short.ratio
