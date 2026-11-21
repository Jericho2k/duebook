"""Report observed pipeline decisions and provenance for human adjudication."""

from __future__ import annotations

from collections import Counter, defaultdict

from ...card import Closure
from ...events import MessagePosted
from ...reducer import PARTIAL, Reason
from ...store.codec import encode
from .labels import Labels, require
from .replay import Decision, Trace, closure_of, predictions

EXPECTED_CLOSE = {
    "claimed": Closure.RESOLVED,
    "fulfilled": Closure.RESOLVED,
    "cancelled": Closure.CANCELLED,
    "superseded": Closure.SUPERSEDED,
    "lapsed": Closure.LAPSED,
    "open": Closure.ACTIVATED,
}


def target_of(labels: Labels, prediction: str | None) -> str | None:
    review = labels.judgments.get(prediction)
    return review["gold"][0] if review and review["judgment"] == "equivalent" else None


def describe(trace: Trace, rows: tuple[Decision, ...]) -> dict:
    observed = {}
    for card in trace.cards.values():
        for ident, prediction in predictions(card).items():
            observed.setdefault(ident, prediction)
    return {
        "presented_identities": list(observed.values()),
        "proposals": [decision_record(trace, row) for row in rows],
        "processed_events": len(trace.proposals),
        "failure": trace.failure,
    }


def decision_record(trace: Trace, decision: Decision) -> dict:
    return {
        "seq": decision.seq,
        "source_id": trace.ticket.events[decision.seq - 1].id,
        "index": decision.index,
        "proposal": encode(decision.proposal),
        "target": decision.target,
        "created": decision.created,
        "admitted": decision.admitted,
        "rejection_reasons": decision.reasons,
        "suggestion_on": decision.suggestion_on,
    }


def _inspect(labels, decision, review):
    found = []
    verification = getattr(decision.proposal, "verification", None)
    expected = review.get("expected_target") if review else None
    actual = target_of(labels, decision.target)
    if verification and verification.error:
        found.append("verification_execution_failure")
    if any(reason in PARTIAL for reason in decision.reasons):
        found.append("deadline_rejected_by_reducer")
    if decision.reasons:
        found.append("observed_reducer_rejection")
    if Reason.VERIFIER_REJECTED in decision.reasons:
        found.append("stored_verifier_veto_applied")
    if review is None or review["decision"] == "uncertain":
        return found + ["decision_unjudged"]
    if expected is not None and expected != actual:
        found.append("proposal_target_unjudged" if actual is None else "wrong_proposal_target")
    admit = review["decision"] == "admit"
    if (
        verification
        and not verification.error
        and (
            admit
            and actual == expected
            and verification.verdict == "no"
            or not admit
            and verification.verdict == "yes"
        )
    ):
        found.append("verification_disagreed_with_adjudication")
    if admit:
        found.append("labeled_admission" if decision.admitted else "adjudicated_admission_blocked")
    else:
        found.append("unsafe_admission" if decision.admitted else "correct_rejection")
    if review.get("suggestion"):
        hints = decision.suggestion_on
        retained = any(target_of(labels, p) == expected for p in hints) if expected else bool(hints)
        found.append("review_suggestion_retained" if retained else "review_suggestion_missing")
    return found


def attribute(trace: Trace, labels: Labels, rows: tuple[Decision, ...], lifecycle: dict) -> dict:
    known = {(d.seq, d.index) for d in rows}
    require(
        labels.proposal_reviews.keys() <= known, "proposal review points outside saved proposals"
    )
    known_ids = {d.created for d in rows if d.created is not None}
    require(labels.judgments.keys() <= known_ids, "identity review points outside saved creations")
    observations = []
    by_seq = defaultdict(list)
    for d in rows:
        by_seq[d.seq].append(d)
        review = labels.proposal_reviews.get((d.seq, d.index))
        findings = _inspect(labels, d, review)
        observations.append(decision_record(trace, d) | {"findings": findings, "review": review})
    gaps = []
    for row in lifecycle["creation"]["rows"]:
        if row["result"] not in {"delayed", "never"}:
            continue
        seq, gold = row["origin_seq"], row["gold"]
        attempts = [d for d in by_seq[seq] if d.created]
        same = [d for d in attempts if target_of(labels, d.created) == gold]
        if trace.cards[seq].last_seq < seq or any(
            u.source.seq == seq for u in trace.cards[seq].unread
        ):
            finding = "extraction_failed_or_unread"
        elif not attempts:
            finding = "no_creation_proposal_at_origin"
        elif same and not any(d.created in predictions(trace.cards[seq]) for d in same):
            finding = "equivalent_creation_rejected"
        elif any(gold in labels.judgments.get(d.created, {}).get("gold", []) for d in attempts):
            finding = "creation_semantics_or_ownership_disagreed"
        else:
            finding = "creation_cause_unresolved"
        gaps.append(
            {
                "gold": gold,
                "seq": seq,
                "finding": finding,
                "proposal_indices": [d.index for d in attempts],
            }
        )
    transition_gaps = []
    for row in lifecycle["transitions"]["rows"]:
        expected = EXPECTED_CLOSE.get(row["expected"])
        if row["dimension"] != "state" or not expected or row["result"] in {"correct", "unjudged"}:
            continue
        seq, gold = row["seq"], row["gold"]
        candidates = [d for d in by_seq[seq] if closure_of(d.proposal)[1] == expected]
        same = [d for d in candidates if target_of(labels, d.target) == gold]
        if trace.cards[seq].last_seq < seq or any(
            u.source.seq == seq for u in trace.cards[seq].unread
        ):
            finding = "transition_extraction_failed_or_unread"
        elif not candidates:
            finding = "no_transition_proposal_at_evidence"
        elif same and all(not d.admitted for d in same):
            finding = "targeted_transition_rejected"
        else:
            finding = "transition_cause_unresolved"
        transition_gaps.append(
            row | {"finding": finding, "proposal_indices": [d.index for d in candidates]}
        )
    roles = []
    for seq, expected in labels.source_sides.items():
        event = trace.ticket.events[seq - 1]
        if isinstance(event, MessagePosted) and event.author.side != expected:
            roles.append({"seq": seq, "observed": event.author.side, "adjudicated": expected})
    unread = trace.cards[len(trace.ticket.events)].unread
    totals = Counter(item for row in observations for item in row["findings"])
    totals.update(row["finding"] for row in gaps)
    totals.update(row["finding"] for row in transition_gaps)
    return {
        "counts": dict(totals),
        "proposal_decisions": observations,
        "creation_gaps": gaps,
        "transition_gaps": transition_gaps,
        "proposals_observed": len(rows),
        "proposals_reviewed": len(labels.proposal_reviews),
        "role_mapping_mismatches": roles,
        "extraction": {
            "held_at": len(trace.proposals) + 1 if trace.failure else None,
            "failure": trace.failure,
            "unread": [{"seq": u.source.seq, "error": u.error} for u in unread],
        },
    }
