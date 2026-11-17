import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from duebook import AssigneeChanged, Card, CommitmentState, Party, Reason, StepOutcome, apply
from duebook.bench import (
    LONG,
    MAIN,
    SPLITS,
    STANDARD,
    Counts,
    Fate,
    Oracle,
    Rewriter,
    SameModel,
    evaluate,
    generate,
    require_different_models,
    run_ticket,
    score,
)
from duebook.bench.generate import TWINS
from duebook.bench.match import mentions
from duebook.bench.rewrite import acceptable
from duebook.events import Role, Side
from duebook.extract import Completion, ModelError, Usage
from duebook.extract.extractor import Extraction
from duebook.proposals import MakeCommitment
from duebook.temporal import Offset, Unit

TICKETS = generate(seed=7, count=120)


class Altered:
    """The perfect extractor with one deliberate fault, to see the score move."""

    def __init__(self, tickets, change):
        self.oracle, self.change = Oracle.of(tickets), change

    def extract(self, card, event):
        proposals = self.oracle.extract(card, event).proposals
        return Extraction(tuple(self.change(event, proposals)), usage=Usage(1, 900, 80, 0, 0.001))


def evaluated(change):
    return evaluate(TICKETS, Altered(TICKETS, change)).score


# ------------------------------------------------------------ generator ----


def test_the_same_seed_gives_the_same_tickets_and_another_seed_different_ones():
    assert generate(7, 20) == TICKETS[:20]
    assert generate(8, 20) != TICKETS[:20]


def test_every_kind_of_planted_event_occurs():
    fates = {c.fate for t in TICKETS for c in t.gold.commitments}
    assert fates == {Fate.KEPT, Fate.BROKEN, Fate.OPEN}
    assert any(t.gold.changes for t in TICKETS)
    assert any(t.gold.contradictions for t in TICKETS)
    assert any(t.gold.repeated_fixes for t in TICKETS)


def test_events_are_in_order_and_time_never_runs_backwards():
    for t in TICKETS:
        assert [e.seq for e in t.events] == list(range(1, len(t.events) + 1))
        assert all(a.at <= b.at for a, b in zip(t.events, t.events[1:], strict=False))
        assert t.end > t.events[-1].at and len(t.events) == len(t.proposals)


def test_every_ticket_has_at_least_50_messages_and_two_handoffs_over_several_days():
    for t in TICKETS:
        assert t.messages >= 50
        handoffs = [e for e in t.events if isinstance(e, AssigneeChanged) and e.previous]
        assert len(handoffs) >= 2 and t.gold.handoffs == tuple(e.seq for e in handoffs)
        assert all(e.previous != e.current for e in handoffs)
        assert t.end - t.events[0].at > timedelta(days=2)


def test_the_length_is_configurable_and_the_long_profile_has_200_messages():
    longer = replace(STANDARD, messages=(80, 80))
    assert {t.messages for t in generate(7, 5, profile=longer)} == {80}
    long = generate(7, 2, profile=LONG)
    assert all(t.messages >= 200 and len(t.gold.handoffs) >= 3 for t in long)
    s = evaluate(long, Oracle.of(long)).score
    assert (s.commitments.f1, s.deadline_accuracy, s.alerts.f1) == (1.0, 1.0, 1.0)


def test_most_messages_are_filler_that_plants_nothing():
    for t in TICKETS:
        planted = sum(1 for p in t.proposals if p)
        assert planted < t.messages / 2


def test_a_promise_made_early_is_settled_days_later():
    for t in TICKETS:
        span = t.end - t.events[0].at
        early = [
            c for c in t.gold.commitments
            if c.due_at and t.events[c.seq - 1].at - t.events[0].at < span * 0.35
            and c.due_at - t.events[c.seq - 1].at > timedelta(days=3)
        ]  # fmt: skip
        assert early, t.ticket_id


def test_a_repeated_fix_straddles_a_handoff_and_is_repeated_by_someone_else():
    for t in TICKETS:
        assert t.gold.repeated_fixes
        for fix in t.gold.repeated_fixes:
            assert fix.suggested_seq < fix.failed_seq < fix.repeated_seq
            assert any(fix.failed_seq < h < fix.repeated_seq for h in t.gold.handoffs)
            first, again = t.events[fix.suggested_seq - 1], t.events[fix.repeated_seq - 1]
            assert first.author != again.author
            assert any(k in t.events[fix.failed_seq - 1].text.lower() for k in fix.key)


def test_each_planted_value_is_recognisable_by_its_key_and_by_nothing_else_in_the_ticket():
    for t in TICKETS:
        assert t.gold.changes and t.gold.contradictions
        for fact in t.gold.facts:
            matching = [f for f in t.gold.facts if mentions(f.value, fact.key)]
            assert matching == [fact] or {f.value for f in matching} == {fact.value}


def test_dev_and_test_splits_are_fixed_disjoint_and_the_test_split_has_20_tickets():
    dev, test = SPLITS["dev"], SPLITS["test"]
    assert dev.seed != test.seed and test.count >= 20
    ids = {t.ticket_id for t in dev.tickets()} & {t.ticket_id for t in test.tickets()}
    assert not ids
    assert SPLITS["long"].profile is LONG


def test_the_benchmark_runs_without_a_database_driver():
    check = "import sys, duebook.bench.experiment; sys.exit('psycopg' in sys.modules)"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    assert subprocess.run([sys.executable, "-c", check], env=env).returncode == 0


# -------------------------------------------- labels are true by design ----


def test_the_planted_proposals_through_the_real_reducer_score_exactly_one():
    s = evaluate(TICKETS, Oracle.of(TICKETS)).score
    assert (s.commitments.fp, s.commitments.fn, s.alerts.fp, s.alerts.fn) == (0, 0, 0, 0)
    assert s.commitments.tp == sum(len(t.gold.commitments) for t in TICKETS) > 200
    assert s.deadline_accuracy == 1.0 and s.commitments.f1 == 1.0
    assert s.alerts.tp == sum(c.fate is Fate.BROKEN for t in TICKETS for c in t.gold.commitments)
    every = s + evaluate(TICKETS, Oracle.of(TICKETS)).handoffs
    assert every.fact_recall == 1.0 and every.facts_current > 100


def test_each_label_describes_what_the_card_really_ends_up_holding():
    for t in TICKETS:
        card, _, failure = run_ticket(t, Oracle.of([t]))
        assert failure is None
        by_seq = {c.source.seq: c for c in card.commitments}
        for gold in t.gold.commitments:
            found = by_seq[gold.seq]
            kept = found.state is CommitmentState.FULFILLED
            assert kept == (gold.fate is Fate.KEPT) and found.owner is gold.owner
        for change in t.gold.changes:
            assert (change.old, change.new) in {(a.value, b.value) for a, b in card.changed_facts()}
        assert {c.slot for c in card.open_contradictions()} == {
            c.slot for c in t.gold.contradictions if c.settled_seq is None
        }
        for dispute in t.gold.contradictions:
            if dispute.settled_seq is not None:
                (stood,) = [f.value for f in card.current_facts() if f.slot == dispute.slot]
                assert stood == dispute.winner
        for fix in t.gold.repeated_fixes:
            (step,) = [s for s in card.steps if s.action == fix.action]
            assert step.outcome is StepOutcome.FAILED and step.outcome_source.seq == fix.failed_seq


def test_suggesting_a_failed_fix_again_is_refused_by_the_reducer_as_already_known():
    ticket = next(t for t in TICKETS if t.gold.repeated_fixes)
    repeats = {fix.repeated_seq for fix in ticket.gold.repeated_fixes}
    card = Card(ticket.ticket_id)
    for event, proposals in zip(ticket.events, ticket.proposals, strict=True):
        outcome = apply(card, event, proposals)
        card = outcome.card
        if event.seq in repeats:
            assert Reason.DUPLICATE in [r.reason for r in outcome.rejections]


# ---------------------------------------------------- the scorer moves ----


def test_counts_give_precision_recall_and_f1():
    c = Counts(tp=6, fp=2, fn=4)
    assert (c.precision, c.recall) == (0.75, 0.6) and c.f1 == pytest.approx(2 / 3)
    assert Counts() + c == c and Counts().f1 == 1.0
    assert Counts(fp=1).precision == 0.0 and Counts(fn=1).recall == 0.0


def test_a_missed_promise_costs_recall_and_not_precision():
    def drop_company_promises(event, proposals):
        return [
            p for p in proposals
            if not (isinstance(p, MakeCommitment) and p.owner is Party.COMPANY)
        ]  # fmt: skip

    s = evaluated(drop_company_promises)
    assert s.commitments.precision == 1.0 and 0.2 < s.commitments.recall < 0.8
    assert s.alerts.recall < 1.0 and s.alerts.precision == 1.0


def test_an_invented_promise_costs_precision_and_not_recall():
    def invent(event, proposals):
        extra = []
        if getattr(event, "text", "").startswith(("По нашим данным", "По накладной")):
            extra = [MakeCommitment(Party.COMPANY, f"проверить {event.seq}", "По")]
        return [*proposals, *extra]

    s = evaluated(invent)
    assert s.commitments.recall == 1.0 and s.commitments.precision < 1.0
    assert s.commitments.fp == sum(len(t.gold.contradictions) for t in TICKETS)


def test_a_wrong_deadline_costs_deadline_accuracy_and_misfires_alerts():
    def always_ten_minutes(event, proposals):
        return [
            replace(p, due=Offset(10, Unit.MINUTE))
            if isinstance(p, MakeCommitment) and p.due is not None
            else p
            for p in proposals
        ]

    s = evaluated(always_ten_minutes)
    assert s.commitments.f1 == 1.0  # the promises themselves were all found
    assert s.deadline_accuracy < 0.5
    # The two readers now disagree, so the deadline is low-confidence and the
    # system stays silent rather than alert on it: recall is lost, precision is not.
    assert s.alerts.fp == 0 and s.alerts.recall == 0.0


def test_a_customers_you_promised_does_not_become_a_company_promise():
    def believe_the_customer(event, proposals):
        text = getattr(event, "text", "")
        author = getattr(event, "author", None)
        if author and author.side is Side.CUSTOMER and "обещали" in text:
            return [*proposals, MakeCommitment(Party.COMPANY, "что-то", "обещали")]
        return proposals

    complaints = sum(c.fate is Fate.BROKEN for t in TICKETS for c in t.gold.commitments)
    assert complaints > 30
    # The extractor is wrong every time, and the reducer refuses it every time.
    assert evaluated(believe_the_customer).commitments.fp == 0


# ----------------------------------------------------------- the runner ----


class Failing:
    def __init__(self, tickets, fail_ticket):
        self.oracle, self.fail_ticket = Oracle.of(tickets), fail_ticket

    def extract(self, card, event):
        if event.ticket_id == self.fail_ticket and event.seq == 3:
            raise ModelError("http 503", Usage(3, 100, 0, 0, 0.002))
        return self.oracle.extract(card, event)


def test_a_failed_extraction_stops_that_ticket_and_is_reported_not_hidden():
    tickets = TICKETS[:10]
    victim = next(t for t in tickets if any(c.seq >= 3 for c in t.gold.commitments))
    report = evaluate(tickets, Failing(tickets, victim.ticket_id))
    assert report.failures == ((victim.ticket_id, 3, "ModelError: http 503"),)
    assert report.score.commitments.fn > 0 and report.score.commitments.fp == 0
    assert report.usage.usd() == 0.002 and "extraction failures: 1" in report.summary()


def test_a_run_stops_at_its_spending_limit_and_says_so():
    report = evaluate(TICKETS, Altered(TICKETS, lambda e, p: p), max_usd=0.1)
    assert report.stopped_on_budget and report.tickets < len(TICKETS)
    # Every event costs 0.001 here: the run stops on the first ticket that
    # takes it past the limit, and at most that one ticket is over it.
    over = len(TICKETS[report.tickets - 1].events) * 0.001
    assert 0.1 <= report.usage.usd() < 0.1 + over
    assert "STOPPED EARLY" in report.summary()


# ------------------------------------------------------------- rewriting ----


class RewritingModel:
    """Stands in for the writing model: rewrites with a function, records requests."""

    def __init__(self, make):
        self.make, self.requests = make, []

    def complete_json(self, messages, schema, name="output"):
        self.requests.append(messages)
        return Completion({"text": self.make(messages[1]["content"])}, Usage(1, 200, 40, 0, 0.0001))


def test_a_rewrite_keeps_the_planted_spans_and_the_labels_stay_true():
    ticket = next(t for t in TICKETS if t.gold.commitments)
    rewriter = Rewriter(RewritingModel(lambda text: f"Ой, слушайте… {text} Вот так."))
    rewritten = rewriter.rewrite(ticket)
    texts = [e.text for e in rewritten.events if hasattr(e, "text")]
    assert all(text.startswith("Ой, слушайте…") for text in texts) and rewriter.discarded == 0
    assert rewriter.spent.calls == len(texts)
    card, _, _ = run_ticket(rewritten, Oracle.of([rewritten]))
    assert score(rewritten, card).commitments == Counts(tp=len(ticket.gold.commitments))


def test_a_rewrite_that_lost_a_planted_span_is_discarded():
    ticket = next(t for t in TICKETS if t.gold.commitments)
    rewriter = Rewriter(RewritingModel(lambda text: "Здравствуйте, добрый день."))
    rewritten = rewriter.rewrite(ticket)
    anchored = [e for e in ticket.events if ticket.anchors.get(e.id)]
    assert rewriter.discarded == len(anchored) > 0
    for before, after in zip(ticket.events, rewritten.events, strict=True):
        if ticket.anchors.get(before.id):
            assert after == before


def test_the_model_that_writes_the_tickets_may_not_be_the_one_that_reads_them():
    require_different_models("openai/gpt-x-mini", "z-ai/glm-5.3-flash")
    with pytest.raises(SameModel):
        require_different_models("z-ai/glm-5.3-flash", "z-ai/glm-5.3-flash")
    with pytest.raises(SameModel):
        require_different_models("z-ai/glm-5.3-flash:free", "Z-AI/GLM-5.3-Flash")


def test_the_rewriter_is_told_who_is_speaking_and_which_spans_to_keep():
    ticket = next(t for t in TICKETS if t.gold.commitments)
    model = RewritingModel(lambda text: text)
    Rewriter(model).rewrite(ticket)
    promise = next(e for e in ticket.events if getattr(e, "author", None)
                   and e.author.role is Role.AGENT and ticket.anchors[e.id])  # fmt: skip
    request = next(r for r in model.requests if r[1]["content"] == promise.text)
    assert "real agent" in request[0]["content"]
    assert all(f"- {a}" in request[0]["content"] for a in ticket.anchors[promise.id])


@pytest.mark.parametrize(
    ("original", "rewrite"),
    [
        ("Спасибо за терпение.", "Спасибо, что ждали. Мы разберёмся."),
        (
            "Спасибо за звонок насчёт доставки!",
            "Спасибо за звонок насчёт доставки! Так и не привезли",
        ),
        ("Мне пришёл блендер модели BL-419.", "Мне пришёл блендер модели BL-419, но не работает."),
        ("Адрес доставки: ул. Лесная, 7.", "Адрес доставки: ул. Лесная, 7. Когда привезёте?"),
        (
            "Пришлём исправленный счёт до 20 января.",
            "Всё исправим. Пришлём исправленный счёт до 20 января.",
        ),
        ("Есть новости?", "Есть новости? Жду с 12 числа."),
    ],
)
def test_a_rewrite_that_adds_a_promise_a_fact_a_question_or_a_number_is_not_accepted(
    original, rewrite
):
    planted = ("BL-419", "ул. Лесная, 7", "Спасибо за звонок насчёт доставки",
               "Пришлём исправленный счёт до 20 января")  # fmt: skip
    anchors = tuple(a for a in planted if a in original)
    assert not acceptable(original, rewrite, anchors)


def test_a_faithful_rewrite_is_accepted():
    assert acceptable(
        "Новой информации пока нет.", "Здравствуйте! Пока новой информации, увы, нет.", ()
    )
    assert acceptable("Пароль сбросила, не помогло.", "Сбросила пароль — не помогло.", ())


def test_a_rewrite_that_turned_into_a_dialogue_or_ballooned_is_not_accepted():

    assert acceptable("Ладно.", "Ну ладно, поняла.", ())
    assert not acceptable("Ладно.", "Клиент: у меня проблема.\nАгент: Сейчас уточню.", ())
    assert not acceptable("Ладно.", "Ладно. Агент поддержки: уточню и вернусь с ответом.", ())
    assert not acceptable("Ладно.", "Ладно, " + "очень " * 30 + "понятно.", ())
    assert not acceptable("Вернём деньги до пятницы.", "Деньги вернём к пятнице.",
                          ("Вернём деньги до пятницы",))  # fmt: skip


# ------------------------------------------------- round 2: main profile ----

MAIN_TICKETS = generate(seed=13, count=30, profile=MAIN)


def test_the_main_profile_is_long_mostly_filler_and_spans_days_and_handoffs():
    for t in MAIN_TICKETS:
        assert 150 <= t.messages <= 200 and len(t.gold.handoffs) >= 3
        assert (t.end - t.events[0].at).days >= 9
        planted = sum(1 for p in t.proposals if p)
        assert planted < t.messages / 3


def test_twin_promises_are_open_at_once_and_one_is_kept_while_the_other_breaks():
    pairs = 0
    for t in MAIN_TICKETS:
        for kind in TWINS:
            actions = {kind[2].format(obj=obj) for obj in kind[4]}
            twins = [c for c in t.gold.commitments if c.action in actions]
            if not twins:
                continue
            kept, broken = sorted(twins, key=lambda c: c.fate is not Fate.KEPT)
            assert (kept.fate, broken.fate) == (Fate.KEPT, Fate.BROKEN)
            # The confirmation of one arrives while the other is still owed.
            assert broken.seq < kept.resolved_seq and not broken.closed(kept.resolved_seq)
            pairs += 1
    assert pairs >= len(MAIN_TICKETS)


def test_the_main_profile_plants_renegotiations_and_cancellations_with_true_labels():
    fates = {c.fate for t in MAIN_TICKETS for c in t.gold.commitments}
    assert {Fate.RENEGOTIATED, Fate.CANCELLED} <= fates
    for t in MAIN_TICKETS:
        card, _, failure = run_ticket(t, Oracle.of([t]))
        assert failure is None
        by_seq = {c.source.seq: c for c in card.commitments}
        expected = {
            Fate.KEPT: CommitmentState.FULFILLED,
            Fate.CANCELLED: CommitmentState.CANCELLED,
            Fate.RENEGOTIATED: CommitmentState.SUPERSEDED,
            Fate.BROKEN: CommitmentState.OPEN,
            Fate.OPEN: CommitmentState.OPEN,
        }
        for gold in t.gold.commitments:
            assert by_seq[gold.seq].state is expected[gold.fate]
            if gold.fate is Fate.RENEGOTIATED:
                assert by_seq[gold.resolved_seq].superseded_by is None
                assert by_seq[gold.seq].superseded_by == by_seq[gold.resolved_seq].id


def test_every_planted_close_passes_the_guards_and_the_oracle_scores_one():
    report = evaluate(MAIN_TICKETS, Oracle.of(MAIN_TICKETS))
    s = report.score + report.handoffs
    assert (s.commitments.fp, s.commitments.fn, s.alerts.fp, s.alerts.fn) == (0, 0, 0, 0)
    assert s.status_accuracy == 1.0 and s.closes_right > 300
    assert (s.closes_false, s.closes_wrong_target, s.closes_missed) == (0, 0, 0)
    assert s.suggestions_right + s.suggestions_wrong == 0


def test_round_one_profiles_generate_exactly_the_tickets_round_one_was_run_on():
    stored = json.loads((Path(__file__).parents[1] / "results/tickets/test.json").read_text())
    for t in SPLITS["test"].tickets():
        messages = {e.id for e in t.events if not isinstance(e, AssigneeChanged)}
        assert messages == set(stored["tickets"][t.ticket_id]["texts"])


def test_round_two_splits_use_fresh_seeds():
    old = {SPLITS[n].seed for n in ("dev", "test", "long")}
    assert SPLITS["dev2"].seed not in old and SPLITS["test2"].seed not in old
    assert SPLITS["test2"].count >= 20 and SPLITS["test2"].profile is MAIN
    fresh = SPLITS["test3"]
    assert fresh.seed not in old | {SPLITS["dev2"].seed, SPLITS["test2"].seed}
    assert fresh.count == 20 and fresh.profile is MAIN
