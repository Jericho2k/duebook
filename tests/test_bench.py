import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from duebook import Card, CommitmentState, Party, Reason, StepOutcome, apply
from duebook.bench import (
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


def test_the_benchmark_runs_without_a_database_driver():
    check = "import sys, duebook.bench; sys.exit('psycopg' in sys.modules)"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    assert subprocess.run([sys.executable, "-c", check], env=env).returncode == 0


# -------------------------------------------- labels are true by design ----


def test_the_planted_proposals_through_the_real_reducer_score_exactly_one():
    s = evaluate(TICKETS, Oracle.of(TICKETS)).score
    assert (s.commitments.fp, s.commitments.fn, s.alerts.fp, s.alerts.fn) == (0, 0, 0, 0)
    assert s.commitments.tp == sum(len(t.gold.commitments) for t in TICKETS) > 200
    assert s.deadline_accuracy == 1.0 and s.commitments.f1 == 1.0
    assert s.alerts.tp == sum(c.fate is Fate.BROKEN for t in TICKETS for c in t.gold.commitments)


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
            c.slot for c in t.gold.contradictions
        }
        for fix in t.gold.repeated_fixes:
            (step,) = [s for s in card.steps if s.action == fix.action]
            assert step.outcome is StepOutcome.FAILED and step.outcome_source.seq == fix.failed_seq


def test_suggesting_a_failed_fix_again_is_refused_by_the_reducer_as_already_known():
    ticket = next(t for t in TICKETS if t.gold.repeated_fixes)
    (fix,) = ticket.gold.repeated_fixes
    card = Card(ticket.ticket_id)
    for event, proposals in zip(ticket.events, ticket.proposals, strict=True):
        outcome = apply(card, event, proposals)
        card = outcome.card
        if event.seq == fix.repeated_seq:
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
        if getattr(event, "text", "").startswith("По трекингу"):
            extra = [MakeCommitment(Party.COMPANY, "проверить трекинг", "По трекингу")]
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
    report = evaluate(TICKETS, Altered(TICKETS, lambda e, p: p), max_usd=0.05)
    assert report.stopped_on_budget and report.tickets < len(TICKETS)
    assert 0.05 <= report.usage.usd() < 0.08  # at most one ticket past the limit
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
    rewriter = Rewriter(RewritingModel(lambda text: "Здравствуйте, чем могу помочь?"))
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
