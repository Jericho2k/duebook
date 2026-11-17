"""The experiment runner: cached rewriting, the summary with intervals, the record."""

import json
from dataclasses import replace

import pytest
from test_baselines import Scripted

from duebook.bench import Oracle, generate
from duebook.bench.baselines import FullContext
from duebook.bench.experiment import (
    METRICS,
    main,
    record,
    rewritten,
    summarise,
    table,
    to_json,
)
from duebook.bench.generate import SPLITS
from duebook.bench.meter import Ledger, Tally
from duebook.bench.methods import MethodRun
from duebook.bench.result import PromiseStatus, checkpoints
from duebook.bench.run import fold, snapshots
from duebook.bench.score import score_ticket
from duebook.extract import Completion, Usage

TICKETS = generate(seed=5, count=6)


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
        main(["--split", "dev", "--max-usd", "0", "--writer", "z-ai/glm-5.3-flash",
              "--results", str(tmp_path)])  # fmt: skip
