"""The scorer judges any method's snapshot, and moves the right way when it errs."""

from dataclasses import replace
from datetime import timedelta

from duebook import Side, notices
from duebook.bench import MAIN, Fate, Oracle, generate
from duebook.bench.match import mentions, tokens
from duebook.bench.result import (
    Dispute,
    FactItem,
    FailedStep,
    Moment,
    Promise,
    PromiseStatus,
    Snapshot,
    checkpoints,
    from_card,
)
from duebook.bench.run import fold, snapshots
from duebook.bench.score import Score, alerted_at, fact_status, score_snapshot, score_ticket
from duebook.lifecycle import NoticeKind

TICKETS = generate(seed=11, count=40)


def oracle_snapshots(ticket):
    folded = fold(ticket, Oracle.of([ticket]), [cp.seq for cp in checkpoints(ticket)])
    return snapshots(ticket, folded)


def end_of(ticket):
    return oracle_snapshots(ticket)[-1]


def with_dispute_settled():
    return next(t for t in TICKETS if any(d.settled_seq is not None for d in t.gold.contradictions))


# --------------------------------------------------------------- values ----


def test_a_value_is_recognised_by_exact_numbers_and_word_stems():
    assert tokens("Списали 3 200 рублей, ещё") == ["списали", "3200", "рублей", "еще"]
    assert mentions("Списали 3 200 рублей", ("3200",))
    assert not mentions("списали 13200 рублей", ("3200",))
    assert mentions("живёт на Лесной улице, дом 12", ("лесн", "12"))
    assert not mentions("живёт на Лесной улице, дом 120", ("лесн", "12"))
    assert mentions("Тариф клиента: ПРЕМИУМ", ("премиум",))


# ----------------------------------------------------- the oracle is 1.0 ----


def test_the_oracle_is_perfect_at_every_handoff_as_well_as_at_the_end():
    total = Score()
    for t in TICKETS:
        scored = score_ticket(t, oracle_snapshots(t))
        assert scored.handoffs.checkpoints == len(t.gold.handoffs) >= 2
        total += scored.all
    assert total.commitments.f1 == total.deadline_accuracy == total.status_accuracy == 1.0
    assert total.alerts.f1 == total.change_accuracy == 1.0
    assert total.disputes.f1 == total.failed_steps.f1 == 1.0
    assert total.freshness_error == 0.0 and total.facts_judged > 200
    assert total.disputes.tp > 0 and total.failed_steps.tp > 0 and total.alerts.tp > 0


def test_the_scorers_alerts_are_exactly_the_cards_breach_notices():
    for t in TICKETS:
        folded = fold(t, Oracle.of([t]), [cp.seq for cp in checkpoints(t)])
        for cp, snap in zip(checkpoints(t), snapshots(t, folded), strict=True):
            card = folded.cards[cp.seq]
            breached = {
                card.commitment(n.commitment_id).source.seq
                for n in notices(card, cp.at)
                if n.kind is NoticeKind.BREACHED
            }
            assert {p.seq for p in snap.promises if alerted_at(p, cp)} == breached


def test_checkpoints_are_every_handoff_then_the_end():
    t = TICKETS[0]
    cps = checkpoints(t)
    assert [cp.seq for cp in cps[:-1]] == list(t.gold.handoffs)
    assert {cp.kind for cp in cps[:-1]} == {Moment.HANDOFF} and cps[-1].kind is Moment.END
    assert cps[-1].at == t.end


# ------------------------------------------------- promises and alerts ----


def test_a_deadline_within_a_minute_is_right_and_an_hour_off_is_wrong():
    t = TICKETS[0]
    snap = end_of(t)

    def shifted(delta):
        moved = tuple(replace(p, due_at=p.due_at + delta) if p.due_at else p for p in snap.promises)
        return score_snapshot(t, replace(snap, promises=moved)).deadline_accuracy

    assert shifted(timedelta(seconds=59)) == 1.0
    assert shifted(timedelta(hours=1)) < 1.0


def test_a_promise_held_open_after_it_was_kept_is_a_wrong_status_and_may_be_a_false_alert():
    t = next(
        t for t in TICKETS if any(c.fate is Fate.KEPT and c.due_at for c in t.gold.commitments)
    )
    snap = end_of(t)
    reopened = tuple(replace(p, status=PromiseStatus.OPEN) for p in snap.promises)
    s = score_snapshot(t, replace(snap, promises=reopened))
    assert s.status_accuracy < 1.0
    kept_and_due = [
        c for c in t.gold.commitments
        if c.fate is Fate.KEPT and c.due_at and c.due_at <= t.end and c.owner.side is Side.COMPANY
    ]  # fmt: skip
    assert s.alerts.fp == len(kept_and_due)


def test_a_claim_counts_as_done_for_status():
    t = next(t for t in TICKETS if any(c.fate is Fate.KEPT for c in t.gold.commitments))
    snap = end_of(t)
    claimed = tuple(
        replace(p, status=PromiseStatus.CLAIMED) if p.status is PromiseStatus.FULFILLED else p
        for p in snap.promises
    )
    assert score_snapshot(t, replace(snap, promises=claimed)).status_accuracy == 1.0


def test_a_deadline_the_method_does_not_trust_raises_no_alert():
    t = next(t for t in TICKETS if any(c.fate is Fate.BROKEN for c in t.gold.commitments))
    snap = end_of(t)
    doubted = tuple(replace(p, alertable=False) for p in snap.promises)
    s = score_snapshot(t, replace(snap, promises=doubted))
    assert s.alerts.tp == 0 and s.alerts.fp == 0 and s.alerts.fn > 0


# --------------------------------------------------------------- closes ----

MAIN_TICKETS = generate(seed=17, count=4, profile=MAIN)


def broken_and_kept(t):
    """The end snapshot, a promise planted as broken in it, and one planted as kept."""
    snap = end_of(t)
    by_seq = {p.seq: p for p in snap.promises}
    broken = next(c for c in t.gold.commitments if c.fate is Fate.BROKEN)
    kept = next(c for c in t.gold.commitments if c.fate is Fate.KEPT)
    return snap, by_seq[broken.seq], by_seq[kept.seq], kept


def test_closing_a_promise_that_is_still_owed_is_a_false_close_and_silences_its_alert():
    t = MAIN_TICKETS[0]
    snap, broken, _, _ = broken_and_kept(t)
    closed = replace(broken, status=PromiseStatus.FULFILLED, closed_seq=broken.seq + 1)
    s = score_snapshot(t, replace(snap, promises=tuple(
        closed if p is broken else p for p in snap.promises)))  # fmt: skip
    assert s.closes_false == 1 and s.alerts.fn == 1 and s.close_precision < 1.0


def test_a_close_on_the_wrong_message_is_a_wrong_target_close_and_none_is_missed_close():
    t = MAIN_TICKETS[0]
    snap, _, kept, gold = broken_and_kept(t)
    wrong = replace(kept, closed_seq=gold.resolved_seq - 1)
    reopened = replace(kept, status=PromiseStatus.OPEN, closed_seq=None)
    for found, field in ((wrong, "closes_wrong_target"), (reopened, "closes_missed")):
        promises = tuple(found if p is kept else p for p in snap.promises)
        s = score_snapshot(t, replace(snap, promises=promises))
        assert getattr(s, field) == 1 and s.closes_false == 0


def test_a_close_with_no_message_given_is_judged_by_outcome_alone():
    t = MAIN_TICKETS[0]
    snap = end_of(t)
    vague = tuple(replace(p, closed_seq=None) for p in snap.promises)
    s = score_snapshot(t, replace(snap, promises=vague))
    assert s.closes_wrong_target == 0
    assert s.closes_right == score_snapshot(t, snap).closes_right > 0


def test_a_suggestion_is_right_when_it_names_the_message_that_closed_the_promise():
    t = MAIN_TICKETS[0]
    snap, broken, kept, gold = broken_and_kept(t)
    held = replace(kept, status=PromiseStatus.OPEN, closed_seq=None, suggested=(gold.resolved_seq,))
    stray = replace(broken, suggested=(gold.resolved_seq,))
    promises = tuple(held if p is kept else stray if p is broken else p for p in snap.promises)
    s = score_snapshot(t, replace(snap, promises=promises))
    assert (s.suggestions_right, s.suggestions_wrong, s.closes_missed) == (1, 1, 1)


def test_a_dropped_promise_must_be_reported_dropped_not_done_or_owed():
    t = next(t for t in MAIN_TICKETS if any(c.fate is Fate.CANCELLED for c in t.gold.commitments))
    snap = end_of(t)
    gold = next(c for c in t.gold.commitments if c.fate is Fate.CANCELLED)
    for status in (PromiseStatus.FULFILLED, PromiseStatus.OPEN):
        promises = tuple(replace(p, status=status) if p.seq == gold.seq else p
                         for p in snap.promises)  # fmt: skip
        assert score_snapshot(t, replace(snap, promises=promises)).status_accuracy < 1.0


# ---------------------------------------------------------------- facts ----


def test_facts_are_matched_by_value_whatever_the_method_calls_them():
    t = TICKETS[0]
    snap = end_of(t)
    renamed = tuple(FactItem(f"something {n}", f"по словам клиента: {f.value}") for n, f in
                    enumerate(snap.facts))  # fmt: skip
    a = score_snapshot(t, snap)
    b = score_snapshot(t, replace(snap, facts=renamed))
    assert (a.facts_judged, a.change_accuracy) == (b.facts_judged, b.change_accuracy)


def test_presenting_the_value_that_was_replaced_is_stale_and_the_change_is_missed():
    t = next(t for t in TICKETS if t.gold.changes)
    change = t.gold.changes[0]
    snap = end_of(t)
    stale = replace(snap, facts=(*snap.facts, FactItem("тариф", change.old)))
    s = score_snapshot(t, stale)
    assert s.facts_stale == 1 and s.freshness_error > 0
    assert s.changes_right == s.changes_judged - 1


def test_a_change_described_as_was_and_now_presents_only_the_new_value():
    t = next(t for t in TICKETS if t.gold.changes)
    change = t.gold.changes[0]
    snap = end_of(t)
    kept = [f for f in snap.facts if change.new not in f.value]
    both = FactItem(change.slot, f"было {change.old}, стало {change.new}")
    s = score_snapshot(t, replace(snap, facts=(*kept, both)))
    assert s.facts_stale == 0 and s.change_accuracy == 1.0


def test_presenting_one_side_of_an_open_dispute_as_fact_counts_as_contested():
    t = next(t for t in TICKETS if any(d.settled_seq is None for d in t.gold.contradictions))
    dispute = next(d for d in t.gold.contradictions if d.settled_seq is None)
    snap = end_of(t)
    s = score_snapshot(t, replace(snap, facts=(*snap.facts, FactItem("x", dispute.values[1]))))
    assert s.facts_contested == 1


def test_a_value_that_lost_a_dispute_is_stale_after_the_settlement_and_contested_before():
    t = with_dispute_settled()
    d = next(d for d in t.gold.contradictions if d.settled_seq is not None)
    loser = next(f for f in t.gold.facts if f.slot == d.slot and f.value != d.winner)
    assert fact_status(t, loser, d.settled_seq - 1) == "contested"
    assert fact_status(t, loser, d.settled_seq) == "stale"
    assert fact_status(t, loser, loser.seq - 1) is None


def test_a_fact_that_carries_no_planted_value_is_counted_but_not_judged():
    t = TICKETS[0]
    snap = end_of(t)
    s = score_snapshot(t, replace(snap, facts=(*snap.facts, FactItem("mood", "раздражена"))))
    assert s.facts_unjudged == 1 and s.freshness_error == 0.0


# ----------------------------------------------------- disputes, steps ----


def test_a_dispute_still_reported_after_it_was_settled_is_a_false_one():
    t = with_dispute_settled()
    d = next(d for d in t.gold.contradictions if d.settled_seq is not None)
    snap = end_of(t)
    stale = replace(snap, disputes=(*snap.disputes, Dispute(d.slot, d.values)))
    s = score_snapshot(t, stale)
    assert s.disputes.fp == 1 and s.disputes.fn == 0


def test_a_dispute_is_recognised_only_with_both_sides_values():
    t = next(t for t in TICKETS if any(d.settled_seq is None for d in t.gold.contradictions))
    snap = end_of(t)
    d = next(d for d in t.gold.contradictions if d.settled_seq is None)
    one_sided = tuple(x for x in snap.disputes if x.topic != d.slot)
    s = score_snapshot(t, replace(snap, disputes=(*one_sided, Dispute("сумма", (d.values[0],)))))
    assert s.disputes.fn == 1 and s.disputes.fp == 1


def test_a_failed_step_is_recognised_by_its_message_or_by_its_words():
    t = TICKETS[0]
    fix = t.gold.repeated_fixes[0]
    snap = end_of(t)
    others = tuple(s for s in snap.failed_steps if s.seq != fix.suggested_seq)
    by_seq = FailedStep("что-то не помогло", failed_seq=fix.failed_seq)
    by_words = FailedStep(f"клиент уже пробовал: {fix.action}")
    for step in (by_seq, by_words):
        s = score_snapshot(t, replace(snap, failed_steps=(*others, step)))
        assert s.failed_steps == score_snapshot(t, snap).failed_steps
    s = score_snapshot(t, replace(snap, failed_steps=others))
    assert s.failed_steps.fn == 1


def test_an_unanswered_checkpoint_scores_as_nothing_found_and_is_counted():
    t = TICKETS[0]
    cp = checkpoints(t)[-1]
    s = score_snapshot(t, Snapshot(cp, error="ModelError: http 503"))
    assert s.errors == 1 and s.commitments.tp == 0 and s.commitments.fn > 0


def test_a_score_round_trips_through_json():
    s = score_ticket(TICKETS[0], oracle_snapshots(TICKETS[0])).all
    assert Score.from_dict(s.as_dict()) == s


def test_the_card_adapter_shows_a_disputed_slot_only_as_a_dispute():
    t = next(t for t in TICKETS if any(d.settled_seq is None for d in t.gold.contradictions))
    folded = fold(t, Oracle.of([t]), [t.events[-1].seq])
    snap = from_card(folded.card, checkpoints(t)[-1])
    disputed = {d.topic for d in snap.disputes}
    assert disputed and not any(f.name in disputed for f in snap.facts)
    assert all(len(d.values) == 2 and len(d.seqs) == 2 for d in snap.disputes)


def test_promises_of_any_status_are_reported_with_their_source_message():
    t = TICKETS[0]
    snap = end_of(t)
    assert {p.seq for p in snap.promises} == {c.seq for c in t.gold.commitments}
    assert all(isinstance(p, Promise) for p in snap.promises)
