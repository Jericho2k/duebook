"""The comparison: every method on the same rewritten tickets, scored by the same code.

    python -m duebook.bench.experiment --split dev2 --methods event_memory --max-usd 0.2
    python -m duebook.bench.experiment --split test2 --max-usd 1.0   # once

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

Money is guarded three ways. Every answer to a temperature-0 request is kept
on disk (``extract.client.ResponseCache``), so re-running an unchanged method
costs nothing. A session cap is shared by every run: each run appends what it
was charged to ``spend.jsonl`` and may spend only what the cap leaves. And
before anything else a run reads a short sample of the first ticket with every
method, projects the whole run's cost from it, and stops if the projection
does not fit. A method too expensive for every ticket can be given a subset
(``--subset mem0=3``); its intervals and its differences to the event memory
are then computed on those tickets only, and the record says so.

The event memory's proposals are kept, and folded again through the reducer
with its closing guards switched off (``event_memory_unguarded``). That costs
nothing and isolates what the guards do on exactly the same model output.
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
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from ..events import MessagePosted
from ..extract.client import DEFAULT_MODEL, ChatClient, ResponseCache
from ..extract.cost import PRICES, Usage
from ..extract.prompt import Bound
from ..reducer import UNGUARDED
from ..store.codec import decode_proposals
from ..temporal import WorkCalendar
from .baselines.answer import render_event
from .generate import MAIN, SPLITS, SimTicket, Split
from .meter import Ledger, Meter, OverBudget
from .methods import EventMemory, Method, MethodRun
from .result import Snapshot, checkpoints
from .rewrite import Rewriter, require_different_models
from .run import refold, snapshots
from .score import Score, TicketScore, match, same_deadline, score_ticket

#: Rewrites the tickets of round 2: of the cheap models tried on the same dev
#: messages, the one that reworded most without adding anything a label would
#: have to account for (docs/results.md). Served as z-ai/glm-5.3-flash.
WRITER = "~z-ai/glm-flash-latest"
RESULTS = Path("results")
ROUND2 = RESULTS / "round2"
CACHE = Path(".cache/responses")
#: What all live model calls of round 2 may cost together.
SESSION_CAP = 1.50
METHODS = ("event_memory", "full_context", "rolling_summary", "mem0")
UNGUARDED_RUN = "event_memory_unguarded"


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
        # Rephrasing needs no reasoning: ``client.PRESETS`` switches it off for
        # a writer that has it. At this temperature nothing is cached.
        client = ChatClient(api_key="proxy-injected", model=writer, temperature=0.8)

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


def make_methods(
    names: Sequence[str], model: str, ledger: Ledger, cal: WorkCalendar,
    cache: ResponseCache | None = None,
) -> list:  # fmt: skip
    reader = ChatClient(api_key="proxy-injected", model=model, cache=cache)
    # Answering a checkpoint means writing out the whole account; it needs room.
    answerer = ChatClient(api_key="proxy-injected", model=model, max_tokens=12_000, timeout=240,
                          cache=cache)  # fmt: skip
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


def run_all(
    methods: Sequence[Method], tickets: Sequence[SimTicket], workers: int,
    subsets: dict[str, int] | None = None,
) -> list:  # fmt: skip
    subsets = subsets or {}
    jobs = [(m, t) for m in methods for t in tickets[: subsets.get(m.name, len(tickets))]]

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


# ------------------------------------------------------------ the money ----


def spent_before(log: Path) -> float:
    """What earlier runs of this session were charged, by their own records."""
    if not log.exists():
        return 0.0
    return sum(json.loads(line).get("usd", 0.0) for line in log.read_text().splitlines() if line)


def sample_of(ticket: SimTicket, events: int) -> SimTicket:
    """The first ``events`` events of a ticket, ending an hour after the last."""
    kept = ticket.events[:events]
    return replace(ticket, events=kept, proposals=ticket.proposals[:events],
                   end=kept[-1].at + timedelta(hours=1))  # fmt: skip


#: How much more a unit (an event read, or a character of transcript) costs
#: over a whole ticket than in the opening sample: the card, the summary and
#: Mem0's store grow, and later checkpoint answers list more. Measured on
#: round 2's test split against a 20-event sample: event memory 1.5, full
#: context 0.7, rolling summary 3.4 (it was projected at 2.0 and three of its
#: four tickets ran into the cap), Mem0 2.4.
GROWTH = {"event_memory": 1.6, "full_context": 1.5, "rolling_summary": 3.5, "mem0": 2.5}


def project(
    methods: Sequence[Method], tickets: Sequence[SimTicket], subsets: dict[str, int],
    ledger: Ledger, cal: WorkCalendar, events: int = 30,
) -> dict[str, dict[str, float]]:
    """Read the opening of the first ticket with each method and project the run.

    The sample is real: it is charged, and through the response cache the run
    reuses what it can of it. "upper" assumes no answer comes from the cache;
    "expected" scales what the sample was actually charged.
    """
    out = {}
    sample = sample_of(tickets[0], events)
    for method in methods:
        before, reused = ledger.usd(), ledger.reused.usd(PRICES.get(_model(method))) or 0.0
        run = method.run(sample)
        charged = ledger.usd() - before
        made = run.usage.usd(PRICES.get(_model(method))) or 0.0
        chosen = tickets[: subsets.get(method.name, len(tickets))]
        if method.name.startswith("full_context"):
            per = len(_transcript(sample, sample.events[-1].seq, cal))
            units = sum(len(_transcript(t, cp.seq, cal)) for t in chosen for cp in checkpoints(t))
        else:
            per, units = len(sample.events), sum(len(t.events) for t in chosen)
        scale = GROWTH.get(method.name.removesuffix("_bounded"), 2.0) * units / per
        out[method.name] = {"sample_usd": made, "sample_charged": charged,
                            "upper": made * scale, "expected": charged * scale,
                            "reused": (ledger.reused.usd() or 0.0) - reused}  # fmt: skip
    return out


def _transcript(ticket: SimTicket, seq: int, cal: WorkCalendar) -> str:
    return "\n".join(render_event(e, cal) for e in ticket.events[:seq])


def _model(method: Method) -> str:
    return getattr(getattr(method, "model", None), "model", DEFAULT_MODEL)


# ------------------------------------------------------- guards replayed ----


def unguarded(run: MethodRun, ticket: SimTicket, cal: WorkCalendar) -> MethodRun:
    """The event memory's own proposals, folded again without the closing guards.

    No model is asked: the proposals are the ones the guarded run recorded.
    The card differs only where a guard refused a close.
    """
    proposals = [decode_proposals(p) for p in run.detail.get("proposals", [])]
    folded = refold(ticket, proposals, UNGUARDED, cal)
    return replace(run, method=UNGUARDED_RUN, snapshots=snapshots(ticket, folded),
                   detail={"replayed_from": run.method})  # fmt: skip


def guard_effects(
    tickets: Sequence[SimTicket], runs: Sequence[MethodRun]
) -> dict[str, Any]:
    """What the guards refused, judged against the labels.

    A refused close is *a false close prevented* when the target was still
    owed at that message, *a true close blocked* when that very message is
    the one that closed the target, and *a wrong-target close prevented* when
    the target was closed by another message.
    """
    by_id = {t.ticket_id: t for t in tickets}
    kinds = {"false_close_prevented": 0, "true_close_blocked": 0,
             "wrong_target_prevented": 0, "not_a_planted_promise": 0}  # fmt: skip
    reasons: dict[str, dict[str, int]] = {}
    examples = []
    for run in runs:
        ticket = by_id[run.ticket_id]
        gold = {c.seq: c for c in ticket.gold.commitments}
        for seq, reason, kind, target in run.detail.get("refused", []):
            g = gold.get(target)
            if g is None:
                verdict = "not_a_planted_promise"
            elif not g.closed(seq):
                verdict = "false_close_prevented"
            elif g.resolved_seq == seq:
                verdict = "true_close_blocked"
            else:
                verdict = "wrong_target_prevented"
            kinds[verdict] += 1
            reasons.setdefault(reason, {}).setdefault(verdict, 0)
            reasons[reason][verdict] += 1
            if len(examples) < 40:
                said = ticket.events[seq - 1]
                examples.append({"ticket": ticket.ticket_id, "seq": seq, "reason": reason,
                                 "kind": kind, "target_seq": target, "verdict": verdict,
                                 "text": getattr(said, "text", "")})  # fmt: skip
    return {"verdicts": kinds, "by_reason": reasons, "examples": examples}


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
    "fact_recall": ("all", lambda s, n: s.fact_recall),
    "fact_change_accuracy": ("all", lambda s, n: s.change_accuracy),
    "dispute_precision": ("all", lambda s, n: s.disputes.precision),
    "dispute_recall": ("all", lambda s, n: s.disputes.recall),
    "false_disputes_per_ticket": ("all", lambda s, n: s.disputes.fp / n),
    "failed_step_precision": ("all", lambda s, n: s.failed_steps.precision),
    "failed_step_recall": ("all", lambda s, n: s.failed_steps.recall),
    "unanswered_checkpoints": ("all", lambda s, n: s.errors / max(s.checkpoints, 1)),
    # Closing, at the end of each ticket: the round-2 measures.
    "false_closes_per_ticket": ("end", lambda s, n: s.closes_false / n),
    "wrong_target_closes_per_ticket": ("end", lambda s, n: s.closes_wrong_target / n),
    "missed_close_rate": ("end", lambda s, n: s.closes_missed / max(s.closes_judged, 1)),
    "close_precision": ("end", lambda s, n: s.close_precision),
    "suggestions_per_ticket": ("end", lambda s, n: (s.suggestions_right + s.suggestions_wrong) / n),
    "false_closes_any_checkpoint": ("all", lambda s, n: s.closes_false / n),
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
    "reasoning_tokens_per_ticket": lambda r, t: r.usage.reasoning_tokens,
    # The share of prompt tokens the provider served from its prompt cache.
    "prompt_cache_share": lambda r, t: r.usage.cache_share,
}


def summarise(
    tickets: Sequence[SimTicket], runs: Sequence[MethodRun], boot: int = 2000, seed: int = 0
) -> dict[str, Any]:
    """Per method: every metric with a 95% bootstrap interval over tickets."""
    by_id = {t.ticket_id: t for t in tickets}
    names = list(dict.fromkeys(r.method for r in runs))
    runs_of = {(r.method, r.ticket_id): r for r in runs}
    scores = {
        key: score_ticket(by_id[key[1]], run.snapshots) for key, run in runs_of.items()
    }  # fmt: skip
    reference = "event_memory" if "event_memory" in names else names[0]
    # A method run on a subset of the tickets is summarised, and compared with
    # the reference, on those tickets only — always the first N of the split.
    own = {m: [t.ticket_id for t in tickets if (m, t.ticket_id) in runs_of] for m in names}
    samples_for: dict[int, list[list[int]]] = {}

    def samples(n: int) -> list[list[int]]:
        if n not in samples_for:
            rng = random.Random(seed)
            samples_for[n] = [[rng.randrange(n) for _ in range(n)] for _ in range(boot)]
        return samples_for[n]

    def values(method: str, order: list[str]) -> dict[str, list[float]]:
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
        draws = [at(s) for s in samples(len(order))]
        return {k: [point[k], *(d[k] for d in draws)] for k in point}

    summary: dict[str, Any] = {"tickets": len(tickets), "bootstrap": boot,
                               "reference": reference, "methods": {}}  # fmt: skip
    for m in names:
        order = own[m]
        computed, versus = values(m, order), values(reference, order)
        rows: dict[str, Any] = {}
        for k, series in computed.items():
            point, draws = series[0], series[1:]
            diffs = [a - b for a, b in zip(draws, versus[k][1:], strict=True)]
            rows[k] = {
                "value": point,
                "ci95": _interval(draws),
                "diff_vs_reference": point - versus[k][0],
                "diff_ci95": _interval(diffs),
            }
        rows["tickets"] = len(order)
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
    parser.add_argument("--split", choices=sorted(SPLITS), default="dev2")
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--model", default=DEFAULT_MODEL, help="the extracting model")
    parser.add_argument("--writer", default=WRITER, help="rewrites the tickets; not --model")
    parser.add_argument(
        "--max-usd",
        type=float,
        required=True,
        help="no model call is made once this run has been charged this much",
    )
    parser.add_argument("--session-cap", type=float, default=SESSION_CAP,
                        help="what all runs logged in <results>/spend.jsonl may cost together")
    parser.add_argument("--subset", default="",
                        help="run a method on the first N tickets only: mem0=3,rolling_summary=5")
    parser.add_argument("--tickets", type=int, help="only the first N tickets of the split")
    parser.add_argument("--sample-events", type=int, default=30,
                        help="events of the first ticket read to project the cost")
    parser.add_argument("--expected", action="store_true",
                        help="judge the projection by what the sample was charged (trusting the "
                             "response cache) instead of what it would cost uncached")
    parser.add_argument("--no-cache", action="store_true", help="do not use the response cache")
    parser.add_argument("--rewrite-only", action="store_true",
                        help="rewrite and save the split's tickets, then stop")
    parser.add_argument("--project-only", action="store_true",
                        help="print the cost projection (its sample is charged), then stop")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, help="default: <results>/<split>.json")
    parser.add_argument("--label", default="", help="a note for the spend log")
    parser.add_argument("--results", type=Path,
                        help="default: results/round2 for round-2 splits, else results")
    args = parser.parse_args(argv)  # fmt: skip

    require_different_models(args.writer, args.model)
    split = SPLITS[args.split]
    results = args.results or (ROUND2 if split.profile is MAIN else RESULTS)
    out = args.out or results / f"{split.name}.json"
    if split.name.startswith("test") and out.exists():
        raise SystemExit(f"{out} exists: the test split is run once")
    log = results / "spend.jsonl"
    left = args.session_cap - spent_before(log)
    if left <= 0:
        raise SystemExit(f"the session cap of ${args.session_cap:.2f} is spent ({log})")
    cal = WorkCalendar()
    ledger = Ledger(cap_usd=min(args.max_usd, left))
    started = datetime.now().astimezone()
    subsets = {k: int(v) for k, v in (x.split("=") for x in args.subset.split(",") if x)}
    cache = None if args.no_cache else ResponseCache(CACHE)

    def spend(what: str) -> None:
        results.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(json.dumps({"at": started.isoformat(), "split": split.name, "what": what,
                                "methods": args.methods, "label": args.label,
                                "usd": ledger.usd(),
                                "reused_usd": ledger.reused.usd() or 0.0}) + "\n")  # fmt: skip

    try:
        tickets, written = rewritten(split, args.writer, ledger, results / "tickets",
                                     args.workers)  # fmt: skip
    except OverBudget:
        spend("rewriting, stopped at the cap")
        raise
    if args.rewrite_only:
        spend("rewriting the tickets")
        discarded = sum(v["discarded"] for v in written["tickets"].values())
        print(f"{len(tickets)} tickets rewritten by {args.writer}: {discarded} of "
              f"{sum(t.messages for t in tickets)} rewrites discarded, ${ledger.usd():.4f}")
        return
    if args.tickets:
        tickets = tickets[: args.tickets]
    writing = ledger.usd()
    print(f"{len(tickets)} tickets of split {split.name!r}, rewritten by {args.writer} "
          f"(${writing:.4f} this run); session has ${left:.4f} left of "
          f"${args.session_cap:.2f}", file=sys.stderr)  # fmt: skip

    methods = make_methods(args.methods.split(","), args.model, ledger, cal, cache)
    projected = project(methods, tickets, subsets, ledger, cal, args.sample_events)
    judged = "expected" if args.expected else "upper"
    total = sum(p[judged] for p in projected.values())
    for name, p in projected.items():
        print(f"  projected {name:<22} upper ${p['upper']:.4f}  expected ${p['expected']:.4f}"
              f"  (sample ${p['sample_usd']:.4f}, charged ${p['sample_charged']:.4f})",
              file=sys.stderr)  # fmt: skip
    room = ledger.cap_usd - ledger.usd()
    print(f"  projected total ({judged}) ${total:.4f}; room ${room:.4f}", file=sys.stderr)
    if args.project_only:
        spend("cost projection only")
        return
    if total > room:
        spend("cost projection only: the run would not fit")
        raise SystemExit(f"projected ${total:.4f} exceeds the ${room:.4f} left: not run")

    runs = run_all(methods, tickets, args.workers, subsets)
    by_id = {t.ticket_id: t for t in tickets}
    guarded = [r for r in runs if r.method == "event_memory"]
    replayed = [unguarded(r, by_id[r.ticket_id], cal) for r in guarded]
    summary = summarise(tickets, runs + replayed)
    effects = guard_effects(tickets, guarded)
    result = {
        "split": split.name,
        "seed": split.seed,
        "profile": split.profile.name,
        "model": args.model,
        "writer": args.writer,
        "started": started.isoformat(),
        "finished": datetime.now().astimezone().isoformat(),
        "subsets": subsets,
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
        "spent_usd": {"writing": writing, "total": ledger.usd(),
                      "reused_from_cache": ledger.reused.usd() or 0.0},  # fmt: skip
        "projection": projected,
        "guard_effects": effects,
        "summary": summary,
        "runs": [record(r, score_ticket(by_id[r.ticket_id], r.snapshots)) for r in runs],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(to_json(result), ensure_ascii=False, indent=1))
    spend("run")
    keys = ["commitment_f1", "deadline_accuracy", "status_accuracy", "breach_alert_recall",
            "false_alerts_per_ticket", "false_closes_per_ticket",
            "wrong_target_closes_per_ticket", "missed_close_rate", "suggestions_per_ticket",
            "freshness_error", "dispute_recall", "usd_per_ticket"]  # fmt: skip
    print(table(summary, keys))
    print(f"guards: {effects['verdicts']}", file=sys.stderr)
    print(f"\ncharged this run: ${ledger.usd():.4f} (+${ledger.reused.usd() or 0:.4f} reused "
          f"from the cache); written to {out}", file=sys.stderr)  # fmt: skip


if __name__ == "__main__":
    main()
