"""Round 3 (dev only): a verifier on destructive changes, and a reasoning extractor.

    python -m duebook.bench.round3 --max-usd 0.9
    python -m duebook.bench.round3 --configs C,B --max-usd 0.05   # the replays only

Three configurations of the event memory on the dev2 split, nothing else:

* **A** — extractor reasoning LOW, deterministic guards, no verifier. New
  extraction calls: the only configuration that pays for reading.
* **B** — extractor reasoning OFF, guards, and the verifier (reasoning low) on
  every proposal that would close, cancel or replace an open commitment.
* **C** — extractor reasoning OFF, guards, no verifier: round 2's frozen
  configuration.

C and the extraction half of B are not run again. Round 2 recorded every
proposal of its frozen dev run (``results/round2/dev2-4.json``), so C is that
record folded through today's reducer, and B is the same record with the
verifier asked about each destructive proposal as the fold reaches it — B pays
for verifier calls only. Their extraction cost is what round 2 was charged
for it. One approximation follows, and is disclosed: in B a close the verifier
admits changes the card that LATER recorded proposals were made against; a
live B run would have shown the extractor that card.

Money: every live call is charged to ``results/round3/spend.jsonl`` and the
whole round may cost ``SESSION_CAP``. Configurations run in the order given
(cheapest first by default), and before each that makes calls the cost is
projected from a real sample; one that would not fit is not started. Answers
are cached on disk, so a re-run costs nothing.

Nothing here touches a test split.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..card import Card
from ..extract.client import DEFAULT_MODEL, ChatClient, ResponseCache
from ..extract.cost import Usage
from ..extract.extractor import JsonModel
from ..extract.verifier import target_of, verifying_reader
from ..proposals import Proposal
from ..reducer import Guards, apply
from ..store.codec import decode_proposals, encode
from ..temporal import WorkCalendar
from .experiment import (
    CACHE,
    ROUND2,
    ROUND3,
    WRITER,
    guard_effects,
    project,
    record,
    rewritten,
    spent_before,
    summarise,
    table,
    to_json,
)
from .generate import SPLITS, SimTicket
from .meter import Ledger, Meter, OverBudget
from .methods import EventMemory, MethodRun
from .result import checkpoints
from .run import Replayed, fold, refold, snapshots
from .score import score_ticket

#: Round 2's frozen dev run: the proposals C and B replay.
RECORDED = ROUND2 / "dev2-4.json"
#: What every live model call of round 3 may cost together.
SESSION_CAP = 1.00
CONFIGS = {
    "C": "extractor reasoning off + guards (round 2, replayed)",
    "B": "extractor reasoning off + guards + verifier, reasoning low (replayed + verifier)",
    "A": "extractor reasoning low + guards, no verifier (new extraction)",
}


# ------------------------------------------------------- the record ----


def recorded(path: Path = RECORDED) -> dict[str, dict[str, Any]]:
    """Per ticket: the proposals round 2's frozen run made, and what they cost."""
    raw = json.loads(path.read_text())
    out = {}
    for run in raw["runs"]:
        if run["method"] != "event_memory":
            continue
        detail = run["detail"]
        out[run["ticket"]] = {
            "proposals": [decode_proposals(p) for p in detail["proposals"]],
            "usage": Usage(**run["usage"]),
            "seconds": run["ingest_seconds"],
            "providers": detail.get("providers", {}),
            "failure": run["failure"],
            "source": str(path),
        }
    return out


def target_quotes(rec: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Do the recorded destructive proposals carry what B needs (a target_quote)?"""
    found = [p for r in rec.values() for ps in r["proposals"] for p in ps if target_of(p)]
    empty = sum(1 for p in found if not p.target_quote.strip())
    return {"destructive": len(found), "without_target_quote": empty}


# ------------------------------------------------------ the configurations ----


def replay_c(ticket: SimTicket, rec: dict[str, Any], cal: WorkCalendar) -> MethodRun:
    """Round 2's proposals through today's reducer: no model call."""
    folded = refold(ticket, rec["proposals"], Guards(), cal)
    return _run("C", ticket, folded, rec["usage"], rec["seconds"],
                {"providers": rec["providers"], "replayed_from": rec.get("source")})  # fmt: skip


def replay_b(
    ticket: SimTicket, rec: dict[str, Any], client: JsonModel, ledger: Ledger,
    cal: WorkCalendar,
) -> MethodRun:  # fmt: skip
    """Round 2's proposals, each destructive one put to the verifier as the fold reaches it."""
    answers = {e.id: tuple(p) for e, p in zip(ticket.events, rec["proposals"], strict=False)}
    reader = verifying_reader(Replayed(answers), client, cal, wrap_model=lambda m: Meter(m, ledger))
    meter = reader.verifier.model
    assert isinstance(meter, Meter)
    folded = fold(ticket, reader, [cp.seq for cp in checkpoints(ticket)], cal)
    spent = meter.tally.usage
    detail = {
        "providers": rec["providers"],
        "replayed_from": rec.get("source"),
        "verifier": {"model": meter.model.model,
                     "reasoning": getattr(meter.model, "reasoning", None),
                     "usage": asdict(spent), "usd": spent.usd(),
                     "providers": meter.tally.providers,
                     "answered_from_cache": meter.tally.cache_hits},  # fmt: skip
    }
    run = _run("B", ticket, folded, rec["usage"] + spent, rec["seconds"], detail)
    return replace(run, ingest_seconds=run.ingest_seconds + meter.tally.ingest_seconds)


def run_a(ticket: SimTicket, reader: ChatClient, ledger: Ledger, cal: WorkCalendar) -> MethodRun:
    return EventMemory(reader, ledger, cal, name="A").run(ticket)


def _run(name, ticket, folded, usage: Usage, seconds: float, extra: dict) -> MethodRun:
    detail = {
        "proposals": [encode(list(p)) for p in folded.proposals],
        "refused": [[r.seq, r.reason, r.kind, r.target_seq] for r in folded.refused],
        **extra,
    }
    failure = folded.failure[2] if folded.failure else None
    return MethodRun(name, ticket.ticket_id, snapshots(ticket, folded), usage, seconds, 0.0,
                     usage, Usage(), failure, detail)  # fmt: skip


# ------------------------------------------------ what the verifier did ----


def audit(ticket: SimTicket, proposals: Sequence[Sequence[Proposal]],
          cal: WorkCalendar) -> list[dict[str, Any]]:  # fmt: skip
    """Every verified proposal, with its verdict, what was admitted, and the truth.

    ``admitted`` is what happened in the run; ``without_verdict`` what the
    same proposal on the same card would have done with verdicts ignored.
    ``truth`` is the label: "true" when this message is the one that closed
    the target, "false" when the target was still owed, "wrong_target" when
    it was closed by another message, "unplanted" for a promise no label has.
    """
    gold = {c.seq: c for c in ticket.gold.commitments}
    card, out = Card(ticket.ticket_id), []
    for event, ps in zip(ticket.events, proposals, strict=False):
        after = apply(card, event, ps, cal).card
        for p in ps:
            v = getattr(p, "verification", None)
            target = card.commitment(target_of(p) or "")
            if v is None or target is None:
                continue
            alone = apply(card, event, [p], cal, guards=Guards(verifier=False)).card
            g = gold.get(target.source.seq)
            if g is None:
                truth = "unplanted"
            elif not g.closed(event.seq):
                truth = "false"
            elif g.resolved_seq == event.seq:
                truth = "true"
            else:
                truth = "wrong_target"
            out.append({
                "ticket": ticket.ticket_id, "seq": event.seq, "kind": type(p).__name__,
                "target_seq": target.source.seq, "verdict": v.verdict.value,
                "evidence": v.evidence_quote, "error": v.error,
                "admitted": _closed(target, after), "without_verdict": _closed(target, alone),
                "truth": truth, "text": getattr(event, "text", ""),
            })  # fmt: skip
        card = after
    return out


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


# ------------------------------------------------------------------ CLI ----


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m duebook.bench.round3")
    parser.add_argument("--configs", default="C,B,A", help="in the order to run them")
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--session-cap", type=float, default=SESSION_CAP)
    parser.add_argument("--sample-events", type=int, default=30)
    parser.add_argument("--project-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, default=ROUND3 / "dev2.json")
    parser.add_argument("--label", default="")
    parser.add_argument("--recorded", type=Path, default=RECORDED,
                        help="the round-2 dev run whose proposals C and B replay")
    args = parser.parse_args(argv)

    configs = [c for c in args.configs.split(",") if c]
    if unknown := set(configs) - set(CONFIGS):
        raise SystemExit(f"unknown configurations: {sorted(unknown)}")
    log = ROUND3 / "spend.jsonl"
    left = args.session_cap - spent_before(log)
    if left <= 0:
        raise SystemExit(f"the session cap of ${args.session_cap:.2f} is spent ({log})")
    ledger = Ledger(cap_usd=min(args.max_usd, left))
    cal = WorkCalendar()
    started = datetime.now().astimezone()
    cache = ResponseCache(CACHE)
    split = SPLITS["dev2"]
    tickets, _ = rewritten(split, WRITER, ledger, ROUND2 / "tickets")  # cached: no call
    rec = recorded(args.recorded)
    needs = target_quotes(rec)
    print(f"recorded round-2 proposals: {needs}", file=sys.stderr)

    def spend(what: str) -> None:
        ROUND3.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(json.dumps({"at": started.isoformat(), "split": split.name, "what": what,
                                "configs": ",".join(configs), "label": args.label,
                                "usd": ledger.usd(),
                                "reused_usd": ledger.reused.usd() or 0.0}) + "\n")  # fmt: skip

    reader_low = ChatClient(api_key="proxy-injected", reasoning="low", cache=cache, timeout=120)
    runs: list[MethodRun] = []
    projections: dict[str, Any] = {}
    try:
        for name in configs:
            before = ledger.usd()
            if name == "C":
                runs += [replay_c(t, rec[t.ticket_id], cal) for t in tickets]
            elif name == "B":
                calls = needs["destructive"]
                print(f"  B: at most {calls} verifier calls; room ${ledger.cap_usd - before:.4f}",
                      file=sys.stderr)  # fmt: skip
                runs += [replay_b(t, rec[t.ticket_id], reader_low, ledger, cal) for t in tickets]
            else:
                method = EventMemory(reader_low, ledger, cal, name="A")
                p = project([method], tickets, {}, ledger, cal, args.sample_events)["A"]
                room = ledger.cap_usd - ledger.usd()
                projections["A"] = p | {"room": room}
                print(f"  projected A: upper ${p['upper']:.4f}, expected ${p['expected']:.4f} "
                      f"(sample of {args.sample_events} events charged "
                      f"${p['sample_charged']:.4f}); room ${room:.4f}",
                      file=sys.stderr)  # fmt: skip
                if args.project_only:
                    continue
                if p["upper"] > room:
                    print("  A does not fit: not run", file=sys.stderr)
                    continue
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(args.workers) as pool:
                    runs += list(pool.map(lambda t: run_a(t, reader_low, ledger, cal), tickets))
            print(f"  {name}: charged ${ledger.usd() - before:.4f}", file=sys.stderr)
    except OverBudget:
        spend("stopped at the cap")
        raise
    if args.project_only:
        spend("cost projection only")
        return

    by_id = {t.ticket_id: t for t in tickets}
    summary = summarise(tickets, runs)
    verified = [
        row
        for r in runs
        if r.method == "B"
        for row in audit(by_id[r.ticket_id], [decode_proposals(p) for p in r.detail["proposals"]],
                         cal)  # fmt: skip
    ]
    result = {
        "split": split.name, "seed": split.seed, "model": DEFAULT_MODEL,
        "configs": {k: CONFIGS[k] for k in configs},
        "started": started.isoformat(), "finished": datetime.now().astimezone().isoformat(),
        "recorded_from": str(args.recorded), "recorded_target_quotes": needs,
        "spent_usd": {"total": ledger.usd(), "reused_from_cache": ledger.reused.usd() or 0.0},
        "projection": projections,
        "guard_effects": {c: guard_effects(tickets, [r for r in runs if r.method == c])
                          for c in configs},
        "verifier": verdict_table(verified) | {"rows": verified},
        "prompt_cache": {c: _pooled([r for r in runs if r.method == c]) for c in configs},
        "summary": summary,
        "runs": [record(r, score_ticket(by_id[r.ticket_id], r.snapshots)) for r in runs],
    }  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(to_json(result), ensure_ascii=False, indent=1))
    spend("run")
    keys = ["false_closes_per_ticket", "false_closes_any_checkpoint",
            "wrong_target_closes_per_ticket", "missed_close_rate", "suggestions_per_ticket",
            "commitment_f1", "deadline_accuracy", "fact_recall", "usd_per_ticket",
            "usd_per_message", "prompt_cache_share"]  # fmt: skip
    print(table(summary, keys))
    print(json.dumps({k: v for k, v in result["verifier"].items() if k != "rows"},
                     ensure_ascii=False), file=sys.stderr)  # fmt: skip
    print(f"charged this run ${ledger.usd():.4f}; written to {args.out}", file=sys.stderr)


def _pooled(runs: Sequence[MethodRun]) -> dict[str, float]:
    """Prompt tokens served from the provider's cache, over all calls of a configuration."""
    total = Usage()
    for r in runs:
        total += r.usage
    return {"prompt_tokens": total.prompt_tokens, "cached_tokens": total.cached_tokens,
            "share": total.cache_share, "calls": total.calls,
            "reasoning_tokens": total.reasoning_tokens}  # fmt: skip


if __name__ == "__main__":
    main()
