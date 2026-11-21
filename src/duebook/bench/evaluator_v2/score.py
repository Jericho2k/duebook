"""Score semantic identities, observed lifecycle and alert episodes."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from ...card import Party
from ...events import MessagePosted
from ...lifecycle import AlertPolicy, NoticeKind, notices
from ..score import TOLERANCE, Counts, same_deadline
from .labels import AlertWindow, Expected, Labels, Obligation, require
from .replay import Frame, Prediction, Trace, logical_id, predictions

TERMINAL = {"fulfilled", "cancelled", "superseded", "lapsed"}
OWED = {"open", "claimed"}
DEADLINE_ERRORS = {"invented_deadline", "missed_deadline", "wrong_deadline"}
MISSED_STATE = {
    "fulfilled": "missed_fulfillment",
    "cancelled": "missed_cancellation",
    "superseded": "missed_supersession",
    "lapsed": "missed_lapse",
}


@dataclass(frozen=True, slots=True)
class Pairing:
    matched: dict[str, Prediction]
    missing: dict[str, Obligation]
    extra: dict[str, str]
    excluded_gold: int
    excluded_predictions: int
    unclear: set[str]

    @property
    def counts(self) -> Counts:
        return Counts(len(self.matched), len(self.extra), len(self.missing))


def match_identities(labels: Labels, card: dict[str, Prediction], seq: int,
                     previous: dict[str, Prediction]) -> Pairing:  # fmt: skip
    """Each accepted edge has one human-identified target. Keep its incumbent if possible."""
    active = {key: obligation for key, obligation in labels.obligations.items()
              if obligation.seq <= seq}  # fmt: skip
    eligible = {key for key, g in active.items() if g.certain and g.owner is not None}
    unclear = set()
    extra, candidates = {}, defaultdict(list)
    excluded = 0
    for key, pred in card.items():
        require(key in labels.judgments, f"missing human identity review of {key}")
        review = labels.judgments[key]
        verdict, targets = review["judgment"], review["gold"]
        if verdict == "uncertain":
            excluded += 1
            unclear.update(targets)
        elif verdict != "equivalent":
            extra[key] = verdict
        else:
            gold = labels.obligations[targets[0]]
            if not gold.certain or gold.owner is None:
                excluded += 1
            elif pred.owner != gold.owner or (
                gold.actor is not None
                and pred.owner is not Party.COMPANY
                and pred.actor != gold.actor
            ):
                extra[key] = "wrong_owner"
            elif bool(pred.condition) != gold.conditional:
                extra[key] = "wrong_condition"
            elif gold.id not in eligible:
                extra[key] = "premature_creation"
            else:
                candidates[gold.id].append(pred)
    matched = {}
    for target, options in candidates.items():
        incumbent = previous.get(target)
        chosen = min(
            options, key=lambda p: (incumbent is None or p.id != incumbent.id, p.seq, p.id)
        )
        matched[target] = chosen
        extra.update({p.id: "duplicate_commitment" for p in options if p.id != chosen.id})
    missing = {key: active[key] for key in sorted(eligible - matched.keys() - unclear)}
    return Pairing(matched, missing, extra, len(active) - len(matched) - len(missing), excluded,
                   unclear)  # fmt: skip


def metrics(c: Counts) -> dict:
    return {"tp": c.tp, "fp": c.fp, "fn": c.fn,
            "precision": c.precision if c.tp + c.fp else None,
            "recall": c.recall if c.tp + c.fn else None,
            "f1": c.f1 if c.tp + c.fp + c.fn else None}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    identity: str
    evidence_seqs: tuple[int, ...]
    prediction: str | None = None


class Durations:
    def __init__(self) -> None:
        self.open: dict[tuple[str, str, str | None], dict] = {}
        self.closed: list[dict] = []

    def observe(
        self,
        frame: Frame,
        following: Frame | None,
        findings: list[Finding],
        unknown_state: set[str],
        unknown_deadline: set[str],
    ) -> None:
        point = {"seq": frame.seq, "at": frame.at, "kind": frame.kind}
        current = {(f.kind, f.identity, f.prediction): f for f in findings}
        for key in self.open.keys() - current.keys():
            kind, identity, _ = key
            unknown = unknown_deadline if kind in DEADLINE_ERRORS else unknown_state
            disposition = "censored" if identity in unknown else "temporary"
            self.closed.append(self.open.pop(key) | {"until": point, "disposition": disposition})
        for key, f in current.items():
            span = self.open.setdefault(
                key,
                {
                    "kind": f.kind,
                    "identity": f.identity,
                    "prediction": f.prediction,
                    "from": point,
                    "message_checkpoints": 0,
                    "seconds": 0.0,
                    "evidence_seqs": [],
                },
            )
            span["message_checkpoints"] += frame.kind == "message"
            if following is not None:
                span["seconds"] += seconds(frame.at, following.at)
            span["evidence_seqs"] = sorted(set(span["evidence_seqs"]) | set(f.evidence_seqs))
        if following is None:
            self.closed.extend(
                span | {"until": point, "disposition": "persistent"} for span in self.open.values()
            )
            self.open.clear()

    def report(self) -> dict:
        groups = defaultdict(Counter)
        self.closed.sort(
            key=lambda s: (
                s["from"]["at"],
                s["from"]["seq"],
                s["kind"],
                s["identity"],
                s["prediction"] or "",
            )
        )
        for span in self.closed:
            counts = groups[span["kind"]]
            counts["occurrences"] += 1
            counts[span["disposition"]] += 1
            counts["message_checkpoints"] += span["message_checkpoints"]
            counts["seconds"] += span["seconds"]
        return {"by_kind": dict(groups), "episodes": self.closed}


def seconds(start: datetime, end: datetime) -> float:
    return (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds()


def compare_state(gold: Expected, pred: Prediction, replacement: Prediction | None) -> str | None:
    if gold.state == "unknown":
        return None
    if pred.state == gold.state:
        if pred.state == "superseded" and (
            replacement is None or pred.replacement != replacement.id
        ):
            return "wrong_replacement_target"
        if (
            pred.state != "pending"
            and pred.state_seq not in (gold.state_seq, *gold.later_evidence)
            and (pred.state != "open" or pred.condition)
        ):
            return "wrong_transition_evidence"
        return None
    if pred.state == "fulfilled" and gold.state == "claimed":
        return "claim_misverified"
    if pred.state in TERMINAL and gold.state in OWED | {"pending"}:
        return "false_close"
    if gold.state in TERMINAL and pred.state not in TERMINAL:
        return MISSED_STATE[gold.state]
    if gold.state == "pending" and pred.state != "pending":
        return "premature_activation"
    if pred.state == "pending" and gold.state in OWED:
        return "missed_activation"
    return "wrong_state"


def _compare(labels: Labels, paired: Pairing, card, frame, tallies, previous, last_seen):
    findings = [Finding(kind, k, (card[k].seq,), k) for k, kind in paired.extra.items()]
    findings += [
        Finding("lost_commitment" if k in last_seen else "missing_commitment", k, (g.seq,))
        for k, g in paired.missing.items()
    ]
    changes = []
    for key, pred in paired.matched.items():
        gold = labels.obligations[key].at(frame.seq)
        tallies["matched"] += 1
        if gold.deadline == "unknown":
            tallies["deadline_excluded"] += 1
        else:
            tallies["deadline_judged"] += 1
            if same_deadline(gold.due_at, pred.due_at):
                tallies["deadline_correct"] += 1
            else:
                kind = (
                    "invented_deadline"
                    if gold.due_at is None
                    else "missed_deadline"
                    if pred.due_at is None
                    else "wrong_deadline"
                )
                findings.append(Finding(kind, key, (gold.due_seq, pred.seq), pred.id))
        if gold.state == "unknown":
            tallies["state_excluded"] += 1
        else:
            tallies["state_judged"] += 1
            tallies["state_correct"] += gold.state == pred.state
            if kind := compare_state(gold, pred, paired.matched.get(gold.replacement)):
                findings.append(Finding(kind, key, (gold.state_seq, pred.state_seq), pred.id))
                if kind == "claim_misverified":
                    findings.append(
                        Finding("false_close", key, (gold.state_seq, pred.state_seq), pred.id)
                    )
        before = last_seen.get(key)
        if before is not None:
            if key not in previous:
                changes.append(Finding("commitment_returned", key, (pred.seq,), pred.id))
            if (
                before.state in TERMINAL
                and pred.state in OWED
                or (before.state == "claimed" and pred.state == "open")
            ):
                kind = (
                    "reopened"
                    if gold.state == pred.state
                    else "incorrect_reopening"
                    if gold.state != "unknown"
                    else "reopening_unjudged"
                )
                changes.append(Finding(kind, key, (pred.state_seq, gold.state_seq), pred.id))
            if not same_deadline(before.due_at, pred.due_at):
                kind = (
                    "deadline_change_unjudged"
                    if gold.deadline == "unknown"
                    else "deadline_corrected_or_updated"
                    if same_deadline(pred.due_at, gold.due_at)
                    else "incorrect_deadline_change"
                )
                changes.append(Finding(kind, key, (gold.due_seq, pred.seq), pred.id))
    return findings, changes


def _notice_id(frame: Frame, notice) -> str | None:
    if notice.kind is NoticeKind.CONDITION_DUE:
        return next((k.id for k in frame.card.conditionals
                     if notice.key.startswith(f"{frame.card.ticket_id}:{k.id}:condition_due:")),
                    None)  # fmt: skip
    return logical_id(frame.card, notice.commitment_id)


def _observe_notices(frame, paired, policy, first_alerts):
    matched = {p.id: gold for gold, p in paired.matched.items()}
    active = []
    for notice in notices(frame.card, frame.at, policy):
        if notice.kind is NoticeKind.UNREADABLE:
            continue
        ident = _notice_id(frame, notice)
        row = {
            "key": notice.key,
            "kind": notice.kind,
            "prediction": ident,
            "gold": matched.get(ident),
            "known_extra": ident in paired.extra,
            "at": frame.at,
            "seq": frame.seq,
            "due_at": notice.due_at,
            "source_seq": notice.seq
            or (paired.matched[matched[ident]].seq if ident in matched else None),
        }
        active.append(row)
        first_alerts.setdefault(notice.key, row)
    return active


def _in_window(alert: dict, window: AlertWindow) -> bool:
    return (
        alert["gold"] == window.gold and alert["kind"] == window.kind and window.covers(alert["at"])
    )


def _alert_order(alert: dict) -> tuple:
    return alert["at"], alert["seq"], alert["key"]


def alert_exposure(labels, frame, active, first_alerts):
    findings = []
    for window in labels.alerts:
        if not window.start <= frame.at < window.end:
            continue
        first = min(
            (a for a in first_alerts.values() if _in_window(a, window)),
            key=_alert_order,
            default=None,
        )
        expected = window.expected_at
        if expected is not None and expected <= frame.at and first is None:
            findings.append(Finding("expected_alert_wait", window.gold, (), window.kind))
        for alert in (a for a in active if _in_window(a, window)):
            emission = first_alerts[alert["key"]]
            if expected is None or (_in_window(emission, window) and first is not emission):
                findings.append(Finding("false_alert_eligibility", window.gold, (), alert["key"]))
            elif (
                first is emission and emission["at"] < expected - TOLERANCE and frame.at < expected
            ):
                findings.append(
                    Finding("premature_alert_eligibility", window.gold, (), alert["key"])
                )
    findings.extend(
        Finding("false_alert_eligibility", a["prediction"], (), a["key"])
        for a in active
        if a["known_extra"]
    )
    return findings


def score_alerts(labels: Labels, observed: list[dict]) -> dict:
    observed = sorted(observed, key=_alert_order)
    totals: Counter = Counter()
    assigned = set()
    rows = []
    for w in labels.alerts:
        eligible = [a for a in observed if _in_window(a, w)]
        totals["windows_judged"] += 1
        if w.expected_at is not None:
            totals["expected"] += 1
            first = eligible.pop(0) if eligible else None
            if first is None:
                totals["missed"] += 1
                rows.append(
                    {
                        "gold": w.gold,
                        "kind": w.kind,
                        "result": "missed",
                        "expected_at": w.expected_at,
                        "missed_seconds": seconds(w.expected_at, w.end),
                    }
                )
            else:
                assigned.add(first["key"])
                delay = seconds(w.expected_at, first["at"])
                margin = TOLERANCE.total_seconds()
                result = (
                    "premature" if delay < -margin else "delayed" if delay > margin else "on_time"
                )
                totals[result] += 1
                rows.append(
                    first
                    | {"result": result, "expected_at": w.expected_at, "offset_seconds": delay}
                )
        for first in eligible:
            assigned.add(first["key"])
            totals["false"] += 1
            rows.append(first | {"result": "false"})
    for first in observed:
        if first["key"] not in assigned:
            result = "false" if first["known_extra"] else "unjudged"
            totals[result] += 1
            rows.append(first | {"result": result})
    return {"counts": dict(totals), "occurrences": rows}


def evaluate(trace: Trace, labels: Labels, policy: AlertPolicy | None = None) -> dict:
    policy = policy or AlertPolicy()
    clocks = {at for w in labels.alerts for at in (w.start, w.end, w.expected_at) if at is not None}
    clocks.update(s.due_at for g in labels.obligations.values() for _, s in g.states
                  if s.due_at is not None)  # fmt: skip
    frames = tuple(trace.frames(clocks, policy))
    alert_frames = iter(frame for frame in frames if frame.alert_checkpoint)
    current_alert_frame = next(alert_frames, None)
    previous, ever, last_seen, first_alerts, at_event = {}, {}, {}, {}, {}
    unclear = set()
    changes, durations, alert_durations = [], Durations(), Durations()
    counts = Counts()
    exposure: Counter = Counter()
    tallies = defaultdict(Counter)
    for i, frame in enumerate(frames):
        card = predictions(frame.card)
        paired = match_identities(labels, card, frame.seq, previous)
        tally = tallies[frame.kind]
        tally["checkpoints"] += 1
        tally["excluded_gold"] += paired.excluded_gold
        tally["excluded_predictions"] += paired.excluded_predictions
        if frame.kind == "message":
            counts += paired.counts
        findings, events = _compare(labels, paired, card, frame, tally, previous, last_seen)
        following = frames[i + 1] if i + 1 < len(frames) else None
        unknown_state = {
            key
            for key in paired.matched
            if labels.obligations[key].at(frame.seq).state == "unknown"
        }
        unknown_deadline = {
            key
            for key in paired.matched
            if labels.obligations[key].at(frame.seq).deadline == "unknown"
        }
        durations.observe(frame, following, findings, unknown_state, unknown_deadline)
        if frame.kind in {"message", "event"}:
            at_event[frame.seq] = paired
        unclear.update(paired.unclear)
        changes.extend({"seq": frame.seq, "at": frame.at, "finding": event} for event in events)
        for key, pred in paired.matched.items():
            ever.setdefault(key, (frame, pred.id))
        if frame is current_alert_frame:
            current_alert_frame = next(alert_frames, None)
            active = _observe_notices(frame, paired, policy, first_alerts)
            alert_durations.observe(
                frame,
                current_alert_frame,
                alert_exposure(labels, frame, active, first_alerts),
                set(),
                set(),
            )
        previous = paired.matched
        last_seen.update(previous)
        exposure["message_checkpoints_held_after_failure"] += (
            frame.kind == "message" and frame.seq > len(trace.proposals)
        )
    creation = creation_delays(trace, labels, ever, unclear)
    return {
        "semantic_end": metrics(paired.counts),
        "semantic_messages": metrics(counts),
        "coverage": dict(exposure)
        | {"events": len(trace.ticket.events), "processed_events": len(trace.proposals)},
        "lifecycle": {
            "denominators": dict(tallies),
            "creation": creation,
            "changes": changes,
            "transitions": transitions(labels, at_event),
            **durations.report(),
        },
        "alerts": score_alerts(labels, list(first_alerts.values()))
        | {"exposure": alert_durations.report()},
    }


def creation_delays(trace: Trace, labels: Labels, first: dict, unclear: set[str]) -> dict:
    rows = []
    eligible = [g for g in labels.obligations.values() if g.certain and g.owner is not None]
    message_counts = [0]
    for event in trace.ticket.events:
        message_counts.append(message_counts[-1] + isinstance(event, MessagePosted))
    for g in eligible:
        if g.id not in first:
            rows.append(
                {
                    "gold": g.id,
                    "origin_seq": g.seq,
                    "result": "unjudged" if g.id in unclear else "never",
                }
            )
            continue
        frame, prediction = first[g.id]
        rows.append(
            {
                "gold": g.id,
                "origin_seq": g.seq,
                "first_seq": frame.seq,
                "prediction": prediction,
                "messages": message_counts[frame.seq] - message_counts[g.seq],
                "seconds": seconds(trace.ticket.events[g.seq - 1].at, frame.at),
                "result": "on_time" if frame.seq == g.seq else "delayed",
            }
        )
    totals = Counter(row["result"] for row in rows)
    return {"judged": len(eligible) - totals["unjudged"], "counts": dict(totals), "rows": rows}


def transitions(labels: Labels, at_event: dict[int, Pairing]) -> dict:
    rows = []
    for key, obligation in labels.obligations.items():
        for seq, expected in obligation.states[1:]:
            pair = at_event[seq]
            pred = pair.matched.get(key)
            for kind in ("state", "deadline"):
                source = expected.state_seq if kind == "state" else expected.due_seq
                if source != seq:
                    continue
                value = expected.state if kind == "state" else expected.deadline
                if (
                    value == "unknown"
                    or key in pair.unclear
                    and pred is None
                    or (not obligation.certain or obligation.owner is None)
                ):
                    result = "unjudged"
                elif pred is None:
                    result = "missing_identity"
                elif kind == "deadline":
                    result = (
                        "correct"
                        if same_deadline(pred.due_at, expected.due_at)
                        else "wrong_deadline"
                    )
                else:
                    result = compare_state(expected, pred, pair.matched.get(expected.replacement))
                    result = result or "correct"
                rows.append(
                    {
                        "gold": key,
                        "seq": seq,
                        "dimension": kind,
                        "expected": value,
                        "due_at": expected.due_at if kind == "deadline" else None,
                        "prediction": pred.id if pred else None,
                        "result": result,
                    }
                )
    counts = Counter(row["result"] for row in rows)
    return {"judged": len(rows) - counts["unjudged"], "counts": dict(counts), "rows": rows}
