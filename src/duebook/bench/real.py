"""Public support evaluation inputs and reporting for the existing benchmark."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
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
from .real_labels import compare, reviewed
from .real_sources import digest, draw, github, read, read_frozen, to_ticket, twitter, write
from .result import checkpoints
from .score import Counts

ROUTE = "OpenRouter / DeepInfra FP8 / " + DEFAULT_MODEL
SYNTHETIC = Path("results/round10/test5.json")


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
                "second_reviewer": "",
                "adjudication": "",
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
    (method,) = make_methods(
        ("event_memory",), DEFAULT_MODEL, ledger, WorkCalendar(ZoneInfo("UTC"))
    )
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


def measurement(counts):
    if counts.tp + counts.fp + counts.fn == 0:
        return "— (0 / 0 / 0)"

    def show(n, value):
        return f"{value:.3f}" if n else "—"

    return (
        f"{show(counts.tp + counts.fp, counts.precision)} / "
        f"{show(counts.tp + counts.fn, counts.recall)} / {counts.f1:.3f} "
        f"({counts.tp} / {counts.fp} / {counts.fn})"
    )


def baseline(record):
    if record["model"] != DEFAULT_MODEL or record.get("replayed_from"):
        raise ValueError("synthetic comparison requires live results for the same model")
    runs = [r for r in record["runs"] if r["method"] == "event_memory"]
    counts = sum((Counts(*r["score_end"]["commitments"]) for r in runs), Counts())
    usage = sum((Usage(**r["usage"]) for r in runs), Usage())
    seconds = sum(r["ingest_seconds"] for r in runs)
    return (
        f"Historical synthetic `{record['split']}` ({len(runs)} tickets) end-anchor "
        f"precision / recall / F1 = {measurement(counts)}. "
        f"The recorded run used {usage.calls:,} model calls, "
        f"${usage.usd(PRICES[DEFAULT_MODEL]):.4f} in reported or priced usage, and "
        f"{seconds:,.1f} summed model seconds. Language, lengths, cache and the certainty of "
        "labels differ between populations. Cost and latency totals describe each run. "
        "Only end-anchor detection uses the same scoring definition here."
    )


def report(bundle, labels, record, synthetic):
    cases = {case["id"]: case for case in bundle["cases"]}
    runs = {run["ticket"]: run for run in record["runs"]}
    if set(runs) != set(cases) or len(runs) != len(record["runs"]) or record["route"] != ROUTE:
        raise ValueError("run coverage or route differs from the frozen evaluation")
    sections = defaultdict(list)
    for raw in record["runs"]:
        run = run_of(raw)
        case = cases[run.ticket_id]
        (snap,) = run.snapshots
        if run.method != "event_memory" or (snap.checkpoint,) != checkpoints(to_ticket(case)):
            raise ValueError(f"unexpected snapshot for {run.ticket_id}")
        sections[case["source"], case["cohort"]].append((case, run, snap))
    lines = [
        "# Duebook real-world evaluation results",
        "",
        f"Route ({record['route']}).",
        "",
        f"Actual run wall time {record['wall_seconds']:.1f} s. "
        f"Ledger charge or estimate ${record['spend_usd']:.4f}. "
        f"Pre-run estimate ${record['estimate']['expected_usd']:.4f}, "
        f"stress projection ${record['estimate']['stress_usd']:.4f}.",
        "",
        f"Source frame counts `{json.dumps(bundle['frame_counts'], sort_keys=True)}`. "
        f"Seed {bundle['seed']}. One checkpoint per ticket at the last observed message.",
    ]
    for (source, cohort), rows in sorted(sections.items()):
        total, fields, kinds, examples = Counts(), Counter(), Counter(), defaultdict(list)
        costs, times, pods, usage = [], [], Counter(), Usage()
        failed = unread = messages = 0
        for case, run, snap in rows:
            score, metrics, errors = compare(case, labels[run.ticket_id], snap)
            total += score
            fields.update(metrics)
            failed += bool(run.failure)
            messages += len(case["messages"])
            unread += len(run.detail.get("unread", ()))
            spent = run.usage.usd(PRICES[DEFAULT_MODEL])
            costs.append(spent)
            times.append(run.ingest_seconds)
            usage += run.usage
            pods.update(run.detail.get("providers", {}))
            pods.update(run.detail.get("verifier", {}).get("providers", {}))
            errors.extend(("unread_message", (seq,)) for seq, _ in run.detail.get("unread", ()))
            if run.failure:
                errors.append(("processing_failure", ()))
            for kind, seqs in errors:
                kinds[kind] += 1
                if len(examples[kind]) < 3:
                    links = [
                        f"[message {s}]({case['messages'][s - 1]['url']})"
                        if case["messages"][s - 1].get("url")
                        else f"tweet ID `{case['messages'][s - 1]['id']}`"
                        for s in seqs
                    ]
                    examples[kind].append(", ".join(links) or f"[{case['id']}]({case['url']})")
        lines.extend(
            [
                "",
                f"## {source} / {cohort}",
                "",
                f"{len(rows)} threads, {messages} messages, {failed} failed threads, "
                f"{unread} unread messages. Partial cards are included in detection.",
                "",
                f"- Commitment anchor precision / recall / F1 = {measurement(total)}.",
                f"- Deadline accuracy (right / judged) = {fields['deadline_right']} / "
                f"{fields['deadline_judged']}. Zero judged means unavailable.",
                f"- Exact status accuracy (right / judged) = {fields['status_right']} / "
                f"{fields['status_judged']}. Matched unknown fulfillment "
                f"excluded = {fields['fulfillment_unknown']}.",
                f"- Ambiguous anchors excluded = {fields['excluded_anchors']}.",
                f"- Cost ${sum(costs):.4f}, {usage.calls} calls, {usage.prompt_tokens:,} "
                f"input / {usage.completion_tokens:,} output tokens. "
                f"Providers reporting success `{json.dumps(pods, sort_keys=True)}`.",
                f"- Model latency (summed / median / max per thread) = "
                f"{sum(times):.1f} / {statistics.median(times):.1f} / {max(times):.1f} s.",
                "",
                "| Category | Count | Source examples |",
                "|---|---:|---|",
            ]
        )
        lines.extend(
            f"| {kind.replace('_', ' ')} | {n} | {' · '.join(examples[kind])} |"
            for kind, n in sorted(kinds.items())
        )
        if not kinds:
            lines.append("| No categories recorded | 0 | — |")
    lines.extend(["", "## Comparison with synthetic results", "", baseline(synthetic), ""])
    return "\n".join(lines)


def main(argv: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m duebook.bench.experiment real")
    cmds = parser.add_subparsers(dest="cmd", required=True)
    prepare = cmds.add_parser("prepare")
    sources = prepare.add_mutually_exclusive_group(required=True)
    sources.add_argument("--github-frame", type=Path)
    sources.add_argument("--twcs-csv", type=Path)
    prepare.add_argument("--github-comments", type=Path)
    prepare.add_argument("--seed", type=int, default=7373)
    prepare.add_argument("--random", type=int, default=40)
    prepare.add_argument("--challenge", type=int, default=10)
    prepare.add_argument("--exclude", type=Path, help="JSON mapping case IDs to review reasons")
    for command in ("prepare", "estimate", "annotations", "run", "report"):
        sub = prepare if command == "prepare" else cmds.add_parser(command)
        sub.add_argument("bundle", type=Path)
        if command in ("annotations", "report", "run"):
            sub.add_argument("--out", required=True, type=Path)
        if command in ("run", "report"):
            sub.add_argument("--labels", required=True, type=Path)
        if command == "report":
            sub.add_argument("--run", required=True, type=Path)
            sub.add_argument("--synthetic", type=Path, default=SYNTHETIC)
        if command == "run":
            sub.add_argument("--approval", required=True, type=Path)
            sub.add_argument("--max-usd", required=True, type=float)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "prepare":
            if min(args.random, args.challenge) < 0 or args.random + args.challenge == 0:
                raise ValueError("cohort sizes must be nonnegative and select at least one case")
            if bool(args.github_frame) != bool(args.github_comments):
                raise ValueError("GitHub preparation requires a frame and comment snapshots")
            source = args.github_frame or args.twcs_csv
            source_sha = digest(source)
            cases = github(source, args.github_comments) if args.github_frame else twitter(source)
            exclusions = read(args.exclude) if args.exclude else None
            result = draw(cases, args.seed, args.random, args.challenge, exclusions)
            if digest(source) != source_sha:
                raise ValueError("source snapshot changed during the draw")
            write(args.bundle, result | {"schema": 1, "source_snapshot_sha256": source_sha})
            return
        bundle, bundle_sha = read_frozen(args.bundle)
        if bundle["schema"] != 1:
            raise ValueError("unsupported bundle schema")
        if args.cmd == "estimate":
            print(json.dumps(estimate(bundle), indent=2))
        elif args.cmd == "annotations":
            write(args.out, annotation_sheet(bundle, bundle_sha))
        elif args.cmd == "run":
            live(bundle, args.labels, bundle_sha, read(args.approval), args.max_usd, args.out)
        else:
            record = read(args.run)
            label_data, labels_sha = read_frozen(args.labels)
            if record["bundle_sha256"] != bundle_sha or record["labels_sha256"] != labels_sha:
                raise ValueError("results were produced against a different bundle or labels")
            labels = reviewed(bundle, label_data, bundle_sha)
            formatted = report(bundle, labels, record, read(args.synthetic))
            with args.out.open("x", encoding="utf-8") as stream:
                stream.write(formatted)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
