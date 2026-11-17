"""The comparison: every method on the same rewritten tickets, scored by the same code.

    python -m duebook.bench.experiment --split dev --max-usd 1.0
    python -m duebook.bench.experiment --split test --max-usd 2.0   # once

Steps:

1. **Tickets.** The split's tickets are generated from its seed, and their
   text is rewritten by the writer model — never the extracting one. The
   rewritten text is cached under ``results/tickets/`` so every method, and
   every later run, reads exactly the same words.
2. **Methods.** The event memory and the three baselines read each ticket;
   (method, ticket) pairs run in parallel threads.
3. **Scores.** Every snapshot is scored; per method, metrics are
   micro-averaged over tickets with 95% bootstrap confidence intervals
   (resampling tickets), and the difference to the event memory is given
   with a paired interval on the same resamples.

Everything a method said, every score and every cost is written to one JSON
file, so the numbers in ``docs/results.md`` can be recomputed from it.

The test split is for the reported numbers and is run once: the command
refuses to overwrite an existing test result.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..events import MessagePosted
from ..extract.client import DEFAULT_MODEL, ChatClient
from ..extract.cost import Usage
from ..extract.prompt import Bound
from ..temporal import WorkCalendar
from .generate import SPLITS, SimTicket, Split
from .meter import Ledger, Meter
from .methods import EventMemory, Method, MethodRun
from .result import Snapshot, checkpoints
from .rewrite import Rewriter, require_different_models
from .score import Score, TicketScore, match, same_deadline, score_ticket

WRITER = "deepseek/deepseek-v4.1-flash"
RESULTS = Path("results")
METHODS = ("event_memory", "full_context", "rolling_summary", "mem0")


# --------------------------------------------------------------- tickets ----


def rewritten(
    split: Split, writer: str, ledger: Ledger, cache: Path, workers: int = 8
) -> tuple[list[SimTicket], dict[str, Any]]:
    """The split's tickets in the writer's words, rewritten once and cached."""
    tickets = split.tickets()
    path = cache / f"{split.name}.json"
    if path.exists():
        stored = json.loads(path.read_text())
        if stored["writer"] != writer or stored["seed"] != split.seed:
            raise SystemExit(f"{path} was written by {stored['writer']} for another split")
    else:
        client = ChatClient(
            api_key="proxy-injected", model=writer, temperature=0.8,
            # Rephrasing needs no reasoning; with it on, the writer spends
            # eight times as much on the same text.
            reasoning_effort=None, extra={"reasoning": {"enabled": False}},
        )  # fmt: skip

        # Messages are rewritten one by one and independently, so all of them
        # can be in flight at once.
        jobs = [(t, e) for t in tickets for e in t.events if isinstance(e, MessagePosted)]

        def one(job) -> tuple[str, str, str, int, Usage]:
            ticket, event = job
            rewriter = Rewriter(Meter(client, ledger))
            text = rewriter.text(event, ticket.anchors.get(event.id, ()))
            return ticket.ticket_id, event.id, text, rewriter.discarded, rewriter.spent

        out: dict[str, Any] = {
            t.ticket_id: {"texts": {}, "discarded": 0, "usage": Usage()} for t in tickets
        }
        with ThreadPoolExecutor(workers) as pool:
            for tid, eid, text, discarded, spent in pool.map(one, jobs):
                out[tid]["texts"][eid] = text
                out[tid]["discarded"] += discarded
                out[tid]["usage"] += spent
        for v in out.values():
            v["usage"] = asdict(v["usage"])
        stored = {"split": split.name, "seed": split.seed, "count": split.count,
                  "profile": split.profile.name, "writer": writer, "tickets": out}  # fmt: skip
        cache.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored, ensure_ascii=False, indent=1))
    result = []
    for t in tickets:
        texts = stored["tickets"][t.ticket_id]["texts"]
        events = tuple(
            replace(e, text=texts[e.id]) if isinstance(e, MessagePosted) else e for e in t.events
        )
        result.append(replace(t, events=events))
    return result, stored


# --------------------------------------------------------------- running ----


def make_methods(names: Sequence[str], model: str, ledger: Ledger, cal: WorkCalendar) -> list:
    reader = ChatClient(api_key="proxy-injected", model=model)
    # Answering a checkpoint means writing out the whole account; it needs room.
    answerer = ChatClient(api_key="proxy-injected", model=model, max_tokens=12_000, timeout=180)
    methods: list[Method] = []
    for name in names:
        if name == "event_memory":
            methods.append(EventMemory(reader, ledger, cal))
        elif name == "event_memory_bounded":
            methods.append(EventMemory(reader, ledger, cal, bound=Bound(), name=name))
        elif name == "full_context":
            from .baselines import FullContext

            methods.append(FullContext(answerer, ledger, cal))
        elif name == "rolling_summary":
            from .baselines import RollingSummary

            methods.append(RollingSummary(_Split(reader, answerer), ledger, cal))
        elif name == "mem0":
            from .baselines import Mem0Memory, mem0_available

            reason = mem0_available()
            if reason:
                raise SystemExit(f"Mem0 is not available: {reason}")
            methods.append(Mem0Memory(answerer, ledger, cal, llm_model=model))
        else:
            raise SystemExit(f"unknown method {name!r}")
    return methods


@dataclass(slots=True)
class _Split:
    """Summary updates through one client, checkpoint answers through another."""

    reader: ChatClient
    answerer: ChatClient

    def complete_json(self, messages, schema, name: str = "output"):
        client = self.answerer if name == "ticket_state" else self.reader
        return client.complete_json(messages, schema, name)


def run_all(methods: Sequence[Method], tickets: Sequence[SimTicket], workers: int) -> list:
    jobs = [(m, t) for m in methods for t in tickets]

    def one(job) -> MethodRun:
        method, ticket = job
        started = time.perf_counter()
        try:
            run = method.run(ticket)
        except Exception as error:  # a method that crashed answers nothing
            snaps = tuple(Snapshot(cp, error=f"{type(error).__name__}: {error}")
                          for cp in checkpoints(ticket))  # fmt: skip
            run = MethodRun(method.name, ticket.ticket_id, snaps, Usage(),
                            failure=f"crashed: {type(error).__name__}: {error}")  # fmt: skip
        print(f"  {method.name:<22} {ticket.ticket_id}  {time.perf_counter() - started:6.1f}s"
              f"  ${run.usage.usd() or 0:.4f}" + (f"  FAILED {run.failure}" if run.failure
              else ""), file=sys.stderr, flush=True)  # fmt: skip
        return run

    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, jobs))


# ------------------------------------------------------------- summarising ----

#: name -> (which checkpoints, the value from summed scores and ticket count).
METRICS: dict[str, tuple[str, Callable[[Score, int], float]]] = {
    "commitment_precision": ("end", lambda s, n: s.commitments.precision),
    "commitment_recall": ("end", lambda s, n: s.commitments.recall),
    "commitment_f1": ("end", lambda s, n: s.commitments.f1),
    "deadline_accuracy": ("end", lambda s, n: s.deadline_accuracy),
    "status_accuracy": ("end", lambda s, n: s.status_accuracy),
    "breach_alert_precision": ("end", lambda s, n: s.alerts.precision),
    "breach_alert_recall": ("end", lambda s, n: s.alerts.recall),
    "false_alerts_per_ticket": ("end", lambda s, n: s.alerts.fp / n),
    "freshness_error": ("all", lambda s, n: s.freshness_error),
    "freshness_error_at_handoffs": ("handoffs", lambda s, n: s.freshness_error),
    "fact_change_accuracy": ("all", lambda s, n: s.change_accuracy),
    "dispute_precision": ("all", lambda s, n: s.disputes.precision),
    "dispute_recall": ("all", lambda s, n: s.disputes.recall),
    "false_disputes_per_ticket": ("all", lambda s, n: s.disputes.fp / n),
    "failed_step_precision": ("all", lambda s, n: s.failed_steps.precision),
    "failed_step_recall": ("all", lambda s, n: s.failed_steps.recall),
    "unanswered_checkpoints": ("all", lambda s, n: s.errors / max(s.checkpoints, 1)),
}
#: name -> value per ticket; averaged over tickets.
COSTS: dict[str, Callable[[MethodRun, SimTicket], float]] = {
    "usd_per_ticket": lambda r, t: r.usage.usd() or 0.0,
    "prompt_tokens_per_ticket": lambda r, t: r.usage.prompt_tokens,
    "completion_tokens_per_ticket": lambda r, t: r.usage.completion_tokens,
    "calls_per_ticket": lambda r, t: r.usage.calls,
    "usd_per_message": lambda r, t: (r.usage.usd() or 0.0) / t.messages,
    "ingest_seconds_per_message": lambda r, t: r.ingest_seconds / t.messages,
    "answer_seconds_per_checkpoint": lambda r, t: r.answer_seconds / len(checkpoints(t)),
    "model_seconds_per_ticket": lambda r, t: r.ingest_seconds + r.answer_seconds,
}


def summarise(
    tickets: Sequence[SimTicket], runs: Sequence[MethodRun], boot: int = 2000, seed: int = 0
) -> dict[str, Any]:
    """Per method: every metric with a 95% bootstrap interval over tickets."""
    by_id = {t.ticket_id: t for t in tickets}
    order = [t.ticket_id for t in tickets]
    names = list(dict.fromkeys(r.method for r in runs))
    runs_of = {(r.method, r.ticket_id): r for r in runs}
    scores = {
        key: score_ticket(by_id[key[1]], run.snapshots) for key, run in runs_of.items()
    }  # fmt: skip
    rng = random.Random(seed)
    samples = [[rng.randrange(len(order)) for _ in order] for _ in range(boot)]
    reference = "event_memory" if "event_memory" in names else names[0]

    def values(method: str) -> dict[str, list[float]]:
        per = [scores[method, tid] for tid in order]
        cost = {k: [f(runs_of[method, tid], by_id[tid]) for tid in order] for k, f in COSTS.items()}

        def at(indices) -> dict[str, float]:
            picked = [per[i] for i in indices]
            sums = {
                "end": _sum(p.end for p in picked),
                "handoffs": _sum(p.handoffs for p in picked),
            }
            sums["all"] = sums["end"] + sums["handoffs"]
            out = {k: f(sums[w], len(picked)) for k, (w, f) in METRICS.items()}
            out |= {k: sum(v[i] for i in indices) / len(indices) for k, v in cost.items()}
            return out

        point = at(range(len(order)))
        draws = [at(s) for s in samples]
        return {k: [point[k], *(d[k] for d in draws)] for k in point}

    computed = {m: values(m) for m in names}
    summary: dict[str, Any] = {"tickets": len(order), "bootstrap": boot, "reference": reference,
                               "methods": {}}  # fmt: skip
    for m in names:
        rows = {}
        for k, series in computed[m].items():
            point, draws = series[0], series[1:]
            diffs = [a - b for a, b in zip(draws, computed[reference][k][1:], strict=True)]
            rows[k] = {
                "value": point,
                "ci95": _interval(draws),
                "diff_vs_reference": point - computed[reference][k][0],
                "diff_ci95": _interval(diffs),
            }
        total = _sum(scores[m, tid].all for tid in order)
        end = _sum(scores[m, tid].end for tid in order)
        rows["counts_end"] = end.as_dict()
        rows["counts_all"] = total.as_dict()
        rows["failures"] = sum(1 for tid in order if runs_of[m, tid].failure)
        rows["usd_total"] = sum(runs_of[m, tid].usage.usd() or 0.0 for tid in order)
        rows["deadline_errors"] = deadline_errors([by_id[t] for t in order],
                                                  [runs_of[m, t] for t in order])  # fmt: skip
        summary["methods"][m] = rows
    return summary


def deadline_errors(tickets: Sequence[SimTicket], runs: Sequence[MethodRun]) -> dict[str, Any]:
    """How the deadlines of found promises went wrong, at the end of each ticket."""
    kinds = {"right": 0, "missing": 0, "invented": 0, "wrong_day": 0, "wrong_time": 0}
    examples = []
    for ticket, run in zip(tickets, runs, strict=True):
        end = run.snapshots[-1]
        pairs, _, _ = match(
            [c for c in ticket.gold.commitments if c.seq <= end.checkpoint.seq], end.promises
        )
        for gold, found in pairs:
            if same_deadline(gold.due_at, found.due_at):
                kinds["right"] += 1
                continue
            if found.due_at is None:
                kind = "missing"
            elif gold.due_at is None:
                kind = "invented"
            else:
                tz = WorkCalendar().tz
                same_day = gold.due_at.astimezone(tz).date() == found.due_at.astimezone(tz).date()
                kind = "wrong_time" if same_day else "wrong_day"
            kinds[kind] += 1
            if len(examples) < 25:
                said = ticket.events[gold.seq - 1]
                examples.append({
                    "ticket": ticket.ticket_id, "seq": gold.seq, "kind": kind,
                    "said_at": said.at.isoformat(), "text": getattr(said, "text", ""),
                    "gold": gold.due_at.isoformat() if gold.due_at else None,
                    "found": found.due_at.isoformat() if found.due_at else None,
                })  # fmt: skip
    return {"kinds": kinds, "examples": examples}


def _sum(scores) -> Score:
    total = Score()
    for s in scores:
        total += s
    return total


def _interval(draws: list[float]) -> list[float]:
    ordered = sorted(draws)
    lo = ordered[int(0.025 * (len(ordered) - 1))]
    hi = ordered[int(round(0.975 * (len(ordered) - 1)))]
    return [lo, hi]


# ------------------------------------------------------------ the record ----


def to_json(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f: to_json(getattr(value, f)) for f in value.__dataclass_fields__}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(v) for v in value]
    return value


def record(run: MethodRun, scored: TicketScore) -> dict[str, Any]:
    return {
        "method": run.method,
        "ticket": run.ticket_id,
        "failure": run.failure,
        "usage": asdict(run.usage),
        "usd": run.usage.usd(),
        "ingest_usage": asdict(run.ingest_usage),
        "answer_usage": asdict(run.answer_usage),
        "ingest_seconds": run.ingest_seconds,
        "answer_seconds": run.answer_seconds,
        "score_end": scored.end.as_dict(),
        "score_handoffs": scored.handoffs.as_dict(),
        "snapshots": to_json(run.snapshots),
        "detail": to_json(run.detail),
    }


def table(summary: dict[str, Any], keys: Sequence[str]) -> str:
    """A markdown table: one column per method, value [95% CI]."""
    methods = list(summary["methods"])
    lines = ["| metric | " + " | ".join(methods) + " |",
             "|---|" + "---|" * len(methods)]  # fmt: skip
    for k in keys:
        cells = []
        for m in methods:
            row = summary["methods"][m][k]
            cells.append(f"{_fmt(row['value'])} [{_fmt(row['ci95'][0])}, {_fmt(row['ci95'][1])}]")
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _fmt(x: float) -> str:
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 10:
        return f"{x:.1f}"
    if abs(x) >= 0.01 or x == 0:
        return f"{x:.3f}"
    return f"{x:.5f}"


# ------------------------------------------------------------------ CLI ----


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m duebook.bench.experiment")
    parser.add_argument("--split", choices=sorted(SPLITS), default="dev")
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--model", default=DEFAULT_MODEL, help="the extracting model")
    parser.add_argument("--writer", default=WRITER, help="rewrites the tickets; not --model")
    parser.add_argument(
        "--max-usd",
        type=float,
        required=True,
        help="no model call is made once this run has been charged this much",
    )
    parser.add_argument("--tickets", type=int, help="only the first N tickets of the split")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, help="default: results/<split>.json")
    parser.add_argument("--results", type=Path, default=RESULTS)
    args = parser.parse_args(argv)  # fmt: skip

    require_different_models(args.writer, args.model)
    split = SPLITS[args.split]
    out = args.out or args.results / f"{split.name}.json"
    if split.name == "test" and out.exists():
        raise SystemExit(f"{out} exists: the test split is run once")
    cal = WorkCalendar()
    ledger = Ledger(cap_usd=args.max_usd)
    started = datetime.now().astimezone()

    tickets, written = rewritten(split, args.writer, ledger, args.results / "tickets",
                                 args.workers)  # fmt: skip
    if args.tickets:
        tickets = tickets[: args.tickets]
    writing = ledger.usd()
    print(f"{len(tickets)} tickets of split {split.name!r}, rewritten by {args.writer} "
          f"(${writing:.4f} this run)", file=sys.stderr)  # fmt: skip

    methods = make_methods(args.methods.split(","), args.model, ledger, cal)
    runs = run_all(methods, tickets, args.workers)
    summary = summarise(tickets, runs)
    by_id = {t.ticket_id: t for t in tickets}
    result = {
        "split": split.name,
        "seed": split.seed,
        "profile": split.profile.name,
        "model": args.model,
        "writer": args.writer,
        "started": started.isoformat(),
        "finished": datetime.now().astimezone().isoformat(),
        "tickets": [
            {
                "id": t.ticket_id,
                "messages": t.messages,
                "events": len(t.events),
                "handoffs": len(t.gold.handoffs),
                "promises": len(t.gold.commitments),
                "discarded_rewrites": written["tickets"][t.ticket_id]["discarded"],
            }
            for t in tickets
        ],  # fmt: skip
        "spent_usd": {"writing": writing, "total": ledger.usd()},
        "summary": summary,
        "runs": [record(r, score_ticket(by_id[r.ticket_id], r.snapshots)) for r in runs],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(to_json(result), ensure_ascii=False, indent=1))
    with (args.results / "spend.jsonl").open("a") as log:
        log.write(json.dumps({"at": started.isoformat(), "split": split.name,
                              "methods": args.methods, "tickets": len(tickets),
                              "usd": ledger.usd()}) + "\n")  # fmt: skip
    keys = ["commitment_f1", "deadline_accuracy", "breach_alert_recall", "false_alerts_per_ticket",
            "freshness_error", "fact_change_accuracy", "dispute_recall", "failed_step_recall",
            "usd_per_ticket"]  # fmt: skip
    print(table(summary, keys))
    print(f"\ncharged this run: ${ledger.usd():.4f}; written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
