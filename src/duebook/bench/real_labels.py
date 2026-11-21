"""Human labels and end-of-transcript commitment scoring for public messages."""

from __future__ import annotations

from collections import Counter

from ..card import Party
from ..events import MessagePosted
from .generate import Fate, GoldCommitment
from .real_sources import instant, source_messages, to_ticket
from .result import PromiseStatus
from .score import Counts, match, same_deadline

STATES = {"open_observed", "claimed", "fulfilled", "cancelled", "superseded", "unknown"}
UNCLEAR = {"identity", "ownership", "conditional", "other"}


def reviewed(bundle, labels, bundle_sha):
    if labels["bundle_sha256"] != bundle_sha:
        raise ValueError("annotation was made for another bundle")
    by_ticket = {entry["ticket_id"]: entry for entry in labels["tickets"]}
    if len(by_ticket) != len(labels["tickets"]) or set(by_ticket) != {
        case["id"] for case in bundle["cases"]
    }:
        raise ValueError("annotation must cover every frozen ticket exactly once")
    for case in bundle["cases"]:
        label = by_ticket[case["id"]]
        if label["review"] != "complete" or not label["annotator"].strip():
            raise ValueError(f"unfinished human labels for {case['id']}")
        if case["cohort"] == "challenge" and (
            not label["second_reviewer"].strip()
            or label["second_reviewer"] == label["annotator"]
            or not label["adjudication"].strip()
        ):
            raise ValueError(f"missing independent challenge review for {case['id']}")
        validate(case, label)
    return by_ticket


def validate(case, label):
    ticket = to_ticket(case)
    messages = {m["id"]: (seq, m) for seq, m in source_messages(case, ticket).items()}
    actors = {e.author.id for e in ticket.events if isinstance(e, MessagePosted)}
    actors.update(m["actor_id"] for _, m in messages.values())

    def evidence(items, earliest=1):
        for item in items:
            seq, message = messages[item["message_id"]]
            quote = item["quote"]
            if seq < earliest or not quote.strip() or quote not in message["text"]:
                raise ValueError(f"invalid evidence for {case['id']} at {message['id']}")

    keys = set()
    for c in label["commitments"]:
        if (
            not c["id"].strip()
            or c["id"] in keys
            or not c["action"].strip()
            or not c["opening"]
            or c["owner_side"] not in ("customer", "company")
            or c["owner_actor_id"] is not None
            and c["owner_actor_id"] not in actors
        ):
            raise ValueError(f"invalid commitment identity or owner for {case['id']}")
        keys.add(c["id"])
        evidence(c["opening"])
        first = messages[c["opening"][0]["message_id"]][0]
        evidence(c["opening"], first)
        deadline = c["deadline"]
        kind, at, source = deadline["kind"], deadline["at"], deadline["evidence"]
        if (
            kind not in ("absent", "exact", "ambiguous")
            or (kind == "exact") != bool(at)
            or kind != "absent"
            and not source
        ):
            raise ValueError(f"invalid deadline for {case['id']}")
        if at:
            instant(at)
        evidence(source, first)
        state, source = c["fulfillment"]["state"], c["fulfillment"]["evidence"]
        if state not in STATES or state != "unknown" and not source:
            raise ValueError(f"invalid fulfillment for {case['id']}")
        evidence(source, first)
    for item in label["ambiguous"]:
        if item["kind"] not in UNCLEAR or not item["evidence"] or not item["note"].strip():
            raise ValueError(f"invalid ambiguity for {case['id']}")
        evidence(item["evidence"])


def compare(case, label, snapshot):
    """Use synthetic anchor matching, then grade only evidence-backed fields."""
    ids = {m["id"]: seq for seq, m in source_messages(case, to_ticket(case)).items()}
    excluded = {
        ids[item["evidence"][0]["message_id"]]
        for item in label["ambiguous"]
        if item["kind"] in ("identity", "ownership")
    }
    labelled = [
        c for c in label["commitments"] if ids[c["opening"][0]["message_id"]] not in excluded
    ]
    gold = [
        GoldCommitment(
            ids[c["opening"][0]["message_id"]],
            Party(c["owner_side"]),
            c["action"],
            instant(c["deadline"]["at"]) if c["deadline"]["at"] else None,
            Fate.OPEN,
        )
        for c in labelled
    ]
    found = [p for p in snapshot.promises if p.activated_seq is None and p.seq not in excluded]
    pairs, extra, missing = match(gold, found)
    labels = dict(zip(map(id, gold), labelled, strict=True))
    gold_anchors = Counter((g.seq, g.owner.side) for g in gold)
    found_anchors = Counter((p.seq, p.side) for p in found)
    counts: Counter[str] = Counter(excluded_anchors=len(excluded))
    errors = []

    def error(kind, *seqs):
        errors.append((kind, tuple(dict.fromkeys(seq for seq in seqs if seq is not None))))

    for g in missing:
        later = {ids[e["message_id"]] for e in labels[id(g)]["opening"][1:]}
        swapped = any(p.seq == g.seq and p.side != g.owner.side for p in extra)
        if swapped:
            error("owing_side_mismatch", g.seq)
        elif alternate := next(
            (p for p in extra if p.seq in later and p.side == g.owner.side), None
        ):
            error("possible_restatement_anchor", g.seq, alternate.seq)
        else:
            error("missed_commitment_anchor", g.seq)
    for p in extra:
        kind = "extra_commitment_same_anchor" if gold_anchors[p.seq, p.side] else "spurious_anchor"
        error(kind, p.seq)
    for g, p in pairs:
        anchor = g.seq, g.owner.side
        if gold_anchors[anchor] > 1 or found_anchors[anchor] > 1:
            error("identity_review_multiple_actions", g.seq)
            continue
        c = labels[id(g)]
        deadline, fulfillment = c["deadline"], c["fulfillment"]
        if deadline["kind"] != "ambiguous":
            counts["deadline_judged"] += 1
            if same_deadline(g.due_at, p.due_at):
                counts["deadline_right"] += 1
            else:
                error(
                    "invented_deadline"
                    if g.due_at is None
                    else "missed_deadline"
                    if p.due_at is None
                    else "wrong_deadline",
                    g.seq,
                )
        state = fulfillment["state"]
        source = [ids[e["message_id"]] for e in fulfillment["evidence"]]
        if state == "unknown":
            counts["fulfillment_unknown"] += 1
            continue
        expected = PromiseStatus.OPEN if state == "open_observed" else PromiseStatus(state)
        counts["status_judged"] += 1
        if p.status == expected:
            counts["status_right"] += 1
            if p.status != PromiseStatus.OPEN and p.closed_seq not in source:
                error("wrong_or_missing_closing_evidence", g.seq, p.closed_seq, *source)
        elif p.status == PromiseStatus.FULFILLED:
            error("unsupported_fulfillment", g.seq, p.closed_seq, *source)
        elif expected == PromiseStatus.FULFILLED:
            error("missed_fulfillment", g.seq, *source)
        else:
            error("wrong_commitment_state", g.seq, p.closed_seq, *source)
    return Counts(len(pairs), len(extra), len(missing)), counts, errors
