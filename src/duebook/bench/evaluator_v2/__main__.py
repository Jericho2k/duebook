"""Opt-in V2 scoring from local artifacts. No model client is constructed."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from zoneinfo import ZoneInfo

from ...reducer import RULES, Guards
from ...store.codec import decode_event, encode
from ...temporal import WorkCalendar
from ..experiment import compact, run_of, to_json
from ..generate import SPLITS, Gold, SimTicket
from ..rewrite import with_rewritten_text
from ..score import Counts, score_ticket
from .attribution import attribute, describe
from .labels import Labels, require, time_of, unique
from .replay import Trace
from .score import evaluate, metrics


def read(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def load_tickets(args, record):
    if args.events:
        raw, fingerprint = read(args.events)
        return {
            ident: SimTicket(
                ident,
                tuple(decode_event(e) for e in row["events"]),
                (),
                Gold(),
                time_of(row["end"]),
            )
            for ident, row in unique(raw["tickets"], "id").items()
        }, fingerprint
    require(record.get("split") == args.split, "record does not match selected split")
    split = SPLITS[args.split]
    tickets = split.tickets()
    if args.rewrites:
        raw, _ = read(args.rewrites)
        require(raw["split"] == split.name and raw["seed"] == split.seed, "wrong frozen rewrites")
        tickets = [with_rewritten_text(t, raw["tickets"][t.ticket_id]["texts"]) for t in tickets]
    else:
        require(bool(record.get("template")), "select the frozen transcript with --rewrites")
    serialized = [(t.ticket_id, encode(t.events), t.end.isoformat()) for t in tickets]
    fingerprint = hashlib.sha256(json.dumps(serialized, sort_keys=True).encode()).hexdigest()
    return {t.ticket_id: t for t in tickets}, fingerprint


def archived_v1(raw, ticket, run, has_native_gold: bool) -> dict | None:
    if "score_end" in raw and "score_handoffs" in raw:
        return {"end": raw["score_end"], "handoffs": raw["score_handoffs"], "basis": "archived"}
    if has_native_gold:
        score = score_ticket(ticket, run.snapshots)
        return {"end": score.end.as_dict(), "handoffs": score.handoffs.as_dict(), "basis": "v1"}
    return None


def summary(tickets: list[dict]) -> dict:
    v1 = [t["score_v1"]["end"]["commitments"] for t in tickets if t["score_v1"] is not None]
    v2 = {"tickets": len(tickets)}
    for key in ("semantic_end", "semantic_messages"):
        rows = [t["score_v2"][key] for t in tickets]
        v2[key] = metrics(sum((Counts(r["tp"], r["fp"], r["fn"]) for r in rows), Counts()))
    creation, transitions, alerts = Counter(), Counter(), Counter()
    for ticket in tickets:
        v2_score = ticket["score_v2"]
        creation.update(v2_score["lifecycle"]["creation"]["counts"])
        transitions.update(v2_score["lifecycle"]["transitions"]["counts"])
        alerts.update(v2_score["alerts"]["counts"])
    v2.update(creation=creation, transitions=transitions, alerts=alerts)
    v1_counts = sum((Counts(*row) for row in v1), Counts())
    anchors = asdict(v1_counts) | {
        "precision": v1_counts.precision,
        "recall": v1_counts.recall,
        "f1": v1_counts.f1,
    }
    return {
        "score_v1": {"tickets": len(v1), "anchor_end": anchors if v1 else None},
        "score_v2": v2,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--events", type=Path, help="frozen source events (stored event codec)")
    src.add_argument("--split", choices=sorted(SPLITS), help="use an existing synthetic split")
    parser.add_argument("--rewrites", type=Path, help="frozen rewritten transcript for --split")
    parser.add_argument("--record", type=Path, required=True, help="saved benchmark JSON")
    parser.add_argument("--method", default="event_memory", help="recorded method with proposals")
    parser.add_argument("--ticket", action="append", help="select recorded ticket IDs (repeatable)")
    parser.add_argument("--calendar", default="Europe/Moscow", help="replay timezone")
    parser.add_argument("--rules", type=int, choices=range(1, RULES + 1), default=RULES)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gold", type=Path)
    mode.add_argument("--review-packet", action="store_true", help="export stable IDs for review")
    parser.add_argument("--reviews", type=Path, help="human adjudications bound to gold and record")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(not (args.events and args.rewrites), "--rewrites requires --split")
        require(bool(args.gold) == bool(args.reviews), "scoring needs both --gold and --reviews")
        record, record_sha = read(args.record)
        tickets, source_sha = load_tickets(args, record)
        require(record["runs"], "record contains no runs")
        rows = [
            r
            for r in record["runs"]
            if r["method"] == args.method and (not args.ticket or r["ticket"] in args.ticket)
        ]
        ids = set(unique(rows, "ticket"))
        require(
            ids and ids <= tickets.keys() and (not args.ticket or ids == set(args.ticket)),
            "missing source events or selected method/ticket",
        )
        output = {
            "version": 2,
            "method": args.method,
            "record_sha256": record_sha,
            "source_sha256": source_sha,
            "rules": args.rules,
            "calendar": args.calendar,
            "tickets": [],
        }
        if args.gold:
            gold, gold_sha = read(args.gold)
            reviews, reviews_sha = read(args.reviews)
            require(
                gold["version"] == reviews["version"] == 2
                and gold["source_sha256"] == source_sha
                and reviews["gold_sha256"] == gold_sha
                and reviews["record_sha256"] == record_sha
                and reviews["method"] == args.method
                and reviews["rules"] == args.rules
                and reviews["calendar"] == args.calendar,
                "frozen labels or review binding differs",
            )
            before, after = unique(gold["tickets"], "ticket"), unique(reviews["tickets"], "ticket")
            require(ids <= before.keys() and ids <= after.keys(), "missing human labels or reviews")
            output.update(gold_sha256=gold_sha, reviews_sha256=reviews_sha)
        cal, guards = WorkCalendar(ZoneInfo(args.calendar)), Guards(rules=args.rules)
        for raw in rows:
            run = run_of(raw)
            ticket = tickets[run.ticket_id]
            trace = Trace.read(ticket, run, guards, cal)
            decisions = tuple(trace.decisions())
            row = {"ticket": run.ticket_id}
            if args.review_packet:
                row.update(describe(trace, decisions))
            else:
                ref, adjudicated = before[run.ticket_id], after[run.ticket_id]
                require(adjudicated["reviewer"].strip(), "human review needs a reviewer")
                labels = Labels.read(
                    ticket,
                    ref
                    | {
                        "identity_reviews": adjudicated["identity_reviews"],
                        "proposal_reviews": adjudicated.get("proposal_reviews", []),
                    },
                )
                score = evaluate(trace, labels)
                score["attribution"] = attribute(
                    trace,
                    labels,
                    decisions,
                    score["lifecycle"],
                )
                row.update(
                    score_v1=archived_v1(raw, ticket, run, bool(args.split)),
                    score_v2=score,
                    evidence={"gold": ref, "reviews": adjudicated},
                )
            output["tickets"].append(row)
        if args.gold:
            output["summary"] = summary(output["tickets"])
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(compact(to_json(output)))
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
