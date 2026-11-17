"""The baselines: what they are shown, how their answers are read, what they cost."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from duebook import Side, WorkCalendar
from duebook.bench import MAIN, Fate, generate
from duebook.bench.baselines import (
    ANSWER_SCHEMA,
    FullContext,
    RollingSummary,
    parse_answer,
    parse_due_at,
    render_event,
)
from duebook.bench.baselines.graphiti_memory import cacheable, listing_of
from duebook.bench.baselines.mem0_memory import _line, usage_of
from duebook.bench.meter import Ledger, Meter, OverBudget
from duebook.bench.result import PromiseStatus, checkpoints
from duebook.bench.score import Score, fact_status, score_ticket
from duebook.extract import Completion, ModelError, Usage

MSK = ZoneInfo("Europe/Moscow")
CAL = WorkCalendar(tz=MSK)
TICKETS = generate(seed=5, count=8) + generate(seed=5, count=2, profile=MAIN)
CALL = Usage(1, 1000, 100, 0, 0.0002)


STATUS = {Fate.KEPT: "fulfilled", Fate.CANCELLED: "cancelled", Fate.RENEGOTIATED: "superseded"}


def perfect_answer(ticket, cp):
    """What a reader that missed nothing would answer at a checkpoint."""
    gold, seq = ticket.gold, cp.seq

    def due(at):
        if at is None:
            return None
        local = at.astimezone(MSK)
        return f"{local:%Y-%m-%d}" if local.second == 59 else f"{local:%Y-%m-%d %H:%M}"

    return {
        "commitments": [
            {
                "owner": "customer" if c.owner.side is Side.CUSTOMER else "company",
                "source_seq": c.seq,
                "action": c.action,
                "due": due(c.due_at),
                "status": STATUS[c.fate] if c.closed(seq) else "open",
                "closed_seq": c.resolved_seq if c.closed(seq) else None,
            }
            for c in gold.commitments
            if c.seq <= seq
        ],
        "current_facts": [
            {"name": f.slot, "value": f.value, "source_seq": f.seq}
            for f in gold.facts
            if fact_status(ticket, f, seq) == "current"
        ],
        "failed_steps": [
            {"action": f.action, "suggested_seq": f.suggested_seq, "failed_seq": f.failed_seq}
            for f in gold.repeated_fixes
            if f.failed_seq <= seq
        ],
        "disputes": [
            {
                "topic": d.slot,
                "customer_says": d.values[0],
                "company_says": d.values[1],
                "customer_seq": d.seqs[0],
                "company_seq": d.seqs[1],
            }
            for d in gold.contradictions
            if d.open_at(seq)
        ],
    }


class Scripted:
    """Stands in for the model. Answers checkpoint questions with ``answer``."""

    def __init__(self, ticket, answer=perfect_answer, fail_on=None):
        self.ticket, self.answer, self.fail_on = ticket, answer, fail_on
        self.requests = []

    def complete_json(self, messages, schema, name="output"):
        self.requests.append((name, messages))
        if self.fail_on and self.fail_on(name, messages):
            raise ModelError("http 503", CALL)
        if name == "summary":
            return Completion({"summary": f"summary after {len(self.requests)} calls"}, CALL)
        assert schema is ANSWER_SCHEMA
        now = messages[1]["content"]
        cp = next(c for c in checkpoints(self.ticket) if f"up to #{c.seq} have" in now)
        return Completion(self.answer(self.ticket, cp), CALL)


# --------------------------------------------------------- the answers ----


def test_a_day_only_deadline_is_the_end_of_that_day_in_the_tickets_timezone():
    assert parse_due_at("2027-01-15", CAL) == datetime(2027, 1, 15, 23, 59, 59, tzinfo=MSK)
    assert parse_due_at("2027-01-15 15:00", CAL) == datetime(2027, 1, 15, 15, tzinfo=MSK)
    assert parse_due_at("2027-01-15T12:00:00Z", CAL) == datetime(2027, 1, 15, 15, tzinfo=MSK)
    with pytest.raises(ValueError):
        parse_due_at("next Friday", CAL)


def test_a_malformed_entry_is_dropped_and_counted_and_the_rest_is_kept():
    cp = checkpoints(TICKETS[0])[-1]
    raw = {
        "commitments": [
            {"owner": "company", "source_seq": 3, "action": "a", "due": "пятница",
             "status": "open"},
            {"owner": "customer", "source_seq": 4, "action": "b", "due": None,
             "status": "claimed"},
        ],
        "current_facts": [{"name": "plan", "value": "Премиум", "source_seq": None}],
        "failed_steps": "none",
        "disputes": [],
    }  # fmt: skip
    read = parse_answer(raw, cp, CAL)
    assert len(read.malformed) == 2
    (promise,) = read.snapshot.promises
    assert (promise.side, promise.seq, promise.status) == (Side.CUSTOMER, 4, PromiseStatus.CLAIMED)
    assert read.snapshot.facts[0].value == "Премиум"


def test_each_event_is_shown_with_its_number_local_time_and_author():
    t = TICKETS[0]
    first = render_event(t.events[0], CAL)
    assert first.startswith("#1 · ") and "customer (" in first and t.events[0].text in first
    handoff = render_event(t.events[t.gold.handoffs[0] - 1], CAL)
    assert "reassigned" in handoff


# ------------------------------------------------- perfect is perfect ----


@pytest.mark.parametrize("method", [FullContext, RollingSummary])
def test_a_baseline_that_answers_perfectly_scores_perfectly(method):
    total = Score()
    for t in TICKETS:
        run = method(Scripted(t), Ledger()).run(t)
        assert run.failure is None
        total += score_ticket(t, run.snapshots).all
    assert total.commitments.f1 == total.deadline_accuracy == total.status_accuracy == 1.0
    assert total.alerts.f1 == total.change_accuracy == 1.0
    assert total.disputes.f1 == total.failed_steps.f1 == 1.0
    assert total.freshness_error == 0.0


# ----------------------------------------------- what each one is shown ----


def test_full_context_shows_every_event_up_to_the_checkpoint_and_nothing_after():
    t = TICKETS[0]
    model = Scripted(t)
    run = FullContext(model, Ledger()).run(t)
    assert len(model.requests) == len(checkpoints(t)) == len(run.snapshots)
    for (_, messages), cp in zip(model.requests, checkpoints(t), strict=True):
        shown = messages[1]["content"]
        assert f"\n#{cp.seq} · " in shown and f"\n#{cp.seq + 1} · " not in shown
        assert "TRANSCRIPT" in shown
    assert run.ingest_usage == Usage() and run.answer_usage.calls == len(checkpoints(t))


def test_the_rolling_summary_reads_each_event_once_and_answers_from_the_summary_alone():
    t = TICKETS[0]
    model = Scripted(t)
    run = RollingSummary(model, Ledger()).run(t)
    updates = [m for name, m in model.requests if name == "summary"]
    answers = [m for name, m in model.requests if name == "ticket_state"]
    assert len(updates) == len(t.events) and len(answers) == len(checkpoints(t))
    for messages in updates:
        assert messages[1]["content"].count("\n#") == 1  # one new event, nothing else
    for messages in answers:
        assert "SUMMARY" in messages[1]["content"]
        assert t.events[0].text not in messages[1]["content"]
    assert run.ingest_usage.calls == len(t.events)


def test_a_failed_summary_update_stops_the_ticket_and_later_answers_say_so():
    t = TICKETS[0]
    stop = t.gold.handoffs[0] - 3

    def fail(name, messages):
        return name == "summary" and f"\n#{stop} · " in messages[1]["content"]

    run = RollingSummary(Scripted(t, fail_on=fail), Ledger()).run(t)
    assert run.failure.startswith(f"seq {stop}:")
    assert all(s.error for s in run.snapshots)
    assert score_ticket(t, run.snapshots).all.errors == len(checkpoints(t))


# ------------------------------------------------------------- metering ----


def test_the_ledger_refuses_a_call_once_the_cap_is_reached():
    ledger = Ledger(cap_usd=0.0005)
    meter = Meter(Scripted(TICKETS[0]), ledger)
    for _ in range(3):
        meter.complete_json([{}, {"content": ""}], {}, name="summary")
    with pytest.raises(OverBudget):
        meter.complete_json([{}, {"content": ""}], {}, name="summary")
    assert ledger.spent.calls == 3 and meter.tally.ingest.calls == 3


def test_an_answer_from_the_response_cache_is_counted_for_the_method_but_not_charged():
    class Remembered:
        def complete_json(self, messages, schema, name="output"):
            return Completion({}, Usage(1, 100, 10, 0, 0.002), cached=True, provider="DeepInfra")

    ledger = Ledger(cap_usd=0.001)
    meter = Meter(Remembered(), ledger)
    for _ in range(3):  # over the cap if charged, and it is not
        meter.complete_json([], {})
    assert ledger.usd() == 0 and ledger.reused.calls == 3
    assert meter.tally.usage.reported_usd == pytest.approx(0.006) and meter.tally.cache_hits == 3
    assert meter.tally.providers == {"DeepInfra": 3}


def test_a_run_that_hits_the_cap_records_the_failure_instead_of_crashing():
    t = TICKETS[0]
    run = FullContext(Scripted(t), Ledger(cap_usd=0.0001)).run(t)
    assert "OverBudget" in run.failure
    assert run.snapshots[-1].error and run.usage.calls == 1


def test_mem0_usage_and_memories_are_read_from_its_own_client_responses():
    class U:
        def model_dump(self):
            return {"prompt_tokens": 10, "completion_tokens": 2, "cost": 1e-5}

    class R:
        usage = U()

    assert usage_of(R()) == Usage(1, 10, 2, 0, 1e-5)
    assert _line({"memory": "Клиент на тарифе Премиум", "metadata": {"seq": 7}}) == (
        "- (added at #7) Клиент на тарифе Премиум"
    )


def test_the_event_memory_spends_only_while_reading_and_answers_from_the_card_for_free():
    from duebook.bench.methods import EventMemory
    from duebook.extract import OUTPUT_SCHEMA

    class Silent:
        def complete_json(self, messages, schema, name="output"):
            return Completion({k: [] for k in OUTPUT_SCHEMA["properties"]}, CALL)

    t = TICKETS[0]
    run = EventMemory(Silent(), Ledger()).run(t)
    assert run.ingest_usage.calls == t.messages and run.answer_usage == Usage()
    assert len(run.snapshots) == len(checkpoints(t)) and run.failure is None
    messages = [e.seq for e in t.events if hasattr(e, "text")]
    assert [seq for seq, _ in run.detail["prompt_tokens"]] == messages


def test_the_baselines_import_without_mem0_or_graphiti_installed():
    check = ("import sys, duebook.bench.baselines; "
             "sys.exit('mem0' in sys.modules or 'graphiti_core' in sys.modules)")  # fmt: skip
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    assert subprocess.run([sys.executable, "-c", check], env=env).returncode == 0


def test_mem0_is_given_its_memory_list_as_objects_when_the_model_wrote_strings():
    from types import SimpleNamespace

    from duebook.bench.baselines.mem0_memory import _repair

    raw = '```json\n{"memory": ["Клиент ждёт возврат", 3, {"id": "1", "text": "x"}]}\n```'
    message = SimpleNamespace(content=raw)
    _repair(SimpleNamespace(choices=[SimpleNamespace(message=message)]))
    assert json.loads(message.content)["memory"] == [
        {"id": "0", "text": "Клиент ждёт возврат"},
        {"id": "1", "text": "x"},
    ]
    fine = SimpleNamespace(content='{"memory": [{"id": "0", "text": "y"}]}')
    _repair(SimpleNamespace(choices=[SimpleNamespace(message=fine)]))
    assert fine.content == '{"memory": [{"id": "0", "text": "y"}]}'


def test_graphiti_facts_are_listed_by_first_message_with_their_sources_and_validity():
    from types import SimpleNamespace as Edge

    utc = ZoneInfo("UTC")
    old = Edge(fact="Клиент на тарифе Базовый", episodes=["e2", "e5"], created_at=1,
               valid_at=datetime(2027, 1, 11, 7, 0, tzinfo=utc),
               invalid_at=datetime(2027, 1, 12, 9, 30), expired_at=None)
    new = Edge(fact="Клиент на тарифе Про", episodes=["e7", "unknown"], created_at=2,
               valid_at=None, invalid_at=None, expired_at=datetime(2027, 1, 13))
    entity = Edge(name="Анна", summary="клиент")
    listing = listing_of([new, old], [entity], {"e2": 2, "e5": 5, "e7": 7}, CAL)
    assert listing.splitlines() == [
        "FACTS",
        "- (from #2, #5) Клиент на тарифе Базовый [valid from Mon 2027-01-11 10:00; "
        "no longer true from Tue 2027-01-12 12:30]",
        "- (from #7) Клиент на тарифе Про [retired]",
        "ENTITIES",
        "- Анна: клиент",
    ]  # fmt: skip
    assert listing_of([], [], {}, CAL) == "(empty graph)"


def test_graphiti_caches_embeddings_and_only_deterministic_answers():
    assert cacheable("embeddings", {})
    assert cacheable("chat", {"temperature": 0})
    assert not cacheable("chat", {"temperature": 0.7})
    assert not cacheable("chat", {})
