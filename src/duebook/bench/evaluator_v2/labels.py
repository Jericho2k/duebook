"""Evidence-backed gold lifecycles and human identity judgments."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from ...card import CommitmentState, Party
from ...events import MessagePosted, Side
from ...lifecycle import NoticeKind
from ..generate import SimTicket

STATES = {*CommitmentState, "pending", "lapsed", "unknown"}
JUDGMENTS = {
    "equivalent", "wrong_action", "wrong_object", "wrong_owner", "wrong_identity",
    "merged", "split", "unsupported", "overgeneralized", "uncertain",
}  # fmt: skip
ALERTS = set(NoticeKind) - {NoticeKind.UNREADABLE}


def require(ok, why: str) -> None:
    if not ok:
        raise ValueError(why)


def time_of(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    require(at.utcoffset() is not None, "timestamps require a UTC offset")
    return at.astimezone(UTC)


def unique(rows, field):
    out = {row[field]: row for row in rows}
    require(len(out) == len(rows), f"duplicate {field}")
    return out


@dataclass(frozen=True, slots=True)
class Expected:
    state: str
    deadline: str
    due_at: datetime | None
    state_seq: int
    due_seq: int
    replacement: str | None = None
    later_evidence: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Obligation:
    id: str
    owner: Party | None
    actor: str | None
    seq: int
    certain: bool
    conditional: bool
    states: tuple[tuple[int, Expected], ...]

    def at(self, seq: int) -> Expected | None:
        return next((state for changed, state in reversed(self.states) if changed <= seq), None)


@dataclass(frozen=True, slots=True)
class AlertWindow:
    gold: str
    kind: NoticeKind
    start: datetime
    end: datetime
    expected_at: datetime | None

    def covers(self, at: datetime) -> bool:
        return self.start <= at <= self.end


@dataclass(frozen=True, slots=True)
class Labels:
    obligations: dict[str, Obligation]
    judgments: dict[str, dict]
    alerts: tuple[AlertWindow, ...]
    proposal_reviews: dict[tuple[int, int], dict]
    source_sides: dict[int, Side]
    raw: dict

    @classmethod
    def read(cls, ticket: SimTicket, raw: dict) -> Labels:
        require(
            raw["ticket"] == ticket.ticket_id and raw["reviewer"].strip(),
            "labels need a ticket ID and human reviewer",
        )
        events = {event.seq: event for event in ticket.events}

        def evidence(rows, lower=1, upper=None):
            require(bool(rows), "evidence is required")
            for row in rows:
                seq, quote = row["seq"], row["quote"]
                event = events[seq]
                require(
                    isinstance(event, MessagePosted)
                    and lower <= seq <= (upper or len(events))
                    and quote.strip()
                    and quote in event.text,
                    f"invalid evidence at {seq}",
                )

        def deadline(row, lower, upper):
            kind, at = row["kind"], row.get("at")
            require(
                kind in {"exact", "absent", "unknown"} and (kind == "exact") == (at is not None),
                "deadline must be exact, absent or unknown",
            )
            if kind == "exact" or row.get("evidence"):
                evidence(row.get("evidence"), lower, upper)
            return kind, time_of(at) if at is not None else None

        gold = unique(raw["commitments"], "id")
        obligations = {}
        actors = {event.author.id for event in events.values() if isinstance(event, MessagePosted)}
        for ident, row in gold.items():
            require(
                ident.strip() and row["action"].strip() and row["object"].strip(),
                "commitment needs an ID, action and object",
            )
            seq = row["origin"][0]["seq"]
            evidence(row["origin"], seq, seq)
            if row.get("restatements"):
                evidence(row["restatements"], seq + 1)
            actor = row.get("owner_actor")
            require(actor is None or actor in actors, "owner actor is absent from the transcript")
            owner = Party(row["owner"]) if row["owner"] is not None else None
            require(row["identity"] in {"certain", "uncertain"}, "invalid identity certainty")
            state = row["initial_state"]
            require(state in {"open", "pending", "unknown"}, "invalid initial state")
            require(
                state != "pending" or bool(row.get("condition", "").strip()),
                "pending offer needs its condition",
            )
            kind, at = deadline(row["deadline"], seq, seq)
            now = Expected(state, kind, at, seq, seq)
            states = [(seq, now)]
            for change in row["transitions"]:
                step = change["seq"]
                require(step > states[-1][0], "transitions must follow creation in sequence order")
                evidence(change["evidence"], seq, step)
                require(
                    any(e["seq"] == step for e in change["evidence"]),
                    "transition needs evidence from its event",
                )
                require("state" in change or "deadline" in change, "empty transition")
                if "state" in change:
                    state = change["state"]
                    replacement = change.get("replacement")
                    require(state in STATES and state != now.state, "invalid state transition")
                    require(
                        state != "pending" and (state != "lapsed" or now.state == "pending"),
                        "only a pending condition can lapse",
                    )
                    if state == "superseded":
                        require(
                            replacement in gold
                            and replacement != ident
                            and gold[replacement]["origin"][0]["seq"] == step,
                            "replacement must be a distinct obligation originating at this event",
                        )
                    else:
                        require(replacement is None, "only supersession names a replacement")
                    later = change.get("later_evidence", [])
                    if later:
                        evidence(later, step + 1)
                    now = replace(
                        now,
                        state=state,
                        state_seq=step,
                        replacement=replacement,
                        later_evidence=tuple(e["seq"] for e in later),
                    )
                if "deadline" in change:
                    kind, at = deadline(change["deadline"], seq, step)
                    now = replace(now, deadline=kind, due_at=at, due_seq=step)
                states.append((step, now))
            obligations[ident] = Obligation(
                ident,
                owner,
                actor,
                seq,
                row["identity"] == "certain",
                bool(row.get("condition")),
                tuple(states),
            )

        reviews = unique(raw["identity_reviews"], "prediction")
        for row in reviews.values():
            ids, judgment = row["gold"], row["judgment"]
            require(
                judgment in JUDGMENTS and len(set(ids)) == len(ids) and set(ids) <= gold.keys(),
                "invalid identity judgment or gold ID",
            )
            require(row["note"].strip(), "identity review needs a reason")
            if row["evidence"]:
                evidence(row["evidence"])
            if judgment == "equivalent":
                require(len(ids) == 1, "equivalence must name one specific gold identity")
                evidence(row["evidence"])
            if judgment == "merged":
                require(len(ids) >= 2, "a merge must name the combined gold identities")

        windows = []
        for row in raw.get("alert_windows", []):
            start, end = time_of(row["from"]), time_of(row["through"])
            expected = time_of(row["expected_at"]) if row["expected_at"] is not None else None
            gold_id, kind = row["gold"], NoticeKind(row["kind"])
            require(
                gold_id in obligations
                and obligations[gold_id].certain
                and obligations[gold_id].owner is not None
                and kind in ALERTS
                and ticket.events[0].at <= start <= end <= ticket.end
                and (expected is None or start <= expected <= end),
                "invalid alert window or expectation",
            )
            evidence(row["evidence"])
            if expected is not None:
                require(
                    expected >= events[obligations[gold_id].seq].at,
                    "alert expectation precedes the obligation",
                )
            window = AlertWindow(gold_id, kind, start, end, expected)
            require(
                not any(
                    w.gold == gold_id and w.kind == kind and w.start <= end and start <= w.end
                    for w in windows
                ),
                "alert windows overlap",
            )
            windows.append(window)

        decisions = {}
        for row in raw.get("proposal_reviews", []):
            key = row["seq"], row["index"]
            require(
                key not in decisions and row["decision"] in {"admit", "reject", "uncertain"},
                "invalid or duplicate proposal review",
            )
            require(
                row.get("expected_target") is None or row["expected_target"] in gold,
                "unknown expected proposal target",
            )
            require(
                not row.get("suggestion") or row["decision"] == "reject",
                "a required suggestion belongs to a rejected change",
            )
            evidence(row["evidence"], upper=row["seq"])
            decisions[key] = row

        source_sides = {}
        for seq, row in unique(raw.get("source_roles", []), "seq").items():
            evidence(row["evidence"], seq, seq)
            source_sides[seq] = Side(row["side"])
        return cls(obligations, reviews, tuple(windows), decisions, source_sides, raw)
