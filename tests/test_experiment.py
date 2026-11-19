"""The experiment runner: cached rewriting, the summary with intervals, the record."""

import json
from dataclasses import replace

import pytest
from test_baselines import Scripted

from duebook.bench import MAIN, Fate, Oracle, generate
from duebook.bench.baselines import FullContext, RollingSummary
from duebook.bench.experiment import (
    GROWTH,
    METRICS,
    audit,
    compact,
    guard_effects,
    load_runs,
    main,
    project,
    record,
    recorded_proposals,
    refolded,
    reread_changes,
    rewritten,
    run_all,
    sample_of,
    saved,
    spent_before,
    summarise,
    table,
    to_json,
    verdict_table,
)
from duebook.bench.generate import SPLITS, CloseCase
from duebook.bench.meter import Ledger, Tally
from duebook.bench.methods import EventMemory, MethodRun
from duebook.bench.result import PromiseStatus, checkpoints
from duebook.bench.run import fold, snapshots
from duebook.bench.score import score_ticket
from duebook.extract import Completion, Usage
from duebook.extract.extractor import Extraction
from duebook.extract.schema import OUTPUT_SCHEMA
from duebook.proposals import AssertFact, ResolveCommitment
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


@pytest.mark.parametrize("kind", CloseCase)
def test_a_wrong_close_case_outcome_lowers_only_its_own_case_metric(kind):
    ticket = SPLITS["dev3"].tickets()[0]
    good = oracle_run(ticket)
    case = next(case for case in ticket.gold.close_cases if case.kind is kind)
    end = good.snapshots[-1]
    # Close the still-owed rival for rescue, choose an ambiguous twin, or
    # drop the callback altogether. Each fails to show what is still owed.
    owed = next(seq for seq in case.promise_seqs if seq != case.target_seq)
    promises = tuple(
        replace(p, status=PromiseStatus.FULFILLED, closed_seq=case.seq) if p.seq == owed else p
        for p in end.promises
        if kind is not CloseCase.WRONG_THING or p.seq != owed
    )
    bad = replace(good, method="full_context", snapshots=(replace(end, promises=promises),))
    rows = summarise([ticket], [good, bad], boot=1)["methods"]
    for metric in CloseCase:
        assert rows["event_memory"][metric.value]["value"] == 1.0
        assert rows["full_context"][metric.value]["value"] == float(metric is not kind)


def test_a_record_holds_what_the_method_said_but_not_its_raw_answers():
    t = TICKETS[0]
    run = FullContext(Scripted(t), Ledger()).run(t)
    rec = record(run, score_ticket(t, run.snapshots))
    assert json.loads(json.dumps(to_json(rec)))["snapshots"][0]["checkpoint"]["seq"] > 0
    assert run.detail["answers"] and "answers" not in rec["detail"]
    assert rec["score_end"]["checkpoints"] == 1
    summary = RollingSummary(Scripted(t), Ledger()).run(t)
    kept = record(summary, score_ticket(t, summary.snapshots))["detail"]
    # What the memory held when it was last asked, without the answer.
    assert kept["end"] == {"seq": t.events[-1].seq, "summary": summary.detail["checkpoints"][-1]
                           ["summary"]} and "checkpoints" not in kept  # fmt: skip


def test_a_saved_run_reads_back_and_scores_as_it_did(tmp_path):
    runs = [FullContext(Scripted(t), Ledger()).run(t) for t in TICKETS[:2]]
    path = tmp_path / "result.json"
    records = [record(r, score_ticket(TICKETS[i], r.snapshots)) for i, r in enumerate(runs)]
    path.write_text(json.dumps({"runs": records}))
    again = load_runs(path)
    assert [r.snapshots for r in again] == [r.snapshots for r in runs]
    assert [r.usage for r in again] == [r.usage for r in runs]


def test_a_record_is_written_one_run_per_line_and_reads_back_unchanged():
    runs = [FullContext(Scripted(t), Ledger()).run(t) for t in TICKETS[:2]]
    rec = to_json({"split": "dev", "runs": [record(r, score_ticket(TICKETS[i], r.snapshots))
                                            for i, r in enumerate(runs)]})  # fmt: skip
    text = compact(rec)
    assert json.loads(text) == rec
    assert len(text.splitlines()) == 7  # {, split, runs: [, a line per run, ], }


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


def test_a_message_the_writer_cannot_be_reached_for_is_tried_again_then_kept_as_written(
    tmp_path,
):
    from duebook.extract.client import ModelError

    class Flaky:
        calls = 0

        def complete_json(self, messages, schema, name="output"):
            Flaky.calls += 1
            if "Здравствуйте" in messages[1]["content"] or Flaky.calls % 2:
                raise ModelError("transport, not retried", Usage(1, 5, 0))
            return Completion({"text": "Короче, " + messages[1]["content"]}, Usage(1, 5, 5))

    split = replace(SPLITS["dev"], count=1)
    import duebook.bench.experiment as experiment

    original = experiment.ChatClient
    experiment.ChatClient = lambda **kw: Flaky()
    try:
        (ticket,), stored = rewritten(split, "some/writer", Ledger(), tmp_path)
    finally:
        experiment.ChatClient = original
    template = split.tickets()[0]
    for before, after in zip(template.events, ticket.events, strict=True):
        if getattr(before, "text", "").startswith("Здравствуйте"):
            assert after.text == before.text
    assert stored["tickets"][ticket.ticket_id]["discarded"] >= 1


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
    unguarded = refolded(run, t, WorkCalendar(), "event_memory_unguarded")
    loose = score_ticket(t, unguarded.snapshots).end
    assert guarded.closes_false == 0 and loose.closes_false == 1
    assert loose.alerts.fn == guarded.alerts.fn + 1
    effects = guard_effects([t], [run])
    assert effects["verdicts"]["false_close_prevented"] == 1
    assert effects["by_reason"] == {"target_mismatch": {"false_close_prevented": 1}}


# ------------------------------------------- the verifier and the records ----


class Judge:
    """A verifier that says yes to every close, quoting the close's own words."""

    model = "stub/judge"

    def __init__(self):
        self.calls = 0

    def complete_json(self, messages, schema, name="output"):
        self.calls += 1
        text = messages[-1]["content"].split("Text:\n", 1)[1]
        answer = {"verdict": "yes", "evidence_quote": text}
        return Completion(answer, Usage(1, 400, 30, 0, 1e-5))


def _recorded(proposals):
    from duebook.bench.methods import Recorded

    return Recorded(tuple(proposals), Usage(170, 300_000, 12_000, 270_000, 0.01), Usage(),
                    1.0, 0.0, {"DeepInfra": 170})  # fmt: skip


def test_a_targeted_rereading_asks_the_model_only_about_the_listed_messages():
    t = MAIN_TICKETS[0]
    promised = [c.seq for c in t.gold.commitments[:2]]
    ledger = Ledger(cap_usd=1.0)
    run = EventMemory(Priced(), ledger, recorded={t.ticket_id: _recorded(t.proposals)},
                      reread={t.ticket_id: frozenset(promised)}).run(t)  # fmt: skip
    assert ledger.spent.calls == 2
    (first, second) = reread_changes([t], [run])
    assert [first["seq"], second["seq"]] == promised
    assert first["new"] == [] and first["lost"] == first["regressions"] == first["old"][:1]


def test_a_recorded_close_follows_its_promise_to_where_the_new_read_put_it():
    t = next(t for t in MAIN_TICKETS if any(c.fate is Fate.KEPT for c in t.gold.commitments))
    kept = next(c for c in t.gold.commitments if c.fate is Fate.KEPT)
    # The record had a fact before the promise; the new read has only the promise.
    noted = AssertFact("note", "заметка", t.events[kept.seq - 1].text[:5])
    proposals = [list(ps) for ps in t.proposals]
    proposals[kept.seq - 1].insert(0, noted)
    closes = [p for p in proposals[kept.resolved_seq - 1] if isinstance(p, ResolveCommitment)]
    proposals[kept.resolved_seq - 1] = [replace(p, commitment_id=f"e{kept.seq}#1") for p in closes]

    (made,) = t.proposals[kept.seq - 1]
    answer = {k: [] for k in OUTPUT_SCHEMA["properties"]} | {"commitments": [
        {"owner": made.owner.value, "action": made.action, "quote": made.quote,
         "due_quote": made.due_quote, "due": None, "supersedes": None, "target_quote": None}]}

    class Again:
        def complete_json(self, messages, schema, name="output"):
            return Completion(answer, Usage(1, 10, 1, 0, 0.0))

    run = EventMemory(Again(), Ledger(), recorded={t.ticket_id: _recorded(proposals)},
                      reread={t.ticket_id: frozenset({kept.seq})}).run(t)  # fmt: skip
    (end,) = [p for p in run.snapshots[-1].promises if p.seq == kept.seq]
    assert end.closed_seq == kept.resolved_seq


def test_recorded_proposals_are_folded_again_and_only_unverified_closes_go_to_the_verifier(
    tmp_path,
):
    cal = WorkCalendar()
    t = MAIN_TICKETS[0]
    judge, ledger = Judge(), Ledger(cap_usd=1.0)
    run = EventMemory(Priced(), ledger, verifier=judge,
                      recorded={t.ticket_id: _recorded(t.proposals)}).run(t)  # fmt: skip
    destructive = sum(1 for ps in t.proposals for p in ps if getattr(p, "target_quote", ""))
    assert judge.calls == destructive > 0 and ledger.spent.calls == destructive
    # The extraction half costs what it was charged; the verifier is added.
    assert run.usage.calls == 170 + destructive
    assert score_ticket(t, run.snapshots).end.commitments.f1 == 1.0
    rows = audit(t, [decode_proposals(p) for p in run.detail["proposals"]], cal)
    assert len(rows) == destructive and {r["truth"] for r in rows} == {"true"}
    summary = verdict_table(rows)
    assert summary["by_verdict"] == {"yes": {"true": destructive}} and summary["errors"] == 0
    # The planted closes need no rescue: the words alone admit them.
    assert summary["rescued"] == {}
    # Read back from its record, the run asks nothing: every close has its verdict.
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"runs": [record(run, score_ticket(t, run.snapshots))]}))
    again = EventMemory(Priced(), Ledger(cap_usd=0.0), verifier=Judge(),
                        recorded=recorded_proposals(path)).run(t)  # fmt: skip
    assert again.snapshots == run.snapshots and again.usage == run.usage


def test_without_the_verifier_the_same_proposals_cost_only_their_extraction():
    t = MAIN_TICKETS[0]
    run = EventMemory(Priced(), Ledger(), verifier=Judge(),
                      recorded={t.ticket_id: _recorded(t.proposals)}).run(t)  # fmt: skip
    alone = refolded(run, t, WorkCalendar(), "event_memory_no_verifier")
    assert alone.usage == _recorded(t.proposals).extraction
    assert alone.ingest_seconds == pytest.approx(1.0)
    assert all(p.verification is not None
               for ps in run.detail["proposals"] for p in decode_proposals(ps)
               if getattr(p, "target_quote", ""))  # fmt: skip


def test_an_interrupted_run_resumes_from_its_journal_and_reruns_only_what_failed(tmp_path):
    class Counting:
        name, calls = "full_context", []

        def run(self, ticket):
            self.calls.append(ticket.ticket_id)
            return FullContext(Scripted(ticket), Ledger()).run(ticket)

    journal = tmp_path / "journal.jsonl"
    finished = FullContext(Scripted(TICKETS[0]), Ledger()).run(TICKETS[0])
    failed = replace(FullContext(Scripted(TICKETS[1]), Ledger()).run(TICKETS[1]), failure="reset")
    journal.write_text("".join(json.dumps(saved(r)) + "\n" for r in (finished, failed)))
    method = Counting()
    runs = run_all([method], TICKETS[:3], 2, journal=journal)
    assert sorted(method.calls) == [TICKETS[1].ticket_id, TICKETS[2].ticket_id]
    assert runs[0].snapshots == finished.snapshots
    assert len(load_runs(journal)) == 4
