"""Public support evaluation inputs and reporting for the existing benchmark."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from zoneinfo import ZoneInfo

from ..extract.client import DEFAULT_MODEL, openrouter_fields, settings_for
from ..extract.cost import PRICES, Usage
from ..temporal import WorkCalendar
from .experiment import make_methods, run_all, run_of, saved
from .meter import Ledger
from .methods import Recorded
from .real_labels import compare, index_labels, reviewed, validate
from .real_mendeley import prepare as prepare_mendeley
from .real_sources import read, read_frozen, source_messages, to_ticket, write
from .result import checkpoints
from .score import Counts

ROUTE = "OpenRouter / DeepInfra FP8 / " + DEFAULT_MODEL
CALENDAR = WorkCalendar(ZoneInfo("Asia/Amman"), frozenset({6, 0, 1, 2, 3}))


def estimate(bundle):
    # Round 10 test5 used about 2,460 input and 106 output tokens per call, across all calls.
    # Charge for two occurrences of the current source body's bytes, plus fixed/card context.
    pricing = PRICES[DEFAULT_MODEL]
    groups = defaultdict(Counter)
    for case in bundle["cases"]:
        group = groups[case["cohort"]]
        group["tickets"] += 1
        for message in case["messages"]:
            group["messages"] += 1
            if not message["text"].strip():
                continue
            group["readable_messages"] += 1
            body = len(message["text"].encode("utf-8"))
            for key, calls, context, output in (
                ("expected_usd", 1.1, 3_000, 150),
                ("stress_usd", 2.0, 6_000, 1_500),
            ):
                group[key] += (
                    calls
                    * ((context + 2 * body) * pricing.input_per_m + output * pricing.output_per_m)
                    / 1_000_000
                )
    return {
        "route": ROUTE,
        "pricing_per_million": asdict(pricing),
        "cohorts": dict(groups),
        "expected_usd": sum(g["expected_usd"] for g in groups.values()),
        "stress_usd": sum(g["stress_usd"] for g in groups.values()),
    }


def annotation_sheet(bundle, bundle_sha):
    return {
        "bundle_sha256": bundle_sha,
        "tickets": [
            {
                "ticket_id": case["id"],
                "review": "pending",
                "annotator": "",
                "commitments": [],
                "ambiguous": [],
            }
            for case in bundle["cases"]
        ],
    }


def preflight(bundle_sha, labels_sha, approval, projection, max_usd):
    if (
        approval.get("bundle_sha256") != bundle_sha
        or approval.get("labels_sha256") != labels_sha
        or approval.get("route") != ROUTE
        or approval.get("external_inference") is not True
        or not approval.get("approved_by", "").strip()
        or not approval.get("rights_and_provider_basis", "").strip()
    ):
        raise ValueError("live run requires documented source and provider clearance for this draw")
    if approval.get("estimate") != projection:
        raise ValueError("live run requires the current, reviewed cost estimate")
    if not math.isfinite(max_usd) or not 0 < max_usd <= approval["max_usd"]:
        raise ValueError("budget must be positive and within approval")


def reference_method(ledger, *, recorded=None):
    (method,) = make_methods(("event_memory",), DEFAULT_MODEL, ledger, CALENDAR, recorded=recorded)
    method.capture_trace = True
    return method


def offline(bundle, labels, bundle_sha, out):
    by_ticket = index_labels(bundle, labels, bundle_sha)
    tickets = []
    for case in bundle["cases"]:
        validate(case, by_ticket[case["id"]])
        tickets.append(to_ticket(case))
    ledger = Ledger(0, PRICES[DEFAULT_MODEL])
    method = reference_method(
        ledger, recorded={ticket.ticket_id: Recorded() for ticket in tickets}
    )
    runs = run_all([method], tickets, 1)
    if ledger.usd() or any(
        run.failure or run.usage.calls or len(run.detail["event_trace"]) != len(ticket.events)
        for ticket, run in zip(tickets, runs, strict=True)
    ):
        raise ValueError("offline fold did not complete with empty proposals")
    anchors = {}
    for case, run in zip(bundle["cases"], runs, strict=True):
        (scored, _, _) = compare(case, by_ticket[run.ticket_id], run.snapshots[-1])
        anchors[run.ticket_id] = asdict(scored)
    write(out, {"bundle_sha256": bundle_sha, "spend_usd": 0, "runs": [saved(r) for r in runs],
                "empty_proposal_anchor_counts": anchors})


def live(bundle, labels_path, bundle_sha, approval, max_usd, out):
    projection = estimate(bundle)
    labels, labels_sha = read_frozen(labels_path)
    preflight(bundle_sha, labels_sha, approval, projection, max_usd)
    reviewed(bundle, labels, bundle_sha)
    if not os.environ.get("DUEBOOK_OPENROUTER_API_KEY"):
        raise ValueError("live evaluation requires DUEBOOK_OPENROUTER_API_KEY")
    journal = out.with_suffix(".journal.jsonl")
    if out.exists():
        raise ValueError(f"result already exists at {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    # An interrupted run keeps its partial journal. Reusing its name would reset the spend ledger.
    journal.touch(exist_ok=False, mode=0o600)
    ledger = Ledger(max_usd, PRICES[DEFAULT_MODEL])
    method = reference_method(ledger)
    for field in ("model", "escalation", "verifier"):
        client = getattr(method, field)
        provider = openrouter_fields(settings_for(client.model, client.reasoning))["provider"]
        setattr(
            method,
            field,
            replace(
                client,
                extra=client.extra
                | {
                    "provider": provider | {"zdr": True, "data_collection": "deny"},
                },
            ),
        )
    started = time.perf_counter()
    runs = run_all([method], [to_ticket(c) for c in bundle["cases"]], 1, journal=journal)
    write(
        out,
        {
            "bundle_sha256": bundle_sha,
            "labels_sha256": labels_sha,
            "route": ROUTE,
            "estimate": projection,
            "spend_usd": ledger.usd(),
            "max_usd": max_usd,
            "wall_seconds": time.perf_counter() - started,
            "runs": [saved(r) for r in runs],
        },
    )


def score_row(ticket, score, fields):
    precision = f"{score.precision:.3f}" if score.tp + score.fp else "—"
    recall = f"{score.recall:.3f}" if score.tp + score.fn else "—"
    f1 = f"{score.f1:.3f}" if score.tp + score.fp + score.fn else "—"
    return (
        f"| {ticket} | {score.tp} / {score.fp} / {score.fn} | "
        f"{precision} / {recall} / {f1} | "
        f"{fields['deadline_right']} / {fields['deadline_judged']} | "
        f"{fields['status_right']} / {fields['status_judged']} |"
    )


def report(bundle, labels, record):
    cases = {case["id"]: case for case in bundle["cases"]}
    runs = {run["ticket"]: run for run in record["runs"]}
    if set(runs) != set(cases) or len(runs) != len(record["runs"]) or record["route"] != ROUTE:
        raise ValueError("run coverage or route differs from the frozen evaluation")
    total, fields, usage = Counts(), Counter(), Usage()
    kinds, examples = Counter(), defaultdict(list)
    seconds = failed = unread = 0
    rows = []
    for key, raw in runs.items():
        case, run = cases[key], run_of(raw)
        ticket = to_ticket(case)
        if run.method != "event_memory" or tuple(
            snap.checkpoint for snap in run.snapshots
        ) != checkpoints(ticket):
            raise ValueError(f"unexpected snapshots for {key}")
        score, metrics, errors = compare(case, labels[key], run.snapshots[-1])
        rows.append(score_row(key, score, metrics))
        total += score
        fields.update(metrics)
        usage += run.usage
        seconds += run.ingest_seconds
        failed += bool(run.failure)
        unread_messages = run.detail.get("unread", ())
        unread += len(unread_messages)
        errors.extend(("unread_message", (seq,)) for seq, _ in unread_messages)
        if run.failure:
            errors.append(("processing_failure", ()))
        messages = source_messages(case, ticket)
        for kind, seqs in errors:
            kinds[kind] += 1
            if len(examples[kind]) < 3:
                anchors = ", ".join(f"`{messages[s]['id']}`" for s in seqs if s in messages)
                examples[kind].append(anchors or key)
    heading = "V1 scores"
    if any(label["review"] == "pending" for label in labels.values()):
        heading = f"Draft V1 scores (machine-drafted, not human-reviewed, n={len(cases)})"
    return "\n".join([
        "# Duebook Mendeley evaluation",
        "",
        f"Route ({record['route']}).",
        f"{len(cases)} tickets, {sum(len(c['messages']) for c in cases.values())} comments, "
        f"{failed} failed tickets, {unread} unread comments.",
        f"Ledger spend ${record['spend_usd']:.4f}, {usage.calls} calls, "
        f"{record['wall_seconds']:.1f} s wall time and {seconds:.1f} s summed model time.",
        "",
        f"## {heading}",
        "",
        "| Ticket | Matched / extra / missed | Precision / recall / F1 | "
        "Deadline right / judged | Status right / judged |",
        "| --- | ---: | ---: | ---: | ---: |",
        *rows,
        score_row("Total", total, fields),
        "",
        "| Category | Count | Source comment examples |",
        "| --- | ---: | --- |",
        *(f"| {kind.replace('_', ' ')} | {count} | {' · '.join(examples[kind])} |"
          for kind, count in sorted(kinds.items())),
        "",
    ])


def main(argv: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m duebook.bench.experiment real")
    cmds = parser.add_subparsers(dest="cmd", required=True)
    mendeley = cmds.add_parser("prepare-mendeley")
    for field in ("source", "manifest", "selection", "bundle", "review"):
        mendeley.add_argument(field, type=Path)
    for command in ("estimate", "annotations", "offline", "run", "report"):
        sub = cmds.add_parser(command)
        sub.add_argument("bundle", type=Path)
        if command != "estimate":
            sub.add_argument("--out", required=True, type=Path)
        if command in ("offline", "run", "report"):
            sub.add_argument("--labels", required=True, type=Path)
        if command == "report":
            sub.add_argument("--run", required=True, type=Path)
        if command == "run":
            sub.add_argument("--approval", required=True, type=Path)
            sub.add_argument("--max-usd", required=True, type=float)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "prepare-mendeley":
            prepare_mendeley(
                args.source, args.manifest, read(args.selection), args.bundle, args.review
            )
            return
        bundle, bundle_sha = read_frozen(args.bundle)
        if bundle["schema"] != 1 or not bundle["cases"] or any(
            case["source"] != "mendeley" for case in bundle["cases"]
        ):
            raise ValueError("a frozen Mendeley bundle is required")
        if args.cmd == "estimate":
            print(json.dumps(estimate(bundle), indent=2))
        elif args.cmd == "annotations":
            write(args.out, annotation_sheet(bundle, bundle_sha))
        elif args.cmd == "offline":
            offline(bundle, read(args.labels), bundle_sha, args.out)
        elif args.cmd == "run":
            live(bundle, args.labels, bundle_sha, read(args.approval), args.max_usd, args.out)
        else:
            record = read(args.run)
            label_data, labels_sha = read_frozen(args.labels)
            if record["bundle_sha256"] != bundle_sha or record["labels_sha256"] != labels_sha:
                raise ValueError("results were produced against a different bundle or labels")
            formatted = report(bundle, reviewed(bundle, label_data, bundle_sha), record)
            with args.out.open("x", encoding="utf-8") as stream:
                stream.write(formatted)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
