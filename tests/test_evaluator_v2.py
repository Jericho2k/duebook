"""Frozen, hand-authored evaluator cases and the unchanged historical scorer."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from duebook.bench.evaluator_v2.__main__ import main, read
from duebook.bench.evaluator_v2.attribution import attribute
from duebook.bench.evaluator_v2.labels import Labels, time_of
from duebook.bench.evaluator_v2.replay import Frame, Trace, predictions
from duebook.bench.evaluator_v2.score import Durations, Finding, evaluate, match_identities
from duebook.bench.experiment import run_of, to_json
from duebook.bench.generate import SPLITS, Gold, SimTicket
from duebook.bench.rewrite import with_rewritten_text
from duebook.bench.score import Counts, score_ticket
from duebook.card import Card, CommitmentState, Party
from duebook.events import Side
from duebook.reducer import Guards
from duebook.store.codec import decode_event
from duebook.temporal import WorkCalendar

FIXTURE = Path(__file__).parent / "fixtures/evaluator-v2"
CALENDAR = WorkCalendar(ZoneInfo("UTC"))
ARGUMENTS = [
    "--events", str(FIXTURE / "events.json"), "--record", str(FIXTURE / "record.json"),
    "--calendar", "UTC", "--gold", str(FIXTURE / "gold.json"),
    "--reviews", str(FIXTURE / "reviews.json"),
]  # fmt: skip


def example(index=0):
    source = read(FIXTURE / "events.json")[0]["tickets"][index]
    gold = read(FIXTURE / "gold.json")[0]["tickets"][index]
    reviews = read(FIXTURE / "reviews.json")[0]["tickets"][index]
    record = read(FIXTURE / "record.json")[0]["runs"][index]
    ticket = SimTicket(source["id"], tuple(map(decode_event, source["events"])), (), Gold(),
                       time_of(source["end"]))  # fmt: skip
    trace = Trace.read(ticket, run_of(record), Guards(), CALENDAR)
    raw = reviews | gold
    return trace, Labels.read(ticket, raw), raw


def test_v1_and_v2_report_diverge_on_the_same_anchored_wrong_action(tmp_path):
    out = tmp_path / "out.json"
    main([*ARGUMENTS, "--out", str(out)])
    report, _ = read(out)
    identity, clock = report["tickets"]
    archive = read(FIXTURE / "record.json")[0]["runs"]
    for i, row in enumerate((identity, clock)):
        assert row["score_v1"]["end"] == archive[i]["score_end"]
        assert row["score_v1"]["handoffs"] == archive[i]["score_handoffs"]
    assert identity["score_v1"]["end"]["commitments"] == [4, 0, 0]
    assert identity["score_v2"]["semantic_end"] == {
        "tp": 3,
        "fp": 1,
        "fn": 1,
        "precision": 0.75,
        "recall": 0.75,
        "f1": 0.75,
    }
    assert identity["score_v2"]["semantic_messages"]["tp"] == 22
    assert clock["score_v2"]["semantic_end"]["tp"] == 7  # includes the conditional offer
    again = tmp_path / "again.json"
    main([*ARGUMENTS, "--out", str(again)])
    assert again.read_bytes() == out.read_bytes()


def test_semantic_edges_account_for_distinctness_merges_splits_and_ownership():
    trace, labels, _ = example()
    exact = predictions(trace.cards[1])["IDENTITY-1#0"]
    card = {
        p.id: p for p in (exact, replace(exact, id="restatement", seq=2),
                          replace(exact, id="vague-merge"), replace(exact, id="split"),
                          replace(exact, id="wrong-party", owner=Party.AGENT))
    }  # fmt: skip
    labels = replace(labels, judgments=labels.judgments | {
        "restatement": {"judgment": "equivalent", "gold": ["refund"]},
        "vague-merge": {"judgment": "merged", "gold": ["refund", "invoice-100"]},
        "split": {"judgment": "split", "gold": ["invoice-100"]},
        "wrong-party": {"judgment": "equivalent", "gold": ["refund"]},
    })  # fmt: skip
    result = match_identities(labels, card, 2, {})
    assert result.counts == Counts(1, 4, 1)
    assert result.matched["refund"].id == exact.id
    assert result.extra == {"restatement": "duplicate_commitment", "vague-merge": "merged",
                            "split": "split", "wrong-party": "wrong_owner"}  # fmt: skip
    assert set(result.missing) == {"invoice-100"}


def test_ambiguity_excludes_only_its_reviewed_candidates_and_unsupported_review_fails():
    trace, labels, raw = example()
    card = predictions(trace.cards[1])
    vague = "IDENTITY-1#1"
    amended = dict(labels.judgments[vague], judgment="uncertain")
    labels = replace(labels, judgments={**labels.judgments, vague: amended})
    paired = match_identities(labels, card, 1, {})
    assert paired.counts == Counts(1, 0, 0)
    assert (paired.excluded_gold, paired.excluded_predictions) == (1, 1)
    with pytest.raises(ValueError, match="missing human identity review"):
        match_identities(replace(labels, judgments={}), card, 1, {})
    raw["identity_reviews"][0]["gold"] = ["refund", "invoice-100"]
    with pytest.raises(ValueError, match="one specific gold identity"):
        Labels.read(trace.ticket, raw)


def test_replay_reconstructs_temporary_false_close_and_censors_unknown_outcome():
    trace, labels, _ = example()
    assert labels.obligations["receipt"].at(10).state == "claimed"
    assert labels.obligations["receipt"].at(12).state == "unknown"
    result = evaluate(trace, labels)["lifecycle"]
    errors = {row["identity"]: row for row in result["episodes"] if row["kind"] == "false_close"}
    assert (errors["refund"]["from"]["seq"], errors["refund"]["until"]["seq"]) == (4, 6)
    assert (errors["refund"]["message_checkpoints"], errors["refund"]["seconds"]) == (2, 1200)
    assert errors["refund"]["disposition"] == "temporary"
    assert errors["receipt"]["disposition"] == "censored"
    assert result["denominators"]["end"]["state_excluded"] == 1
    transitions = {(r["gold"], r["seq"]): r["result"] for r in result["transitions"]["rows"]}
    assert transitions["refund", 6] == "wrong_transition_evidence"
    assert transitions["invoice-200", 8] == "correct"  # actual counterparty cancellation
    assert transitions["invoice-100", 8] == "missing_identity"


def test_false_close_spanning_40_messages_is_retained_after_clearance():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    frames = [Frame(i, start + timedelta(minutes=i), "message", Card("duration"))
              for i in range(1, 42)]  # fmt: skip
    tracker = Durations()
    for left, right in zip(frames, frames[1:], strict=False):
        tracker.observe(left, right, [Finding("false_close", "A", (1,))], set(), set())
    tracker.observe(frames[-1], None, [], set(), set())
    assert tracker.report()["by_kind"]["false_close"] == {
        "occurrences": 1,
        "temporary": 1,
        "message_checkpoints": 40,
        "seconds": 2400,
    }


def test_clock_scores_alerts_between_messages_and_genuine_renegotiation():
    trace, labels, _ = example(1)
    result = evaluate(trace, labels)
    by_result = {r["result"]: r for r in result["alerts"]["occurrences"]}
    assert by_result["premature"]["offset_seconds"] == -1200
    assert by_result["delayed"]["offset_seconds"] == 1200
    assert by_result["missed"]["missed_seconds"] == 3000
    assert by_result["on_time"]["at"] == datetime(2026, 1, 1, 11, tzinfo=UTC)
    assert by_result["on_time"]["seq"] == 7  # the next message arrives at 11:05
    assert by_result["false"]["gold"] == "stock"
    assert result["alerts"]["exposure"]["by_kind"]["expected_alert_wait"]["seconds"] == 4200
    changes = result["lifecycle"]["transitions"]["rows"]
    assert any(r["gold"] == "postage" and r["expected"] == "superseded" and r["result"] == "correct"
               for r in changes)  # fmt: skip
    assert "invented_deadline" in result["lifecycle"]["by_kind"]


def test_attribution_records_stage_evidence_and_leaves_unadjudicated_causes_open():
    trace, labels, _ = example()
    audited = tuple(trace.decisions())
    wrong = next(d for d in audited if d.seq == 11)
    audited = tuple(replace(d, target="IDENTITY-1#0") if d is wrong else d for d in audited)
    labels = replace(labels, source_sides={12: Side.COMPANY})
    result = attribute(trace, labels, audited, evaluate(trace, labels)["lifecycle"])
    rows = {r["seq"]: r for r in result["proposal_decisions"]}
    assert "stored_verifier_veto_applied" in rows[3]["findings"]
    assert "review_suggestion_retained" in rows[3]["findings"]
    assert "unsafe_admission" in rows[4]["findings"]
    assert "deadline_rejected_by_reducer" in rows[7]["findings"]
    assert "decision_unjudged" in rows[7]["findings"]
    assert "wrong_proposal_target" in rows[11]["findings"]
    assert "verification_execution_failure" in rows[11]["findings"]
    assert result["role_mapping_mismatches"] == [{"seq": 12, "observed": Side.CUSTOMER,
                                                  "adjudicated": Side.COMPANY}]  # fmt: skip
    assert {g["finding"] for g in result["transition_gaps"]} == {
        "targeted_transition_rejected",
        "no_transition_proposal_at_evidence",
    }


def test_round10c_keeps_original_v1_score_and_record_during_opt_in_replay(tmp_path):
    path = Path("results/round10c/test3-live.json")
    record, before_sha = read(path)
    raw = next(r for r in record["runs"] if r["method"] == "event_memory")
    frozen = Path("results/round4/tickets/test3.json")
    rewrites, _ = read(frozen)
    ticket = next(t for t in SPLITS["test3"].tickets() if t.ticket_id == raw["ticket"])
    ticket = with_rewritten_text(ticket, rewrites["tickets"][ticket.ticket_id]["texts"])
    run = run_of(raw)
    before = score_ticket(ticket, run.snapshots)
    out = tmp_path / "review.json"
    main(
        [
            "--split",
            "test3",
            "--rewrites",
            str(frozen),
            "--record",
            str(path),
            "--ticket",
            ticket.ticket_id,
            "--rules",
            "6",
            "--review-packet",
            "--out",
            str(out),
        ]
    )
    assert read(out)[0]["tickets"][0]["presented_identities"]
    assert read(path)[1] == before_sha
    assert score_ticket(ticket, run.snapshots) == before
    assert to_json(before.end.as_dict()) == raw["score_end"]
    assert to_json(before.handoffs.as_dict()) == raw["score_handoffs"]


def test_creation_delay_drop_and_reopening_use_semantic_identity_across_gaps():
    trace, labels, _ = example()
    cards = dict(trace.cards)
    original = "IDENTITY-1#0"
    for seq in (1, 7):
        card = cards[seq]
        cards[seq] = replace(
            card, commitments=tuple(c for c in card.commitments if c.id != original)
        )
    cards[8] = replace(cards[8], commitments=tuple(
        replace(c, state=CommitmentState.OPEN, resolution=None) if c.id == original else c
        for c in cards[8].commitments
    ))  # fmt: skip
    lifecycle = evaluate(replace(trace, cards=cards), labels)["lifecycle"]
    created = next(row for row in lifecycle["creation"]["rows"] if row["gold"] == "refund")
    assert (created["result"], created["messages"], created["seconds"]) == ("delayed", 1, 600)
    lost = next(row for row in lifecycle["episodes"] if row["kind"] == "lost_commitment")
    assert (lost["from"]["seq"], lost["until"]["seq"]) == (7, 8)
    kinds = {row["finding"].kind for row in lifecycle["changes"] if row["seq"] == 8}
    assert kinds == {"commitment_returned", "incorrect_reopening"}


def test_failed_run_replays_held_prefix_and_reports_missing_evidence():
    trace, labels, _ = example()
    archived = read(FIXTURE / "record.json")[0]["runs"][0]
    raw = archived | {
        "failure": "Fixture unavailable",
        "snapshots": [],
        "detail": {"proposals": archived["detail"]["proposals"][:1]},
    }
    held = Trace.read(trace.ticket, run_of(raw), Guards(), CALENDAR)
    assert held.cards[12].last_seq == 1
    result = evaluate(held, labels)
    assert result["coverage"]["message_checkpoints_held_after_failure"] == 11
    reviewed = replace(
        labels,
        proposal_reviews={},
        judgments={k: v for k, v in labels.judgments.items() if k.startswith("IDENTITY-1#")},
    )
    audit = attribute(held, reviewed, tuple(held.decisions()), result["lifecycle"])
    assert audit["extraction"]["held_at"] == 2
    assert audit["counts"]["extraction_failed_or_unread"] == 2
    assert audit["counts"]["transition_extraction_failed_or_unread"] == 5
