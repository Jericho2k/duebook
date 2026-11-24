"""The comparison: every method on the same rewritten tickets, scored by the same code.

    python -m duebook.bench.experiment --split dev2 --methods event_memory --max-usd 0.2
    python -m duebook.bench.experiment --split test3 --max-usd 4.5   # once

Steps:

1. **Tickets.** The split's tickets are generated from its seed, and their
   text is rewritten by the writer model — never the extracting one. The
   rewritten text is kept under the split's round (``<round>/tickets/``) so
   every method, and every later run, reads exactly the same words.
2. **Methods.** The event memory and the baselines read each ticket;
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

A round is a configuration of this runner, not a module. The event memory
runs as the product ships it (``event_memory``) or with a reasoning extractor
and no verifier (``event_memory_reasoning``). Its proposals, with the
verifier's verdicts, are kept and folded again with the verdicts ignored
(``event_memory_no_verifier``) and with the closing guards off
(``event_memory_unguarded``): the same model output, no model call, so what
the verifier and the guards do is isolated.

Records stand in for model calls. ``--replay`` folds the proposals a recorded
``event_memory`` run made (or, given ``oracle``, the planted ones), so a later
reducer is measured on frozen model output; ``--reread`` reads the messages it
lists again, live, against the card the replay rebuilds. ``--frozen`` takes
every method not run now from a record (or several, the first holding a run
wins) and scores its snapshots again; a frozen run is not written again.
``full_context_live`` is the full-context baseline asked after every message.
Every record says, per method, when it knew each promise and alerted on each
broken one, how its answers changed from one to the next (``timeliness``), and
what a ticket costs answering only at checkpoints and with an answer current
after every message (``regimes``).
Finished runs are journalled under ``.cache/runs/``; an interrupted run
resumes from the journal and the response cache, and never pays twice.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from ..card import Card
from ..events import MessagePosted, PromiseCheck, other_side, remember_turn
from ..extract.client import (
    DEFAULT_MODEL,
    REASONING,
    ChatClient,
    ModelError,
    ResponseCache,
    reconciliation_client,
)
from ..extract.cost import Usage
from ..extract.extractor import reading_clients
from ..extract.verifier import DEFAULT_VERIFIER_REASONING, target_of
from ..lifecycle import AlertPolicy, NoticeKind, next_wakeup, notices
from ..proposals import AssertFact, MakeCommitment, MakeConditional, Proposal, SettleConditional
from ..reducer import UNGUARDED, Guards, apply
from ..slots import DEFAULT as DEFAULT_VOCABULARY
from ..store.codec import decode_event, decode_proposals, serialized_fields
from ..temporal import WorkCalendar
from .baselines.answer import render_event
from .baselines.full_context import moments
from .generate import SPLITS, Beat, CloseCase, SimTicket, Split
from .meter import Ledger, Meter
from .methods import EventMemory, Method, MethodRun, Recorded
from .result import Checkpoint, Moment, Snapshot, checkpoints, from_card, snapshot_of
from .rewrite import Rewriter, require_different_models, with_rewritten_text
from .run import Replayed, fold, refold, snapshots
from .score import (
    Score,
    Stability,
    TicketScore,
    attribute,
    beat_outcomes,
    match,
    same_deadline,
    score_ticket,
    stability,
    timeliness,
)

#: Rewrites the tickets of round 2: of the cheap models tried on the same dev
#: messages, the one that reworded most without adding anything a label would
#: have to account for (docs/results.md). Served as z-ai/glm-5.3-flash.
WRITER = "~z-ai/glm-flash-latest"
CACHE = Path(".cache/responses")
#: ``--replay oracle``: the planted proposals stand in for a recorded run.
ORACLE = Path("oracle")
#: Finished (method, ticket) runs of a run in progress, to resume from.
JOURNAL = Path(".cache/runs")
#: What all live model calls logged in one results directory may cost together.
SESSION_CAP = 1.50
WRITER_ATTEMPTS = 3
METHODS = ("event_memory", "full_context", "rolling_summary", "mem0", "graphiti")
#: The event memory's configurations: (extractor reasoning, verifier reasoning
#: or None for no verifier).
EVENT_MEMORY = {"event_memory": ("off", DEFAULT_VERIFIER_REASONING),
                "event_memory_reasoning": ("low", None)}  # fmt: skip
#: The event memory's own proposals folded again under other rules: verdicts
#: ignored, and the guards off as well.
REFOLDS = {"event_memory_no_verifier": Guards(verifier=False),
           "event_memory_unguarded": UNGUARDED}  # fmt: skip


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
        client = ChatClient(api_key=_api_key(), model=writer, temperature=0.8)

        # Messages are rewritten one by one and independently, so all of them
        # can be in flight at once.
        jobs = [(t, e) for t in tickets for e in t.events if isinstance(e, MessagePosted)]

        def one(job) -> tuple[str, str, str, int, Usage]:
            ticket, event = job
            rewriter = Rewriter(Meter(client, ledger))
            # A broken connection costs one message, not the paid rewrites of
            # the whole split: it is tried again, then left in template words.
            for attempt in range(WRITER_ATTEMPTS):
                try:
                    text = rewriter.text(event, ticket.anchors.get(event.id, ()))
                    break
                except ModelError as error:
                    rewriter.spent += error.usage
                    if attempt == WRITER_ATTEMPTS - 1:
                        text, rewriter.discarded = event.text, rewriter.discarded + 1
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
        path.write_text(compact(stored))
    result = [with_rewritten_text(t, stored["tickets"][t.ticket_id]["texts"]) for t in tickets]
    return result, stored


# --------------------------------------------------------------- running ----


def _api_key() -> str:
    return os.environ.get("DUEBOOK_OPENROUTER_API_KEY") or "proxy-injected"


def make_methods(
    names: Sequence[str], model: str, ledger: Ledger, cal: WorkCalendar,
    cache: ResponseCache | None = None, recorded: dict[str, Recorded] | None = None,
    reread: dict[str, frozenset[int]] | None = None,
    reconcile_reasoning: str | None = None,
) -> list:  # fmt: skip
    """The methods by name; ``recorded`` replays proposals for ``event_memory``, except
    the messages in ``reread``, which are read again live."""
    key = _api_key()
    reader = ChatClient(api_key=key, model=model, cache=cache)
    # Answering a checkpoint means writing out the whole account; it needs room.
    answerer = ChatClient(api_key=key, model=model, max_tokens=12_000, timeout=240,
                          cache=cache)  # fmt: skip
    methods: list[Method] = []
    for name in names:
        if name in EVENT_MEMORY:
            extracting, verifying = EVENT_MEMORY[name]
            # Round 3 gave a reasoning extractor more time.
            extractor, escalation = reading_clients(replace(
                reader, reasoning=extracting,
                timeout=120 if extracting == "low" else reader.timeout))
            # The verifier is called as ``cli.extractor`` calls it: the same
            # client, with the verifier's reasoning.
            live = recorded is None or reread is not None
            verifier = replace(reader, reasoning=verifying) if verifying and live else None
            reconciler = (reconciliation_client(reader, reconcile_reasoning)
                          if name == "event_memory" and recorded is None else None)
            methods.append(EventMemory(extractor, ledger, cal, name=name, verifier=verifier,
                                       reconciler=reconciler,
                                       recorded=recorded if name == "event_memory" else None,
                                       escalation=escalation, reread=reread))
        elif name in ("full_context", "full_context_live"):
            from .baselines import FullContext

            live = name == "full_context_live"
            methods.append(FullContext(answerer, ledger, cal, name=name, live=live))
        elif name == "rolling_summary":
            from .baselines import RollingSummary

            methods.append(RollingSummary(_Split(reader, answerer), ledger, cal))
        elif name == "mem0":
            from .baselines import Mem0Memory, mem0_available

            reason = mem0_available()
            if reason:
                raise SystemExit(f"Mem0 is not available: {reason}")
            methods.append(Mem0Memory(answerer, ledger, cal, llm_model=model))
        elif name == "graphiti":
            from .baselines import GraphitiMemory, graphiti_available

            reason = graphiti_available()
            if reason:
                raise SystemExit(f"Graphiti is not available: {reason}")
            methods.append(GraphitiMemory(answerer, ledger, cal, llm_model=model, cache=cache))
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
    subsets: dict[str, int] | None = None, journal: Path | None = None,
) -> list:  # fmt: skip
    """Every (method, ticket) pair; one already in the journal without a failure is not run."""
    subsets = subsets or {}
    jobs = [(m, t) for m in methods for t in tickets[: subsets.get(m.name, len(tickets))]]
    done = {(r.method, r.ticket_id): r for r in load_runs(journal) if not r.failure}
    lock = threading.Lock()

    def one(job) -> MethodRun:
        method, ticket = job
        if (method.name, ticket.ticket_id) in done:
            return done[method.name, ticket.ticket_id]
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
        if journal is not None:
            with lock, journal.open("a") as f:
                f.write(json.dumps(saved(run), ensure_ascii=False) + "\n")
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
#: four tickets ran into the cap), Mem0 2.4. Graphiti, on two dev2 tickets
#: against a 30-event sample: 1.1. Full context asked live, on test3's first ticket
#: against a 20-event sample: 0.46 (early answers are mostly fixed overhead).
GROWTH = {"event_memory": 1.6, "full_context": 1.5, "rolling_summary": 3.5, "mem0": 2.5,
          "graphiti": 1.2, "full_context_live": 0.5}  # fmt: skip


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
        before, reused = ledger.usd(), ledger.reused.usd() or 0.0
        method.run(sample)
        charged = ledger.usd() - before
        # Every call made now, from the cache or not; proposals replayed from a
        # record are no call.
        made = charged + (ledger.reused.usd() or 0.0) - reused
        chosen = tickets[: subsets.get(method.name, len(tickets))]
        if method.name.startswith("full_context"):
            asked = moments if method.name == "full_context_live" else checkpoints
            per = sum(transcript_chars(sample, cal, asked))
            units = sum(sum(transcript_chars(t, cal, asked)) for t in chosen)
        else:
            per, units = len(sample.events), sum(len(t.events) for t in chosen)
        scale = GROWTH.get(method.name, 2.0) * units / per
        out[method.name] = {"sample_usd": made, "sample_charged": charged,
                            "upper": made * scale, "expected": charged * scale,
                            "reused": made - charged}  # fmt: skip
    return out


def project_from(path: Path, methods, tickets, subsets) -> dict[str, dict[str, float]]:
    """Budget a held-out run without querying any held-out message first."""
    previous = json.loads(path.read_text())
    counts = {t["id"]: t["messages"] for t in previous["tickets"]}
    estimates = {}
    for method in methods:
        runs = [r for r in load_runs(path) if r.method == method.name]
        if not runs or any(r.failure or r.usage.usd() is None for r in runs):
            raise SystemExit(f"{path}: projection requires completed {method.name} runs")
        messages = sum(t.messages for t in tickets[: subsets.get(method.name, len(tickets))])
        rates = [r.usage.usd() / counts[r.ticket_id] for r in runs]
        upper, expected = 1.5 * max(rates), sum(rates) / len(rates)
        estimates[method.name] = {
            "upper": upper * messages, "expected": expected * messages,
            "sample_usd": 0.0, "sample_charged": 0.0, "reused": 0.0,
        }
    return estimates


def project_reread(path: Path, calls: int) -> dict[str, dict[str, float]]:
    """Budget a targeted re-extraction from what one extraction call cost in ``path``."""
    runs = [r for r in load_runs(path) if r.method == "event_memory"]
    used = [Usage(**r.detail["extraction_usage"]) for r in runs]
    rate = sum(u.usd() or 0.0 for u in used) / max(sum(u.calls for u in used), 1)
    verifying = sum((r.detail.get("verifier") or {}).get("usd") or 0.0 for r in runs)
    rate += verifying / max(sum(u.calls for u in used), 1)
    return {"event_memory": {"upper": 2 * rate * calls, "expected": rate * calls,
                             "sample_usd": 0.0, "sample_charged": 0.0, "reused": 0.0}}  # fmt: skip


def reread_changes(tickets: Sequence[SimTicket], runs: Sequence[MethodRun]) -> list[dict]:
    """Per message read again: what the record and the live read proposed, and what was lost.

    A promise of a side, or a fact's slot, that the record had and the new read
    has not is listed as lost, and as a regression when the label holds one too.
    """
    by_id = {t.ticket_id: t for t in tickets}
    rows = []
    for run in runs:
        ticket = by_id[run.ticket_id]
        for seq, old, new in run.detail.get("reread", ()):
            old, new = (_items(decode_proposals(p)) for p in (old, new))
            gold = _items(ticket.proposals[seq - 1])
            lost = _lost(old, new, "promise") + _lost(old, new, "fact")
            rows.append({
                "ticket": run.ticket_id, "seq": seq, "text": ticket.events[seq - 1].text,
                "gold": gold, "old": old, "new": new, "lost": lost,
                # Lost, and the label holds a promise of that side or a fact of that slot.
                "regressions": [i for i in lost if i.split(":")[0] in _kinds(gold)],
            })  # fmt: skip
    return rows


def _kinds(items: list[str]) -> set[str]:
    return {i.split(":")[0] for i in items}


def _items(proposals: Sequence[Proposal]) -> list[str]:
    """Each proposal as one line: its kind, whose or which slot, and its words."""
    out = []
    for p in proposals:
        match p:
            case MakeCommitment():
                out.append(f"promise {p.owner.side.value}: {p.action}")
            case AssertFact():
                out.append(f"fact {DEFAULT_VOCABULARY.normalise(p.slot)}: {p.value}")
            case MakeConditional():
                out.append(f"offer {p.owner.side.value}: {p.action} | {p.condition}")
            case SettleConditional():
                out.append(f"condition {'holds' if p.holds else 'failed'}: {p.quote}")
            case _:
                out.append(f"{type(p).__name__}: {p.quote}")
    return out


def _lost(old: list[str], new: list[str], kind: str) -> list[str]:
    """Items of ``kind`` whose side or slot the new read no longer has as often."""
    def keys(items):
        return Counter(i.split(":")[0] for i in items if i.startswith(kind))

    missing = keys(old) - keys(new)
    return [i for i in old if i.split(":")[0] in missing]


def transcript_chars(ticket: SimTicket, cal: WorkCalendar, asked=checkpoints) -> list[int]:
    """The length of the transcript a full-context answer reads, at each moment it is asked."""
    ends, total = {}, -1
    for e in ticket.events:
        total += len(render_event(e, cal)) + 1
        ends[e.seq] = total
    return [ends[cp.seq] for cp in asked(ticket)]


# ------------------------------------------------- the proposals replayed ----


def refolded(run: MethodRun, ticket: SimTicket, cal: WorkCalendar, name: str) -> MethodRun:
    """The event memory's own proposals, folded again under the rules ``REFOLDS[name]``.

    No model is asked: the proposals, and their verdicts, are the ones the run
    recorded. Without the verifier the run costs what its extraction cost.
    """
    proposals = [decode_proposals(p) for p in run.detail.get("proposals", [])]
    folded = refold(ticket, proposals, REFOLDS[name], cal, recorded_checks=_checks(run))
    usage, seconds = run.usage, run.ingest_seconds
    if name == "event_memory_no_verifier" and "verifier" in run.detail:
        usage = Usage(**run.detail["extraction_usage"])
        seconds -= run.detail["verifier"]["seconds"]
    detail = {"replayed_from": run.method,
              "refused": [[r.seq, r.reason, r.kind, r.target_seq] for r in folded.refused]}
    return replace(run, method=name, snapshots=snapshots(ticket, folded), usage=usage,
                   ingest_usage=usage, ingest_seconds=seconds, detail=detail)  # fmt: skip


def _checks(run: MethodRun) -> tuple[tuple[PromiseCheck, tuple[Proposal, ...]], ...]:
    return tuple((decode_event(e), decode_proposals(ps))
                 for e, ps in run.detail.get("check_events", ()))


def recorded_proposals(path: Path, tickets: Sequence[SimTicket] = ()) -> dict[str, Recorded]:
    """Per ticket: what a recorded ``event_memory`` run proposed, and what that cost.

    The path ``oracle`` stands for the proposals planted in ``tickets``.
    """
    if path == ORACLE:
        return {t.ticket_id: Recorded(t.proposals) for t in tickets}
    out = {}
    for run in load_runs(path):
        if run.method != "event_memory":
            continue
        verifier = run.detail.get("verifier") or {}
        extraction = run.detail.get("extraction_usage")
        out[run.ticket_id] = Recorded(
            tuple(decode_proposals(p) for p in run.detail["proposals"]),
            Usage(**extraction) if extraction else run.usage,
            Usage(**verifier["usage"]) if verifier else Usage(),
            run.ingest_seconds - verifier.get("seconds", 0.0), verifier.get("seconds", 0.0),
            run.detail.get("providers", {}), _checks(run),
        )  # fmt: skip
    return out


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
    kinds = dict.fromkeys(PREVENTED.values(), 0)
    reasons: dict[str, dict[str, int]] = {}
    examples = []
    for run in runs:
        ticket = by_id[run.ticket_id]
        for seq, reason, kind, target in run.detail.get("refused", []):
            verdict = PREVENTED[_truth(ticket, target, seq)]
            kinds[verdict] += 1
            reasons.setdefault(reason, {}).setdefault(verdict, 0)
            reasons[reason][verdict] += 1
            if len(examples) < 40:
                said = ticket.events[seq - 1]
                examples.append({"ticket": ticket.ticket_id, "seq": seq, "reason": reason,
                                 "kind": kind, "target_seq": target, "verdict": verdict,
                                 "text": getattr(said, "text", "")})  # fmt: skip
    return {"verdicts": kinds, "by_reason": reasons, "examples": examples}


def audit(ticket: SimTicket, proposals: Sequence[Sequence[Proposal]],
          cal: WorkCalendar) -> list[dict[str, Any]]:  # fmt: skip
    """Every verified proposal, with its verdict, what was admitted, and the truth.

    ``admitted`` is what happened in the run; ``without_verdict`` what the
    same proposal on the same card would have done with verdicts ignored.
    ``truth`` is the label: "true" when this message is the one that closed
    the target, "false" when the target was still owed, "wrong_target" when
    it was closed by another message, "unplanted" for a promise no label has.
    """
    card, out, turns = Card(ticket.ticket_id), [], {}
    for event, ps in zip(ticket.events, proposals, strict=False):
        previous = turns.get(other_side(event))
        after = apply(card, event, ps, cal, previous=previous).card
        for p in ps:
            if isinstance(p, MakeCommitment) and p.restates is not None:
                continue
            v = getattr(p, "verification", None)
            target = card.commitment(target_of(p) or "")
            if v is None or target is None:
                continue
            alone = apply(card, event, [p], cal, guards=Guards(verifier=False),
                          previous=previous).card
            truth = _truth(ticket, target.source.seq, event.seq)
            out.append({
                "ticket": ticket.ticket_id, "seq": event.seq, "kind": type(p).__name__,
                "target_seq": target.source.seq, "verdict": v.verdict.value,
                "evidence": v.evidence_quote, "error": v.error,
                "admitted": _closed(target, after), "without_verdict": _closed(target, alone),
                "truth": truth, "text": getattr(event, "text", ""),
            })  # fmt: skip
        card = after
        remember_turn(turns, event)
    return out


#: A refused close, by what the labels say it would have been.
PREVENTED = {"false": "false_close_prevented", "true": "true_close_blocked",
             "wrong_target": "wrong_target_prevented", "unplanted": "not_a_planted_promise"}


def _truth(ticket: SimTicket, target_seq: int, seq: int) -> str:
    """What closing the promise made at ``target_seq`` by message ``seq`` is, by the labels."""
    g = next((c for c in ticket.gold.commitments if c.seq == target_seq), None)
    if g is None:
        return "unplanted"
    if not g.closed(seq):
        return "false"
    return "true" if g.resolved_seq == seq else "wrong_target"


def false_notice_audit(ticket: SimTicket, run: MethodRun, cal: WorkCalendar) -> list[dict]:
    """Judge each notice at its first eligibility, including clock wakeups."""
    policy = AlertPolicy()
    proposals = [decode_proposals(row) for row in run.detail["proposals"]]
    events = ticket.events[:len(proposals)]
    reader = Replayed({e.id: p for e, p in zip(events, proposals, strict=True)})
    frames = []
    fold(ticket, reader, (), cal, recorded_checks=_checks(run), frames=frames)
    repeats = {b.seq for b in ticket.gold.beats if b.kind is Beat.RESTATEMENT}
    kinds = {NoticeKind.DUE_SOON, NoticeKind.BREACHED, NoticeKind.CLAIM_UNCONFIRMED}
    seen, false = set(), []
    for i, (event, card) in enumerate(frames[:len(events) + len(_checks(run))]):
        snapshot = from_card(card, Checkpoint(event.seq, event.at, Moment.MESSAGE))
        pairs, _, _ = match(ticket.gold.commitments, snapshot.promises)
        labels = {id(p): g for g, p in pairs}
        gold = {c.id: labels.get(id(p)) for c, p in zip(card.commitments, snapshot.promises,
                                                     strict=True)}  # fmt: skip
        until = frames[i + 1][0].at if i + 1 < len(frames) else None
        at = event.at
        while at is not None and (
            at == event.at or (at < until if until is not None else at <= ticket.end)
        ):
            for notice in notices(card, at, policy):
                if notice.kind not in kinds or notice.key in seen:
                    continue
                seen.add(notice.key)
                c = card.commitment(notice.commitment_id)
                seq = c.source.seq
                label = gold[c.id]
                evidence = label.resolved_seq if label and label.closed(event.seq) else None
                if seq in repeats:
                    cause = "D"
                elif label is None:
                    cause = "unplanted"
                elif evidence is not None:
                    ids = {target_of(p) for p in proposals[evidence - 1]} - {None}
                    if c.id in ids:
                        cause = "claim_only" if notice.kind is NoticeKind.CLAIM_UNCONFIRMED else "B"
                    else:
                        cause = "C" if ids else "A"
                elif notice.kind is NoticeKind.CLAIM_UNCONFIRMED:
                    continue
                elif label.due_at is None:
                    cause = "E"
                else:
                    span = label.due_at - ticket.events[seq - 1].at
                    lead = min(policy.due_soon_lead, span * policy.max_lead_fraction)
                    if notice.kind is NoticeKind.BREACHED and at >= label.due_at:
                        continue
                    gold_soon = label.due_at - lead <= at < label.due_at
                    if notice.kind is NoticeKind.DUE_SOON and gold_soon:
                        continue
                    cause = "E"
                false.append({"key": notice.key, "kind": notice.kind, "at": at, "cause": cause,
                              "promise": c.source.event_id, "evidence": f"e{evidence}" if evidence
                              else None})  # fmt: skip
            at = next_wakeup(card, at, policy)
    return false


def _closed(target, card: Card) -> bool:
    now = card.commitment(target.id)
    return now is not None and now.state is not target.state


def verdict_table(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Verdicts against the labels, and what they changed."""
    by: dict[str, dict[str, int]] = {}
    for r in rows:
        by.setdefault(r["verdict"], {}).setdefault(r["truth"], 0)
        by[r["verdict"]][r["truth"]] += 1
    rescued = [r for r in rows if r["admitted"] and not r["without_verdict"]]
    # What a "no" that vetoed would have done to closes the words admitted.
    veto = [r for r in rows if r["without_verdict"] and r["verdict"] == "no"]
    return {
        "calls": len(rows),
        "by_verdict": by,
        "rescued": _truths(rescued),
        "veto_would_block": _truths(veto),
        "quote_not_in_message": sum(1 for r in rows if r["verdict"] == "yes"
                                    and r["evidence"] and not _in(r["evidence"], r["text"])),
        "errors": sum(1 for r in rows if r["error"]),
    }  # fmt: skip


def _truths(rows) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["truth"]] = out.get(r["truth"], 0) + 1
    return out


def _in(evidence: str, text: str) -> bool:
    fold = lambda x: " ".join(x.casefold().replace("ё", "е").split())  # noqa: E731
    return fold(evidence) in fold(text)


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
    # Conditional offers (decision 0004), at every checkpoint.
    "offer_recall": ("all", lambda s, n: s.offers.recall),
    "offer_precision": ("all", lambda s, n: s.offers.precision),
    "offer_state_accuracy": ("all",
                             lambda s, n: s.offer_states_right / max(s.offer_states_judged, 1)),
    "condition_alert_recall": ("all", lambda s, n: s.condition_alerts.recall),
    "false_condition_alerts_per_ticket": ("all", lambda s, n: s.condition_alerts.fp / n),
    # Unconfirmed claims (decision 0006), at the end of each ticket.
    "claim_check_precision": ("end", lambda s, n: s.claim_checks.precision),
    "claim_check_recall": ("end", lambda s, n: s.claim_checks.recall),
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
    close_cases = {case.kind.value for t in tickets for case in t.gold.close_cases}
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
                "end": sum((p.end for p in picked), Score()),
                "handoffs": sum((p.handoffs for p in picked), Score()),
            }
            sums["all"] = sums["end"] + sums["handoffs"]
            out = {k: f(sums[w], len(picked)) for k, (w, f) in METRICS.items()}
            out |= {case.value: getattr(sums["end"], case.value).recall
                    for case in CloseCase if case.value in close_cases}  # fmt: skip
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
        total = sum((scores[m, tid].all for tid in order), Score())
        end = sum((scores[m, tid].end for tid in order), Score())
        rows["counts_end"] = end.as_dict()
        rows["counts_all"] = total.as_dict()
        rows["failures"] = sum(1 for tid in order if runs_of[m, tid].failure)
        rows["usd_total"] = sum(runs_of[m, tid].usage.usd() or 0.0 for tid in order)
        rows["deadline_errors"] = deadline_errors([by_id[t] for t in order],
                                                  [runs_of[m, t] for t in order])  # fmt: skip
        rows |= losses([by_id[t] for t in order], [runs_of[m, t] for t in order])
        summary["methods"][m] = rows
    return summary


def losses(tickets: Sequence[SimTicket], runs: Sequence[MethodRun]) -> dict[str, Any]:
    """Where each gold promise stands at the end, and how Round 8's beats fared."""
    stages: Counter[str] = Counter()
    beats: dict[str, Counter[str]] = {}
    for ticket, run in zip(tickets, runs, strict=True):
        raw = run.detail.get("proposals")
        proposed = [decode_proposals(p) for p in raw] if raw is not None else None
        end = run.snapshots[-1]
        found = attribute(ticket, end, proposed)
        stages.update(stage.value for stage in found.values())
        for kind, counts in beat_outcomes(ticket, end, found).items():
            beats.setdefault(kind, Counter()).update(counts)
    return {"promise_stages": dict(stages), "beats": {k: dict(v) for k, v in beats.items()}}


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


# ------------------------------------------------------- when it knows ----


def timeline(run: MethodRun, ticket: SimTicket, cal: WorkCalendar) -> tuple[Snapshot, ...]:
    """A method's answers in time: the event memory's card after every event, a live
    method's every answer it gave, otherwise its answer at each checkpoint."""
    if "proposals" in run.detail:
        answers = {e.id: tuple(decode_proposals(p))
                   for e, p in zip(ticket.events, run.detail["proposals"], strict=False)}
        frames = []
        fold(ticket, Replayed(answers), (), cal, recorded_checks=_checks(run), frames=frames)
        kinds = {cp.seq: cp.kind for cp in checkpoints(ticket)}
        return tuple(from_card(card, Checkpoint(e.seq, e.at, kinds.get(e.seq, Moment.MESSAGE)))
                     for e, card in frames)  # fmt: skip
    if "live" in run.detail:
        live = (s if isinstance(s, Snapshot) else snapshot_of(s) for s in run.detail["live"])
        return tuple(s for s in live if s.error is None)
    return run.snapshots


def when_known(
    tickets: Sequence[SimTicket], runs: Sequence[MethodRun], cal: WorkCalendar
) -> dict[str, Any]:
    """Per method: promise and alert delays in hours, and how its answers changed."""
    by_id = {t.ticket_id: t for t in tickets}
    out: dict[str, Any] = {}
    for name in dict.fromkeys(r.method for r in runs):
        promise, alert, steady, n = [], [], Stability(), 0
        for r in (r for r in runs if r.method == name):
            ticket = by_id[r.ticket_id]
            line = timeline(r, ticket, cal)
            found = timeliness(ticket, line)
            promise += found.promise_delays
            alert += found.alert_delays
            steady += stability(ticket, line)
            n += 1
        out[name] = {"tickets": n, "promise_delay_hours": _spread(promise),
                     "alert_delay_hours": _spread(alert), "stability": asdict(steady)}  # fmt: skip
    return out


def _spread(delays: Sequence[timedelta | None]) -> dict[str, Any]:
    """Median and p90 in hours, "never" counted as the longest; None where it falls on never."""
    hours = sorted(float("inf") if d is None else d.total_seconds() / 3600 for d in delays)

    def rank(q: float) -> float | None:
        x = hours[max(math.ceil(q * len(hours)) - 1, 0)] if hours else float("inf")
        return None if x == float("inf") else round(x, 3)

    never = sum(d is None for d in delays)
    return {"n": len(hours), "median": rank(0.5), "p90": rank(0.9), "never": never,
            "never_share": never / len(hours) if hours else 0.0,
            "early": sum(1 for h in hours if h < 0)}  # fmt: skip


def regimes(
    tickets: Sequence[SimTicket], runs: Sequence[MethodRun], cal: WorkCalendar
) -> dict[str, Any]:
    """Per method, dollars per ticket answering only at checkpoints and answering live.

    Live is measured for the event memory (it reads every message anyway) and
    for ``full_context_live``. Full context on other tickets is projected from
    the live tickets' dollars per character of transcript read; a memory that
    ingests every message, by its ingest cost and one answer per message at its
    recorded cost per answer.
    """
    by_id = {t.ticket_id: t for t in tickets}
    live_fc = [r for r in runs if r.method == "full_context_live"]
    units = {t.ticket_id: sum(transcript_chars(t, cal, moments)) for t in tickets}
    rate = (sum(r.usage.usd() or 0.0 for r in live_fc) / sum(units[r.ticket_id] for r in live_fc)
            if live_fc else None)  # fmt: skip
    measured = {r.ticket_id: r.usage.usd() or 0.0 for r in live_fc}
    out: dict[str, Any] = {}
    for name in dict.fromkeys(r.method for r in runs):
        own = [r for r in runs if r.method == name]
        messages = sum(by_id[r.ticket_id].messages for r in own)
        recorded = [r.usage.usd() or 0.0 for r in own]
        row: dict[str, Any] = {"tickets": len(own), "messages": messages}
        if name.startswith("event_memory"):
            row |= {"checkpoints_usd": recorded, "live_usd": recorded, "live": "measured"}
        elif name == "full_context_live":
            row |= {"live_usd": recorded, "live": "measured"} | _calls(own)
        elif name == "full_context":
            row |= {"checkpoints_usd": recorded, "live": "measured where run live, else projected",
                    "live_usd": [measured.get(r.ticket_id, rate * units[r.ticket_id])
                                 if rate else None for r in own]}  # fmt: skip
        else:
            row |= {"checkpoints_usd": recorded, "live": "projected",
                    "live_usd": [(r.ingest_usage.usd() or 0.0) + len(moments(by_id[r.ticket_id]))
                                 * (r.answer_usage.usd() or 0.0) / max(r.answer_usage.calls, 1)
                                 for r in own]}  # fmt: skip
        for key in ("checkpoints_usd", "live_usd"):
            if key in row and None not in row[key]:
                total = sum(row[key])
                row[key] = {"per_ticket": total / len(own), "per_message": total / messages}
            elif key in row:
                row[key] = None
        out[name] = row
    return out


def _calls(runs: Sequence[MethodRun]) -> dict[str, Any]:
    """What a live method's calls took: tokens, cache share and seconds per call."""
    calls = [c for r in runs for c in r.detail.get("calls", ())]
    seconds = sorted(c[4] for c in calls)
    usage = sum((r.usage for r in runs), Usage())
    return {"calls_per_ticket": len(calls) / len(runs),
            "prompt_tokens_per_call": usage.prompt_tokens / max(usage.calls, 1),
            "completion_tokens_per_call": usage.completion_tokens / max(usage.calls, 1),
            "cache_share": usage.cache_share,
            "seconds_per_call": {"median": seconds[len(seconds) // 2] if seconds else None,
                                 "p90": seconds[int(0.9 * (len(seconds) - 1))] if seconds
                                 else None}}  # fmt: skip


def _by_call_kind(runs: Sequence[MethodRun]) -> dict[str, Any]:
    table = {}
    calls = [c for r in runs for c in r.detail.get("model_calls", ())]
    for kind in ("read", "escalate", "verify", "reconcile_identity", "reconcile_deadline",
                 "reconcile_handoff"):
        rows = [c for c in calls if c["kind"] == kind]
        if not rows and not calls:
            continue
        useful = sum(c["useful"] is True for c in rows)
        table[kind] = {
            "calls": len(rows),
            "response_cache_hits": sum(c["cache_hit"] for c in rows),
            **{k: sum(c[k] for c in rows) for k in ("input", "cached_input", "output")},
            "usd": sum(c["usd"] or 0 for c in rows),
            "charged_usd": sum(c["usd"] or 0 for c in rows if not c["cache_hit"]),
            "mean_seconds": sum(c["seconds"] for c in rows) / len(rows) if rows else None,
            "useful": useful if kind.startswith("reconcile_") else None,
            "useful_rate": useful / len(rows) if rows and kind.startswith("reconcile_") else None,
        }
    return table


def _interval(draws: list[float]) -> list[float]:
    ordered = sorted(draws)
    lo = ordered[int(0.025 * (len(ordered) - 1))]
    hi = ordered[int(round(0.975 * (len(ordered) - 1)))]
    return [lo, hi]


# ------------------------------------------------------------ the record ----


def to_json(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name)) for f in serialized_fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(v) for v in value]
    return value


def compact(record: dict) -> str:
    """A record as JSON with one top-level key per line, and one line per item of a list or
    mapping under it (a run, a ticket): a new record is a few thousand lines, not 80,000."""

    def line(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def block(value: Any) -> str:
        if isinstance(value, list) and value:
            return "[\n" + ",\n".join(line(v) for v in value) + "\n]"
        if isinstance(value, dict) and value:
            return "{\n" + ",\n".join(f"{line(k)}:{line(v)}" for k, v in value.items()) + "\n}"
        return line(value)

    return "{\n" + ",\n".join(f"{line(k)}:{block(v)}" for k, v in record.items()) + "\n}\n"


def saved(run: MethodRun) -> dict[str, Any]:
    """A run as plain JSON: every answer, what it cost, how long it took."""
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
        "snapshots": to_json(run.snapshots),
        "detail": to_json(run.detail),
    }


def record(run: MethodRun, scored: TicketScore) -> dict[str, Any]:
    """A run for the committed record, with its score.

    Raw model answers are left out (a snapshot is their parsed form), and so
    is what a baseline showed the model at each checkpoint, except at the end
    of the ticket: what the memory held when it was last asked.
    """
    out = saved(run)
    detail = {k: v for k, v in out["detail"].items() if k not in ("answers", "checkpoints")}
    if shown := out["detail"].get("checkpoints"):
        detail["end"] = {k: v for k, v in shown[-1].items() if k != "answer"}
    return out | {"detail": detail, "score_end": scored.end.as_dict(),
                  "score_handoffs": scored.handoffs.as_dict()}  # fmt: skip


def load_runs(path: Path | None) -> list[MethodRun]:
    """The runs of a record (``runs`` of a result, or a journal of saved runs)."""
    if path is None or not path.exists():
        return []
    text = path.read_text()
    raws = (json.loads(text)["runs"] if path.suffix == ".json"
            else [json.loads(line) for line in text.splitlines() if line])  # fmt: skip
    return [run_of(r) for r in raws]


def run_of(raw: dict[str, Any]) -> MethodRun:
    """A saved run read back: its snapshots can be scored again without a model."""
    return MethodRun(raw["method"], raw["ticket"],
                     tuple(snapshot_of(s) for s in raw["snapshots"]), Usage(**raw["usage"]),
                     raw["ingest_seconds"], raw["answer_seconds"], Usage(**raw["ingest_usage"]),
                     Usage(**raw["answer_usage"]), raw["failure"], raw["detail"])  # fmt: skip


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
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "real":
        from .real import main as real_main

        real_main(argv[1:])
        return
    parser = argparse.ArgumentParser(prog="python -m duebook.bench.experiment")
    parser.add_argument("--split", choices=sorted(SPLITS), default="dev2")
    parser.add_argument("--methods", default=",".join(METHODS),
                        help=f"of {', '.join([*METHODS, *EVENT_MEMORY])}; empty to run none")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="the extracting model")
    parser.add_argument("--reconcile-reasoning", choices=REASONING,
                        help="override the reconciler preset on the development set")
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
    parser.add_argument("--replay", type=Path,
                        help="a result whose event_memory proposals event_memory folds again, "
                             "or 'oracle' for the planted proposals")
    parser.add_argument("--reread", type=Path,
                        help="with --replay: a JSON file {ticket: [seq, ...]} of messages read "
                             "again live, against the card the replay rebuilds")
    parser.add_argument("--frozen", type=Path, action="append", default=[],
                        help="a result whose runs of the methods not run now are scored again; "
                             "may be given more than once, the first holding a run wins")
    parser.add_argument("--sample-events", type=int, default=30,
                        help="events of the first ticket read to project the cost")
    parser.add_argument("--projection-from", type=Path,
                        help="project from a completed development run with the same method")
    parser.add_argument("--expected", action="store_true",
                        help="judge the projection by what the sample was charged (trusting the "
                             "response cache) instead of what it would cost uncached")
    parser.add_argument("--no-cache", action="store_true", help="do not use the response cache")
    parser.add_argument("--rewrite-only", action="store_true",
                        help="rewrite and save the split's tickets, then stop")
    parser.add_argument("--project-only", action="store_true",
                        help="print the cost projection (its sample is charged), then stop")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--template", action="store_true", help="use original development tickets")
    parser.add_argument("--compare-frozen-event-memory", action="store_true",
                        help="score the original event memory as event_memory_previous")
    parser.add_argument("--out", type=Path, help="default: <results>/<split>.json")
    parser.add_argument("--label", default="", help="a note for the spend log")
    parser.add_argument("--results", type=Path,
                        help="defaults to the results directory for the split's round")
    args = parser.parse_args(argv)  # fmt: skip

    require_different_models(args.writer, args.model)
    split = SPLITS[args.split]
    if args.template and split.name.startswith("test"):
        parser.error("--template is only for development splits")
    results = args.results or split.results
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
    names = [m for m in args.methods.split(",") if m]

    def spend(what: str) -> None:
        results.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(json.dumps({"at": started.isoformat(), "split": split.name, "what": what,
                                "methods": args.methods, "label": args.label,
                                "usd": ledger.usd(),
                                "reused_usd": ledger.reused.usd() or 0.0}) + "\n")  # fmt: skip

    try:
        where = split.results / "tickets"
        if args.template:
            tickets = split.tickets()
            written = {"tickets": {t.ticket_id: {"discarded": 0} for t in tickets}}
        else:
            tickets, written = rewritten(split, args.writer, ledger, where, args.workers)
    except BaseException as error:
        spend(f"rewriting, stopped: {type(error).__name__}")
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

    recorded = recorded_proposals(args.replay, tickets) if args.replay else None
    reread = None
    if args.reread:
        listed = json.loads(args.reread.read_text())["tickets"]
        reread = {t: frozenset(seqs) for t, seqs in listed.items()}
        tickets = [t for t in tickets if t.ticket_id in reread]
    methods = make_methods(names, args.model, ledger, cal, cache, recorded, reread,
                           **({"reconcile_reasoning": args.reconcile_reasoning}
                              if args.reconcile_reasoning else {}))
    if reread is not None:
        calls = sum(len(reread[t.ticket_id]) for t in tickets)
        projected = project_reread(args.projection_from or args.replay, calls)
    elif args.projection_from:
        projected = project_from(args.projection_from, methods, tickets, subsets)
    else:
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

    journal = JOURNAL / f"{str(out).replace('/', '_')}.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    try:
        runs = run_all(methods, tickets, args.workers, subsets, journal)
    finally:
        spend("run")
    by_id = {t.ticket_id: t for t in tickets}
    held: dict[tuple[str, str], MethodRun] = {}
    for path in args.frozen:
        for r in load_runs(path):
            if r.ticket_id in by_id:
                held.setdefault((r.method, r.ticket_id), r)
    frozen = [r for (m, _), r in held.items() if m not in names]
    if args.compare_frozen_event_memory:
        frozen += [replace(r, method="event_memory_previous") for (m, _), r in held.items()
                   if m == "event_memory"]
    runs += frozen
    reference = [r for r in runs if r.method == "event_memory"]
    replays = [refolded(r, by_id[r.ticket_id], cal, name) for name in REFOLDS for r in reference]
    summary = summarise(tickets, runs + replays)
    effects = guard_effects(tickets, reference)
    verified = [
        row
        for r in reference
        for row in audit(by_id[r.ticket_id], [decode_proposals(p) for p in r.detail["proposals"]],
                         cal)  # fmt: skip
    ]
    result = {
        "split": split.name,
        "seed": split.seed,
        "profile": split.profile.name,
        "model": args.model,
        "writer": args.writer,
        "template": args.template,
        "methods": {m: EVENT_MEMORY.get(m) for m in names},
        "replayed_from": str(args.replay) if args.replay else None,
        "frozen_from": [str(p) for p in args.frozen] or None,
        "started": started.isoformat(),
        "finished": datetime.now().astimezone().isoformat(),
        "subsets": {m: n for m, n in subsets.items() if m in names}
                   | {r.method: summary["methods"][r.method]["tickets"] for r in frozen
                      if summary["methods"][r.method]["tickets"] < len(tickets)},
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
        ],
        "spent_usd": {"writing": writing, "total": ledger.usd(),
                      "reused_from_cache": ledger.reused.usd() or 0.0},
        "projection": projected,
        "projection_from": str(args.projection_from) if args.projection_from else None,
        "guard_effects": effects,
        "verifier": verdict_table(verified) | {"rows": verified},
        "reread": reread_changes(tickets, reference) if reread is not None else None,
        "summary": summary,
        "timeliness": when_known(tickets, runs, cal),
        "regimes": regimes(tickets, runs, cal),
        # A frozen run stays in the record it came from.
        "runs": [record(r, score_ticket(by_id[r.ticket_id], r.snapshots)) for r in runs
                 if id(r) not in {id(f) for f in frozen}],
    }  # fmt: skip
    by_kind = {name: _by_call_kind([r for r in runs if r.method == name]) for name in names}
    if any(by_kind.values()):
        result["model_calls_by_kind"] = by_kind
    if split.profile.implicit or any(_checks(r) for r in reference):
        result["false_notices"] = {r.ticket_id: false_notice_audit(by_id[r.ticket_id], r, cal)
                                   for r in reference}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(compact(to_json(result)))
    journal.unlink(missing_ok=True)
    keys = ["commitment_f1", "deadline_accuracy", "status_accuracy", "breach_alert_recall",
            "false_alerts_per_ticket", "false_closes_per_ticket",
            "wrong_target_closes_per_ticket", "missed_close_rate", "suggestions_per_ticket",
            "freshness_error", "dispute_recall", "usd_per_ticket"]  # fmt: skip
    keys.extend(case.value for case in CloseCase
                if case.value in summary["methods"][summary["reference"]])  # fmt: skip
    print(table(summary, keys))
    for name, rows in by_kind.items():
        if rows:
            print(f"\n{name} model calls")
            print("| Kind | Calls | Disk hits | Input | Cached input | Output | "
                  "USD | Mean s | Useful |")
            print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
            for kind, row in rows.items():
                rate = "—" if row["useful_rate"] is None else f"{row['useful_rate']:.1%}"
                print(f"| {kind} | {row['calls']} | {row['response_cache_hits']} | "
                      f"{row['input']} | "
                      f"{row['cached_input']} | {row['output']} | ${row['usd']:.4f} | "
                      f"{row['mean_seconds'] or 0:.2f} | {rate} |")
    for m, rows in summary["methods"].items():
        print(f"{m}: promises {rows['promise_stages']}; beats {rows['beats']}", file=sys.stderr)
    print(f"guards: {effects['verdicts']}", file=sys.stderr)
    print(json.dumps({k: v for k, v in result["verifier"].items() if k != "rows"},
                     ensure_ascii=False), file=sys.stderr)  # fmt: skip
    print(f"\ncharged this run: ${ledger.usd():.4f} (+${ledger.reused.usd() or 0:.4f} reused "
          f"from the cache); written to {out}", file=sys.stderr)  # fmt: skip


if __name__ == "__main__":
    main()
