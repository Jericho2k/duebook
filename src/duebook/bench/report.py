"""Tables and figures for ``docs/results.md``, computed from the saved results.

    python -m duebook.bench.report results/test.json --long results/long.json
    python -m duebook.bench.report results/round2/test2.json --dev results/round2/dev2-*.json

Nothing here calls a model: every number is recomputed from the JSON the
experiment wrote, so the document can be checked against the raw record.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .deadlines import classify
from .experiment import run_of, table
from .figures import line_chart
from .generate import SPLITS, CloseCase

ACCURACY = [
    "commitment_precision", "commitment_recall", "commitment_f1", "deadline_accuracy",
    "status_accuracy", "breach_alert_precision", "breach_alert_recall",
    "false_alerts_per_ticket",
]  # fmt: skip
CONTEXT = [
    "freshness_error", "freshness_error_at_handoffs", "fact_change_accuracy",
    "dispute_precision", "dispute_recall", "false_disputes_per_ticket",
    "failed_step_precision", "failed_step_recall", "unanswered_checkpoints",
]  # fmt: skip
COST = [
    "usd_per_ticket", "usd_per_message", "prompt_tokens_per_ticket",
    "completion_tokens_per_ticket", "calls_per_ticket", "ingest_seconds_per_message",
    "answer_seconds_per_checkpoint", "model_seconds_per_ticket",
]  # fmt: skip
CLOSES = [
    "false_closes_per_ticket", "wrong_target_closes_per_ticket", "missed_close_rate",
    "close_precision", "suggestions_per_ticket", "false_closes_any_checkpoint",
]  # fmt: skip
KEY = ["commitment_f1", "deadline_accuracy", "breach_alert_recall", "false_alerts_per_ticket",
       "freshness_error", "fact_change_accuracy", "dispute_recall", "failed_step_recall",
       "usd_per_ticket"]  # fmt: skip


def differences(summary: dict[str, Any], keys: list[str]) -> str:
    """Each method minus the reference, with the paired 95% interval."""
    ref = summary["reference"]
    methods = [m for m in summary["methods"] if m != ref]
    lines = [f"| metric (method − {ref}) | " + " | ".join(methods) + " |",
             "|---|" + "---|" * len(methods)]  # fmt: skip
    for k in keys:
        cells = []
        for m in methods:
            row = summary["methods"][m][k]
            lo, hi = row["diff_ci95"]
            mark = " *" if lo > 0 or hi < 0 else ""
            cells.append(f"{row['diff_vs_reference']:+.3f} [{lo:+.3f}, {hi:+.3f}]{mark}")
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def close_counts(summary: dict[str, Any]) -> str:
    """Closes at the end of each ticket, as counts: what every rate above rests on."""
    lines = ["| method | tickets | closes right | **false** | wrong target | missed | "
             "suggestions (right / other) |", "|---|---|---|---|---|---|---|"]  # fmt: skip
    for m, rows in summary["methods"].items():
        c = rows["counts_end"]
        judged = c["closes_right"] + c["closes_wrong_target"] + c["closes_missed"]
        lines.append(
            f"| {m} | {rows['tickets']} | {c['closes_right']} | {c['closes_false']} | "
            f"{c['closes_wrong_target']} | {c['closes_missed']} of {judged} | "
            f"{c['suggestions_right']} / {c['suggestions_wrong']} |"
        )
    return "\n".join(lines)


def guard_effects(result: dict[str, Any]) -> str:
    """What the guards refused on a run, by reason, judged against the labels."""
    effects = result.get("guard_effects") or {}
    verdicts = ["false_close_prevented", "wrong_target_prevented", "true_close_blocked",
                "not_a_planted_promise"]  # fmt: skip
    lines = ["| reason | " + " | ".join(v.replace("_", " ") for v in verdicts) + " |",
             "|---|" + "---|" * len(verdicts)]  # fmt: skip
    for reason, counts in sorted((effects.get("by_reason") or {}).items()):
        lines.append(f"| {reason} | " + " | ".join(str(counts.get(v, 0)) for v in verdicts) + " |")
    total = effects.get("verdicts") or {}
    lines.append("| **all** | " + " | ".join(str(total.get(v, 0)) for v in verdicts) + " |")
    return "\n".join(lines)


def replayed(result: dict[str, Any]) -> dict[str, Any]:
    """The event memory's recorded proposals folded again through today's reducer, with
    the guards and without: end-of-ticket close counts and breach alerts, summed."""
    from ..reducer import UNGUARDED, Guards
    from ..store.codec import decode_proposals
    from .experiment import ROUND2, rewritten
    from .meter import Ledger
    from .result import checkpoints
    from .run import refold, snapshots
    from .score import Score, score_ticket

    split = SPLITS[result["split"]]
    tickets, _ = rewritten(split, result["writer"], Ledger(cap_usd=0), ROUND2 / "tickets")
    by_id = {t.ticket_id: t for t in tickets}
    out = {}
    for name, guards in (("guards on", Guards()), ("guards off", UNGUARDED)):
        total = Score()
        for run in result["runs"]:
            if run["method"] != "event_memory":
                continue
            t = by_id[run["ticket"]]
            proposals = [decode_proposals(p) for p in run["detail"]["proposals"]]
            folded = refold(t, proposals, guards)
            assert len(checkpoints(t)) == len(snapshots(t, folded))
            total += score_ticket(t, snapshots(t, folded)).end
        out[name] = total
    return out


def ablation(results: list[tuple[str, dict[str, Any]]]) -> str:
    """Guards on and off, on the same recorded proposals of every run given."""
    lines = ["| run | guards | false closes | wrong target | missed closes | suggestions | "
             "breach alerts (right, false, missed) | status accuracy |",
             "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for label, result in results:
        for name, s in replayed(result).items():
            a = s.alerts
            lines.append(
                f"| {label} | {name} | {s.closes_false} | {s.closes_wrong_target} | "
                f"{s.closes_missed} of {s.closes_judged} | "
                f"{s.suggestions_right + s.suggestions_wrong} | {a.tp}, {a.fp}, {a.fn} | "
                f"{s.status_accuracy:.3f} |"
            )
    return "\n".join(lines)


def deadline_kinds(summary: dict[str, Any]) -> str:
    kinds = ["right", "missing", "invented", "wrong_day", "wrong_time"]
    lines = ["| method | " + " | ".join(kinds) + " |", "|---|" + "---|" * len(kinds)]
    for m, rows in summary["methods"].items():
        k = rows["deadline_errors"]["kinds"]
        lines.append(f"| {m} | " + " | ".join(str(k[x]) for x in kinds) + " |")
    return "\n".join(lines)


def conventions(result: dict[str, Any]) -> str:
    """Deadline errors split into arithmetic and the "end of week" convention."""
    tickets = SPLITS[result["split"]].tickets()
    kinds = ["right", "convention", "arithmetic", "missing", "invented"]
    lines = ["| method | " + " | ".join(kinds) + " | strict | either convention |",
             "|---|" + "---|" * (len(kinds) + 2)]  # fmt: skip
    for m in result["summary"]["methods"]:
        runs = [run_of(r) for r in result["runs"] if r["method"] == m]
        if not runs:  # a replay, not a run: its deadlines are the run's
            continue
        k = classify(tickets[: len(runs)], runs)
        n = sum(k.values()) or 1
        strict, either = k["right"] / n, (k["right"] + k["convention"]) / n
        counts = " | ".join(str(k[x]) for x in kinds)
        lines.append(f"| {m} | {counts} | {strict:.3f} | {either:.3f} |")
    return "\n".join(lines)


def prompt_growth(result: dict[str, Any]) -> tuple[str, str]:
    """The event memory's prompt per message on long tickets: measured, and bounded.

    Measured is the provider's prompt-token count. The bounded curve is the
    same card rendered with the default bound, converted to tokens with the
    ticket's own measured tokens-per-character ratio.
    """
    series, rows = (
        [],
        [
            "| ticket | messages | first prompt | last prompt | growth | "
            "last, bounded | saved at the end |",
            "|---|---|---|---|---|---|---|",
        ],
    )
    for run in result["runs"]:
        if not run["method"].startswith("event_memory"):
            continue
        tokens = run["detail"]["prompt_tokens"]
        chars = run["detail"]["prompt_chars"]
        if not tokens or len(chars) != len(tokens):
            continue
        ratio = sum(t for _, t in tokens) / sum(c for _, c, _ in chars)
        measured = [(n, t) for n, (_, t) in enumerate(tokens, 1)]
        bounded = [(n, b * ratio) for n, (_, _, b) in enumerate(chars, 1)]
        series += [(f"{run['ticket']} measured", measured),
                   (f"{run['ticket']} bounded (estimated)", bounded)]  # fmt: skip
        first, last, last_b = measured[0][1], measured[-1][1], bounded[-1][1]
        growth, saved = last / first - 1, 1 - last_b / last
        rows.append(f"| {run['ticket']} | {len(tokens)} | {first} | {last} | "
                    f"{growth:+.0%} | {last_b:.0f} | {saved:.1%} |")  # fmt: skip
    title = "Event memory: prompt tokens per message on 200+ message tickets"
    svg = line_chart(series, title=title, x_label="message number", y_label="prompt tokens")
    return svg, "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m duebook.bench.report")
    parser.add_argument("result", type=Path)
    parser.add_argument("--long", type=Path)
    parser.add_argument("--figure", type=Path, default=Path("docs/prompt-growth.svg"))
    parser.add_argument("--dev", type=Path, nargs="*", default=[],
                        help="dev runs whose proposals are refolded with and without guards")
    args = parser.parse_args()
    result = json.loads(args.result.read_text())
    s = result["summary"]
    print(f"## {result['split']}: {s['tickets']} tickets\n")
    sections = (("Accuracy (end of ticket)", ACCURACY),
                ("Context (every handoff and the end)", CONTEXT), ("Cost", COST))  # fmt: skip
    if "guard_effects" in result:
        cases = [case.value for case in CloseCase if case.value in s["methods"][s["reference"]]]
        sections = (("Closes (end of ticket)", CLOSES + cases), *sections)
    for title, keys in sections:
        print(f"### {title}\n\n{table(s, keys)}\n")
    if "guard_effects" in result:
        print(f"### Closes, counted\n\n{close_counts(s)}\n")
        print(f"### What the guards refused\n\n{guard_effects(result)}\n")
        runs = [(p.stem, json.loads(p.read_text())) for p in args.dev]
        print(f"### Guards on and off, same proposals\n\n"
              f"{ablation([*runs, (args.result.stem, result)])}\n")  # fmt: skip
    print(f"### Differences\n\n{differences(s, KEY)}\n")
    print(f"### Deadlines of found promises\n\n{deadline_kinds(s)}\n")
    print(f"### Deadline errors: arithmetic or convention\n\n{conventions(result)}\n")
    print("### Totals\n")
    recorded = {r["method"] for r in result["runs"]}
    for m, rows in s["methods"].items():
        if m not in recorded:
            print(f"- {m}: a replay of recorded proposals, no model call")
            continue
        print(f"- {m}: ${rows['usd_total']:.4f} charged, {rows['failures']} tickets with a failure")
    if args.long:
        long = json.loads(args.long.read_text())
        print(f"\n## long: {long['summary']['tickets']} tickets\n")
        print(table(long["summary"], KEY + ["usd_per_message", "model_seconds_per_ticket"]))
        svg, rows = prompt_growth(long)
        args.figure.parent.mkdir(parents=True, exist_ok=True)
        args.figure.write_text(svg)
        print(f"\n### Prompt growth\n\n{rows}\n\n(figure written to {args.figure})")


if __name__ == "__main__":
    main()
