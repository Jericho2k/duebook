import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import time, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from duebook import AssigneeChanged, Card, CommitmentState, Party, Reason, StepOutcome, apply
from duebook.bench import (
    LONG,
    MAIN,
    MAIN2,
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
)
from duebook.bench.experiment import to_json
from duebook.bench.generate import MAIN3, PROMISE_BEATS, TWINS, Beat, CloseCase
from duebook.bench.match import mentions
from duebook.bench.rewrite import acceptable
from duebook.bench.run import fold
from duebook.closing import overlap
from duebook.events import Role, Side
from duebook.extract import Completion, ModelError, Usage
from duebook.extract.extractor import Extraction
from duebook.proposals import MakeCommitment
from duebook.reducer import Guards
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


def test_the_benchmark_runs_without_a_database_driver():
    check = "import sys, duebook.bench.experiment; sys.exit('psycopg' in sys.modules)"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    assert subprocess.run([sys.executable, "-c", check], env=env).returncode == 0


# -------------------------------------------- labels are true by design ----


def test_each_label_describes_what_the_card_really_ends_up_holding():
    for t in TICKETS:
        folded = fold(t, Oracle.of([t]))
        card = folded.card
        assert folded.failure is None
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
            extra = [MakeCommitment(Party.COMPANY, f"проверить {event.seq:03d}", "По")]
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


class AlwaysUnreadable(Failing):
    def extract(self, card, event):
        if event.ticket_id == self.fail_ticket and event.seq == 3:
            raise ModelError("cut off", Usage(2, 100, 0, 0, 0.002), unreadable=True)
        return self.oracle.extract(card, event)


def test_a_message_unreadable_on_every_attempt_is_recorded_and_the_ticket_goes_on():
    ticket = TICKETS[0]
    folded = fold(ticket, AlwaysUnreadable([ticket], ticket.ticket_id))
    assert folded.failure is None and folded.card.last_seq == len(ticket.events)
    (item,) = folded.card.unread
    assert item.source.seq == 3 and folded.usage.usd() == pytest.approx(0.006)


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
    s = evaluate([rewritten], Oracle.of([rewritten])).score
    assert s.commitments == Counts(tp=len(ticket.gold.commitments))


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
        folded = fold(t, Oracle.of([t]))
        card = folded.card
        assert folded.failure is None
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


@pytest.mark.parametrize("name", SPLITS)
def test_the_oracle_scores_perfectly_at_the_end_and_handoffs_of_every_split(name):
    tickets = SPLITS[name].tickets()
    report = evaluate(tickets, Oracle.of(tickets))
    for s in (report.score, report.handoffs):
        assert s.commitments.f1 == s.alerts.f1 == s.disputes.f1 == s.failed_steps.f1 == 1.0
        assert s.deadline_accuracy == s.status_accuracy == s.close_precision == 1.0
        assert s.change_accuracy == s.fact_recall == 1.0 and s.freshness_error == 0.0
        assert s.closes_missed == s.suggestions_right == s.suggestions_wrong == s.errors == 0
        assert all(getattr(s, case.value).recall == 1.0 for case in CloseCase)
        assert s.offers.f1 == s.condition_alerts.f1 == 1.0
        assert s.offer_states_right == s.offer_states_judged
    every = report.score + report.handoffs
    assert every.commitments.tp and every.alerts.tp and every.disputes.tp and every.failed_steps.tp
    assert report.handoffs.checkpoints == sum(len(t.gold.handoffs) for t in tickets)
    if SPLITS[name].profile in (MAIN2, MAIN3):
        assert all(getattr(report.score, case.value).tp == len(tickets) for case in CloseCase)


def test_round_one_profiles_generate_exactly_the_tickets_round_one_was_run_on():
    stored = json.loads((Path(__file__).parents[1] / "results/tickets/test.json").read_text())
    for t in SPLITS["test"].tickets():
        messages = {e.id for e in t.events if not isinstance(e, AssigneeChanged)}
        assert messages == set(stored["tickets"][t.ticket_id]["texts"])


def test_every_split_has_its_own_seed_and_every_test_split_20_tickets():
    assert len({split.seed for split in SPLITS.values()}) == len(SPLITS)
    assert all(s.count >= 20 for name, s in SPLITS.items() if name.startswith("test"))


def digest_of(tickets) -> str:
    data = json.dumps(
        tickets, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=time.isoformat
    )
    return sha256(data.encode("utf-8")).hexdigest()


# Round 4 widened the planted span of the two device-model statements to the
# word that makes the code a model; nothing else changed, and round 2's records
# score the same against the tickets generated now.
@pytest.mark.parametrize(
    ("name", "digest"),
    [
        ("dev2", "86d842568209e51c98a254bae25119e05913143e78e79db28b82ca8f582daded"),
        ("test2", "8469160533fcb977f257e2cbbbdf550e784b9bed3c13e50ffbe6e39377c80aa0"),
    ],
)
def test_round_two_generated_tickets_match_the_hashes_captured_before_main2(name, digest):
    tickets = to_json(SPLITS[name].tickets())
    for ticket in tickets:
        # Compare the full round-2 record using its original schema.
        assert ticket["gold"].pop("close_cases") == [] and ticket["gold"].pop("beats") == []
        assert ticket["gold"].pop("conditionals") == []
        for proposals in ticket["proposals"]:
            for proposal in proposals:
                assert proposal.pop("context_quote", "") == ""
    assert digest_of(tickets) == digest


# Captured before Round 8 added its beats: every earlier split is unchanged.
@pytest.mark.parametrize(
    ("name", "digest"),
    [
        ("dev", "feaf64c23f238b8d2facab55cc30ca93d20bd3aa141021080627a08b35012d01"),
        ("test", "6a1951e375b01a8b7e836e426e35d7f6a76b25a258639f0537dcbdaaf7684586"),
        ("long", "8fdd677923744a857bea058ce98a60cca615a21065e05b9b96532169c41c23cc"),
        ("dev3", "f3e4320e9d36bbe7180f3a28ad88aa9bee6ac703a07d0a3f8c81d3b644052c44"),
        ("test3", "aea3c2b06e4b780f6341e836241694eeb2f4de5a9ffb15f0ff67afa8fc42b4cf"),
        ("dev4", "6b68885372bc2758ec179fc3e4e608dc16c2755a06881fc779af0213f8ada4a8"),
    ],
)
def test_earlier_splits_match_the_hashes_captured_before_main3(name, digest):
    tickets = to_json(SPLITS[name].tickets())
    for ticket in tickets:
        assert ticket["gold"].pop("beats") == [] and ticket["gold"].pop("conditionals") == []
    assert digest_of(tickets) == digest


# Captured before Round 9 changed the conditional offer's labels: the messages
# Round 8 rewrote and read are unchanged.
@pytest.mark.parametrize(
    ("name", "digest"),
    [
        ("dev5", "c615074f51281724c4044090a23f2cca10598efcbe5ee89ba625b71e7c21a31a"),
        ("test4", "2eccd59915fb8e9739afc8fb7617496749b8bb9a11fe1d1c2fb0474f47ad3db4"),
    ],
)
def test_round_eight_messages_match_the_hashes_captured_before_round_nine(name, digest):
    tickets = [{"events": to_json(t.events), "anchors": t.anchors} for t in SPLITS[name].tickets()]
    assert digest_of(tickets) == digest


def test_round_eight_splits_plant_every_beat():
    for name in ("dev5", "test4"):
        assert SPLITS[name].profile is MAIN3
        kinds = set()
        for ticket in SPLITS[name].tickets():
            mine = [b.kind for b in ticket.gold.beats]
            kinds |= set(mine)
            assert mine.count(Beat.SAME_ACTION) == 2
            assert mine.count(Beat.DELEGATED_DATED) + mine.count(Beat.DELEGATED_UNDATED) == 2
        assert kinds == set(Beat)


def test_a_beat_that_is_not_a_promise_plants_no_commitment():
    for ticket in SPLITS["dev5"].tickets():
        promised = {c.seq for c in ticket.gold.commitments}
        for beat in ticket.gold.beats:
            made = [p for p in ticket.proposals[beat.seq - 1] if isinstance(p, MakeCommitment)]
            assert bool(made) is (beat.kind in PROMISE_BEATS) is (beat.seq in promised)


def test_two_promises_with_one_action_survive_only_under_promise_identity():
    for ticket in SPLITS["dev5"].tickets():
        pair = [b.seq for b in ticket.gold.beats if b.kind is Beat.SAME_ACTION]
        for guards, kept in ((Guards(), 2), (Guards(rules=3), 1)):
            card = fold(ticket, Oracle.of([ticket]), guards=guards).card
            assert sum(c.source.seq in pair for c in card.commitments) == kept


def test_the_round_three_splits_are_long_and_span_days_and_handoffs():
    for name in ("dev3", "test3", "dev4"):
        assert SPLITS[name].profile is MAIN2
        for ticket in SPLITS[name].tickets():
            assert 150 <= ticket.messages <= 200 and 3 <= len(ticket.gold.handoffs) <= 4
            assert (ticket.end - ticket.events[0].at).days >= 9


@pytest.mark.parametrize("name", ["dev3", "test3"])
def test_rescue_closes_only_its_intended_twin_and_needs_the_verifier(name):
    for ticket in SPLITS[name].tickets():
        (case,) = [case for case in ticket.gold.close_cases if case.kind is CloseCase.RESCUE]
        by_seq = {c.seq: c for c in ticket.gold.commitments}
        left, right = (by_seq[seq] for seq in case.promise_seqs)
        assert left.owner.side is right.owner.side and left.seq != right.seq
        reply = ticket.events[case.seq - 1].text
        for promise in (left, right):
            assert promise.seq < case.seq and not promise.closed(case.seq - 1)
            assert overlap(reply, (ticket.events[promise.seq - 1].text, promise.action)) == 0
        assert case.target_seq in case.promise_seqs
        assert [p.seq for p in (left, right) if p.done(case.seq)] == [case.target_seq]
        deterministic = fold(ticket, Oracle.of([ticket]), guards=Guards(verifier=False))
        assert [(r.seq, r.reason) for r in deterministic.refused] == [
            (case.seq, Reason.NOT_ABOUT_TARGET)
        ]


def test_negative_close_cases_leave_all_relevant_promises_open():
    for ticket in SPLITS["dev3"].tickets():
        for case in ticket.gold.close_cases:
            open_before = {
                c.seq
                for c in ticket.gold.commitments
                if c.seq < case.seq and not c.closed(case.seq)
            }
            if case.kind is CloseCase.AMBIGUOUS:
                assert len(case.promise_seqs) == 2 and set(case.promise_seqs) <= open_before
                for seq in case.promise_seqs:
                    assert overlap(ticket.events[case.seq - 1].text,
                                   (ticket.events[seq - 1].text,)) == 0  # fmt: skip
            elif case.kind is CloseCase.WRONG_THING:
                assert len(case.promise_seqs) == 1 and set(case.promise_seqs) == open_before
            if case.target_seq is None:
                assert not ticket.proposals[case.seq - 1]


def test_rewriting_keeps_the_close_case_replies_and_twin_context_verbatim():
    ticket = SPLITS["dev3"].tickets()[0]
    writer = RewritingModel(lambda text: f"Ой, слушайте… {text}")
    rewritten = Rewriter(writer).rewrite(ticket)
    for case in ticket.gold.close_cases:
        assert rewritten.events[case.seq - 1] == ticket.events[case.seq - 1]
        if case.kind is not CloseCase.WRONG_THING:
            assert rewritten.events[case.seq - 2] == ticket.events[case.seq - 2]
    assert len(writer.requests) == ticket.messages - 5


def test_a_product_code_is_kept_together_with_the_word_that_makes_it_a_model():
    ticket = SPLITS["dev3"].tickets()[0]
    dispute = next(d for d in ticket.gold.contradictions if d.slot == "device.model")
    told, recorded = (ticket.events[seq - 1] for seq in dispute.seqs)
    # A rewrite may keep the code and change what it is; that one is discarded.
    assert not acceptable(recorded.text, f"Номер вашей накладной — {dispute.values[1]}.",
                          ticket.anchors[recorded.id])  # fmt: skip
    assert not acceptable(told.text, f"Мне пришёл заказ {dispute.values[0]}.",
                          ticket.anchors[told.id])  # fmt: skip
