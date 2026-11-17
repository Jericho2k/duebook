"""The experiment runner: cached rewriting, the summary with intervals, the record."""

import json
from dataclasses import replace

import pytest
from test_baselines import Scripted

from duebook.bench import MAIN, Fate, Oracle, generate
from duebook.bench.baselines import FullContext
from duebook.bench.experiment import (
    GROWTH,
    METRICS,
    guard_effects,
    main,
    project,
    record,
    rewritten,
    sample_of,
    spent_before,
    summarise,
    table,
    to_json,
    unguarded,
)
from duebook.bench.generate import SPLITS
from duebook.bench.meter import Ledger, Tally
from duebook.bench.methods import EventMemory, MethodRun
from duebook.bench.result import PromiseStatus, checkpoints
from duebook.bench.run import fold, snapshots
from duebook.bench.score import score_ticket
from duebook.extract import Completion, Usage
from duebook.extract.extractor import Extraction
from duebook.extract.schema import OUTPUT_SCHEMA
from duebook.proposals import ResolveCommitment
from duebook.store.codec import decode_proposals, encode
from duebook.temporal import WorkCalendar

TICKETS = generate(seed=5, count=6)
MSK = WorkCalendar().tz


def oracle_run(t, name="event_memory"):
    folded = fold(t, Oracle.of([t]), [cp.seq for cp in checkpoints(t)])
    return MethodRun.of(name, t, snapshots(t, folded), Tally(ingest=Usage(1, 10, 1, 0, 0.001)))


def test_a_perfect_method_gets_a_perfect_summary_with_a_degenerate_interval():
    runs = [oracle_run(t) for t in TICKETS]
    summary = summarise(TICKETS, runs, boot=200)
    rows = summary["methods"]["event_memory"]
    for name in ("commitment_f1", "deadline_accuracy", "dispute_recall", "failed_step_recall"):
        assert rows[name]["value"] == 1.0 and rows[name]["ci95"] == [1.0, 1.0]
    assert rows["false_alerts_per_ticket"]["value"] == 0.0
    assert rows["usd_per_ticket"]["value"] == pytest.approx(0.001)
    assert set(METRICS) <= set(rows)


def test_a_worse_method_is_compared_with_a_paired_interval():
    good = [oracle_run(t) for t in TICKETS]

    def reopen(run):
        snaps = tuple(
            replace(s, promises=tuple(replace(p, status=PromiseStatus.OPEN) for p in s.promises))
            for s in run.snapshots
        )
        return replace(run, method="worse", snapshots=snaps)

    summary = summarise(TICKETS, good + [reopen(r) for r in good], boot=200)
    worse = summary["methods"]["worse"]
    assert worse["status_accuracy"]["value"] < 1.0
    assert worse["status_accuracy"]["diff_vs_reference"] < 0
    assert worse["status_accuracy"]["diff_ci95"][1] <= 0
    assert worse["false_alerts_per_ticket"]["value"] > 0
    rendered = table(summary, ["status_accuracy", "false_alerts_per_ticket"])
    assert rendered.count("\n") == 3 and "worse" in rendered


def test_a_record_holds_what_the_method_said_and_is_plain_json():
    t = TICKETS[0]
    run = FullContext(Scripted(t), Ledger()).run(t)
    rec = record(run, score_ticket(t, run.snapshots))
    assert json.loads(json.dumps(to_json(rec)))["snapshots"][0]["checkpoint"]["seq"] > 0
    assert rec["detail"]["answers"] and rec["score_end"]["checkpoints"] == 1


def test_tickets_are_rewritten_once_and_then_read_from_the_cache(tmp_path):
    class Writer:
        calls = 0

        def complete_json(self, messages, schema, name="output"):
            Writer.calls += 1
            return Completion({"text": "Короче, " + messages[1]["content"]}, Usage(1, 5, 5))

    split = replace(SPLITS["dev"], count=2)
    import duebook.bench.experiment as experiment

    original = experiment.ChatClient
    experiment.ChatClient = lambda **kw: Writer()
    try:
        first, _ = rewritten(split, "some/writer", Ledger(), tmp_path)
        calls = Writer.calls
        second, stored = rewritten(split, "some/writer", Ledger(), tmp_path)
    finally:
        experiment.ChatClient = original
    assert Writer.calls == calls == sum(t.messages for t in first)
    assert first == second and stored["writer"] == "some/writer"
    assert all(e.text.startswith("Короче, ") for e in first[0].events if hasattr(e, "text"))
    with pytest.raises(SystemExit):
        rewritten(split, "another/writer", Ledger(), tmp_path)


def test_the_test_split_is_not_run_twice(tmp_path):
    (tmp_path / "test.json").write_text("{}")
    with pytest.raises(SystemExit, match="run once"):
        main(["--split", "test", "--max-usd", "0", "--results", str(tmp_path)])


def test_the_writer_may_not_be_the_extracting_model(tmp_path):
    with pytest.raises(Exception, match="different model"):
        main(["--split", "dev", "--max-usd", "0", "--writer", "deepseek/deepseek-v4.1-flash",
              "--results", str(tmp_path)])  # fmt: skip


def test_an_end_of_week_answered_as_sunday_is_a_convention_and_other_misses_are_arithmetic():
    from datetime import timedelta

    from duebook.bench.deadlines import classify

    t = next(
        t for t in generate(seed=5, count=40)
        if any("конца недели" in t.events[c.seq - 1].text for c in t.gold.commitments)
    )  # fmt: skip
    run = oracle_run(t)
    assert classify([t], [run]) == {"right": len(t.gold.commitments), "convention": 0,
                                    "arithmetic": 0, "missing": 0, "invented": 0}  # fmt: skip
    week_end = {c.seq for c in t.gold.commitments if "конца недели" in t.events[c.seq - 1].text}

    def moved(p):
        if p.due_at is None:
            return p
        local = p.due_at.astimezone(MSK)
        if p.seq in week_end:  # Friday -> the Sunday of the same week
            return replace(p, due_at=p.due_at + timedelta(days=6 - local.weekday()))
        return replace(p, due_at=p.due_at + timedelta(days=1))

    end = run.snapshots[-1]
    shifted = replace(run, snapshots=(replace(end, promises=tuple(map(moved, end.promises))),))
    k = classify([t], [shifted])
    assert k["convention"] == len(week_end)
    assert k["arithmetic"] == sum(1 for c in t.gold.commitments if c.due_at) - len(week_end)


# ------------------------------------------------------ round 2 plumbing ----

MAIN_TICKETS = generate(seed=21, count=3, profile=MAIN)


def test_a_method_run_on_a_subset_is_summarised_and_compared_on_that_subset_only():
    runs = [oracle_run(t) for t in TICKETS] + [oracle_run(t, "partial") for t in TICKETS[:2]]
    summary = summarise(TICKETS, runs, boot=100)
    partial = summary["methods"]["partial"]
    assert partial["tickets"] == 2 and summary["methods"]["event_memory"]["tickets"] == 6
    assert partial["commitment_f1"]["diff_vs_reference"] == 0.0
    assert partial["commitment_f1"]["diff_ci95"] == [0.0, 0.0]


def test_the_session_cap_counts_every_earlier_run(tmp_path):
    log = tmp_path / "spend.jsonl"
    log.write_text('{"usd": 1.0}\n{"usd": 0.6}\n')
    assert spent_before(log) == pytest.approx(1.6)
    with pytest.raises(SystemExit, match="session cap"):
        main(["--split", "dev", "--max-usd", "1", "--results", str(tmp_path)])


class Priced:
    """A model whose every answer is empty and costs a fixed amount."""

    def __init__(self, usd=0.0001, cached=False):
        self.usd, self.cached, self.model = usd, cached, "deepseek/deepseek-v4.1-flash"

    def complete_json(self, messages, schema, name="output"):
        empty = {k: [] for k in OUTPUT_SCHEMA["properties"]}
        return Completion(empty, Usage(1, 1000, 50, 0, self.usd), cached=self.cached)


def test_the_projection_scales_the_sample_to_the_whole_run_and_trusts_the_cache_only_if_asked():
    ledger = Ledger()
    fresh = EventMemory(Priced(), ledger)
    (p,) = project([fresh], MAIN_TICKETS, {}, ledger, WorkCalendar(), events=20).values()
    sample = sample_of(MAIN_TICKETS[0], 20)
    calls = sum(1 for e in sample.events if getattr(e, "text", ""))
    events = sum(len(t.events) for t in MAIN_TICKETS)
    assert p["sample_usd"] == pytest.approx(calls * 0.0001)
    assert p["upper"] == pytest.approx(p["sample_usd"] * GROWTH["event_memory"] * events / 20)
    assert p["expected"] == pytest.approx(p["upper"])
    replayed = EventMemory(Priced(cached=True), Ledger())
    (q,) = project([replayed], MAIN_TICKETS, {}, replayed.ledger, WorkCalendar(), 20).values()
    assert q["expected"] == 0 and q["upper"] == pytest.approx(p["upper"])


def test_the_guards_are_measured_by_folding_the_same_proposals_without_them():
    t = next(t for t in MAIN_TICKETS if any(c.fate is Fate.BROKEN for c in t.gold.commitments))
    broken = next(c for c in t.gold.commitments if c.fate is Fate.BROKEN)
    kept = next(c for c in t.gold.commitments if c.fate is Fate.KEPT and c.seq > broken.seq)

    class Careless:
        """The oracle, except that it pins one confirmation on the broken promise."""

        oracle = Oracle.of([t])

        def extract(self, card, event):
            proposals = self.oracle.extract(card, event).proposals
            if event.seq == kept.resolved_seq:
                proposals = (ResolveCommitment(f"e{broken.seq}#0", proposals[0].quote, ""),)
            return Extraction(proposals)

    folded = fold(t, Careless(), [cp.seq for cp in checkpoints(t)])
    run = MethodRun.of("event_memory", t, snapshots(t, folded), Tally(),
                       detail={"proposals": [encode(list(p)) for p in folded.proposals],
                               "refused": [[r.seq, r.reason, r.kind, r.target_seq]
                                           for r in folded.refused]})  # fmt: skip
    guarded = score_ticket(t, run.snapshots).end
    loose = score_ticket(t, unguarded(run, t, WorkCalendar()).snapshots).end
    assert guarded.closes_false == 0 and loose.closes_false == 1
    assert loose.alerts.fn == guarded.alerts.fn + 1
    effects = guard_effects([t], [run])
    assert effects["verdicts"]["false_close_prevented"] == 1
    assert effects["by_reason"] == {"target_mismatch": {"false_close_prevented": 1}}


# ---------------------------------------------------------------- round 3 ----


class Judge:
    """A verifier that says yes to every close, quoting the close's own words."""

    model = "stub/judge"

    def complete_json(self, messages, schema, name="output"):
        text = messages[-1]["content"].split("Text:\n", 1)[1]
        answer = {"verdict": "yes", "evidence_quote": text}
        return Completion(answer, Usage(1, 400, 30, 0, 1e-5))


def test_round_three_replays_recorded_proposals_and_audits_every_verdict():
    from duebook.bench.round3 import audit, replay_b, replay_c, verdict_table

    cal = WorkCalendar()
    t = MAIN_TICKETS[0]
    rec = {"proposals": list(t.proposals), "usage": Usage(170, 300_000, 12_000, 270_000, 0.01),
           "seconds": 1.0, "providers": {"DeepInfra": 170}}  # fmt: skip
    c = replay_c(t, rec, cal)
    assert score_ticket(t, c.snapshots).end.commitments.f1 == 1.0 and c.usage == rec["usage"]
    ledger = Ledger(cap_usd=1.0)
    b = replay_b(t, rec, Judge(), ledger, cal)
    verifier = b.detail["verifier"]
    destructive = sum(1 for ps in t.proposals for p in ps if getattr(p, "target_quote", ""))
    assert verifier["usage"]["calls"] == destructive > 0 and ledger.spent.calls == destructive
    # The extraction half costs what it was charged; the verifier is added.
    assert b.usage.calls == 170 + destructive
    rows = audit(t, [decode_proposals(p) for p in b.detail["proposals"]], cal)
    assert len(rows) == destructive and {r["truth"] for r in rows} == {"true"}
    assert all(r["admitted"] for r in rows)
    summary = verdict_table(rows)
    assert summary["by_verdict"] == {"yes": {"true": destructive}} and summary["errors"] == 0
    # The planted closes need no rescue: the words alone admit them.
    assert summary["rescued"] == {}
