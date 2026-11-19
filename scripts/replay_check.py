"""Replay every saved record at $0 and check it against what was saved.

    python scripts/replay_check.py --write /tmp/before.json    # before a change
    python scripts/replay_check.py --against /tmp/before.json  # after it

1. Rounds 4-8 and 10: every event-memory run's recorded proposals, folded under the
   closing rules of its round, must give exactly the snapshots it saved; a
   run that stopped on a message stops there again.
2. Rounds 9 and 10: Round 8's proposals folded under rule versions 4 and 5, and the
   targeted re-extractions of Rounds 9 and 10 (the messages read again are
   served from their records, a later round's over an earlier one's), must
   give exactly the metrics they saved.
3. Round 10b: Round 10's records and ``test4``'s (Round 8's, and spliced with the
   reads of Rounds 9 and 10) folded under rule versions 5 and 6 (decision 0006)
   must give exactly the metrics, and the tickets that changed, saved in
   ``round10/replay``.
4. The Round 10 model comparison checks both readers' saved snapshots and paired scores.
5. Every full record of rounds 4-10 is scored again through the experiment runner,
   its saved snapshots (``--frozen``) and its proposals folded under today's
   rules (``--replay``). These summaries differ from the saved ones where a
   later round changed the rules or the scorer, so with ``--against`` they
   must equal an earlier output of this script instead.

A model call stops the check.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import duebook.bench.experiment as experiment
import duebook.bench.methods as methods
import duebook.bench.run as run
import duebook.extract.client as client
from duebook.bench.experiment import WRITER, rewritten, to_json
from duebook.bench.generate import SPLITS
from duebook.bench.meter import Ledger
from duebook.bench.result import checkpoints
from duebook.extract.client import ModelError
from duebook.extract.extractor import Extraction
from duebook.reducer import Guards, apply
from duebook.store.codec import decode_proposals
from duebook.temporal import WorkCalendar

R = Path("results")
MODEL_COMPARISON = "round10/model-comparison"
MODEL_ARMS = ("deepseek", "glm")
#: The full records, and the closing rules their round decided under.
FULL = {"round4/test3": 1, "round5/test3": 2, "round5/dev3": 2, "round6/test3": 3,
        "round7/test3": 3, "round7/dev3": 3, "round7/dev4": 3, "round8/test4": 4,
        "round8/dev5": 4, "round10/test5": 5, "round10/test3": 5,
        **{f"{MODEL_COMPARISON}/{arm}": 5 for arm in MODEL_ARMS}}  # fmt: skip
#: Targeted re-extractions: (record, split, what was replayed, the messages read
#: again, in the order the rounds read them, and the records that hold those reads).
R9_TEST4 = (["results/round9/reread.json"], ["round9/test4"])
REREAD = [("round9/test4", "test4", "results/round8/test4.json", *R9_TEST4),
          ("round9/dev5", "dev5", "results/round8/dev5.json", ["results/round9/reread.json"],
           ["round9/dev5"]),
          ("round9/dev6", "dev6", "oracle", ["results/round9/reread-dev6.json"], ["round9/dev6"]),
          ("round10/test4-reread", "test4", "results/round8/test4.json",
           [*R9_TEST4[0], "results/round10/reread-test4.json"],
           [*R9_TEST4[1], "round10/test4-reread"]),
          ("round10/dev7", "dev7", "oracle", ["results/round10/reread-dev7.json"],
           ["round10/dev7"])]  # fmt: skip
#: Round 10b: what was folded under rule versions 5 and 6.
R10B = {"round10/test5": ("test5", "results/round10/test5.json"),
        "round10/test3": ("test3", "results/round10/test3.json"),
        "round8/test4": ("test4", "results/round8/test4.json"),
        "round10/test4-reread": ("test4", REREAD[3])}  # fmt: skip


def no_model(self, body):
    raise SystemExit("a model call was attempted: the replay is not free")


def load(name: str) -> dict:
    return json.loads((R / f"{name}.json").read_text())


def holds(saved, now) -> bool:
    """Is everything ``saved`` holds also in ``now``? A later version may add fields."""
    if isinstance(saved, dict):
        return isinstance(now, dict) and all(k in now and holds(v, now[k])
                                             for k, v in saved.items())  # fmt: skip
    if isinstance(saved, list):
        return (isinstance(now, list) and len(saved) == len(now)
                and all(holds(a, b) for a, b in zip(saved, now, strict=True)))  # fmt: skip
    return saved == now


class Recorded:
    """A run's recorded proposals; where the run stopped, the error it stopped on."""

    def __init__(self, proposals: list, failure: str | None):
        self.proposals, self.failure = proposals, failure

    def extract(self, card, event):
        if event.seq > len(self.proposals) and self.failure:
            raise ModelError(self.failure.removeprefix("ModelError: "))
        return Extraction(self.proposals[event.seq - 1] if event.seq <= len(self.proposals) else ())


def snapshots_differ(name: str, rules: int) -> list[str]:
    """The event-memory tickets of a record whose saved snapshots its proposals do not rebuild."""
    record = load(name)
    split = SPLITS[record["split"]]
    tickets = (split.tickets() if record.get("template")
               else rewritten(split, WRITER, Ledger(), split.results / "tickets")[0])
    by_id = {t.ticket_id: t for t in tickets}
    differ = []
    for raw in record["runs"]:
        if raw["method"] != "event_memory":
            continue
        ticket = by_id[raw["ticket"]]
        reader = Recorded([decode_proposals(p) for p in raw["detail"]["proposals"]],
                          raw["failure"])  # fmt: skip
        stops = [cp.seq for cp in checkpoints(ticket)]
        folded = run.fold(ticket, reader, stops, WorkCalendar(), Guards(rules=rules))
        now = json.loads(json.dumps(to_json(run.snapshots(ticket, folded))))
        if not holds(raw["snapshots"], now):
            differ.append(f"{name} {ticket.ticket_id}: snapshots differ under rules {rules}")
    return differ


def summary(argv: list[str], out: Path) -> dict:
    experiment.main([*argv, "--max-usd", "0", "--results", str(out.parent), "--out", str(out)])
    result = json.loads(out.read_text())
    return {"summary": result["summary"]["methods"],
            "guard_effects": result["guard_effects"]["verdicts"],
            "verifier": {k: v for k, v in result["verifier"].items() if k != "rows"}}  # fmt: skip


def rounded(rows: dict) -> dict:
    """A summary in the form Round 9's records keep it."""
    skip = {"tickets", "counts_end", "counts_all", "failures", "usd_total", "deadline_errors",
            "promise_stages", "beats"}  # fmt: skip
    return {"metrics": {k: round(v["value"], 4) for k, v in rows.items() if k not in skip},
            "counts_all_checkpoints": rows["counts_all"],
            "promise_stages": rows["promise_stages"], "beats": rows["beats"]}  # fmt: skip


def metrics_differ(label: str, saved: dict, now: dict) -> list[str]:
    return [f"{label} {part}: saved {saved[part]}, now {now.get(part)}"
            for part in saved if not holds(saved[part], now.get(part))]  # fmt: skip


def under(rules: int, argv: list[str], out: Path) -> dict:
    """``summary`` with every event decided under rule version ``rules``."""
    run.apply = lambda *a, guards=None, **k: apply(*a, guards=guards or Guards(rules=rules), **k)
    try:
        return summary(argv, out)
    finally:
        run.apply = apply


def serving(name: str, split: str, replay: str, selections: list[str], records: list[str],
            tmp: Path) -> list[str]:  # fmt: skip
    """The runner's arguments for a targeted re-extraction, its reads served from the records."""
    answers = {(m["ticket"], m["seq"]): decode_proposals(proposals_of(m))
               for r in records for m in load(r)["messages"]}  # fmt: skip
    methods.read_event = lambda reader, card, event, previous: Extraction(
        answers[event.ticket_id, event.seq])
    # Served from the records, the reads cost nothing to project.
    experiment.project_reread = lambda path, calls: {"event_memory": dict.fromkeys(
        ("upper", "expected", "sample_usd", "sample_charged", "reused"), 0.0)}
    listed: dict[str, set[int]] = {}
    for path in selections:
        for ticket, seqs in json.loads(Path(path).read_text())["tickets"].items():
            listed.setdefault(ticket, set()).update(seqs)
    reread = tmp / f"{name.replace('/', '-')}.reread.json"
    reread.write_text(json.dumps({"tickets": {t: sorted(s) for t, s in listed.items()}}))
    return ["--split", split, "--methods", "event_memory", "--replay", replay,
            "--reread", str(reread), *(["--template"] if replay == "oracle" else [])]


def round9(tmp: Path) -> list[str]:
    differ = []
    for split, versions in load("round9/replay")["splits"].items():
        for version, saved in versions.items():
            now = under(int(version.removeprefix("rules_v")),
                        ["--split", split, "--methods", "event_memory",
                         "--replay", f"results/round8/{split}.json"],
                        tmp / f"r9-{split}-{version}.json")  # fmt: skip
            differ += metrics_differ(f"round9/replay {split} {version}", saved,
                                     rounded(now["summary"]["event_memory"]))  # fmt: skip
    for name, *config in REREAD:
        now = summary(serving(name, *config, tmp), tmp / f"{name.replace('/', '-')}.json")
        differ += metrics_differ(name, load(name)["event_memory"],
                                 rounded(now["summary"]["event_memory"]))  # fmt: skip
    return differ


def round10b(tmp: Path) -> dict:
    """Each record folded under rule versions 5 and 6, and the tickets whose scores differ."""
    out = {}
    for name, (split, replay) in R10B.items():
        stem = tmp / f"r10b-{name.replace('/', '-')}"
        argv = (serving(name, *replay[1:], tmp) if isinstance(replay, tuple) else
                ["--split", split, "--methods", "event_memory", "--replay", replay])  # fmt: skip
        both = {f"rules_v{v}": under(v, argv, stem.with_suffix(f".v{v}.json")) for v in (5, 6)}
        tickets = [{r["ticket"]: r for r in json.loads(stem.with_suffix(f".v{v}.json").read_text())
                    ["runs"]} for v in (5, 6)]  # fmt: skip
        out[name] = {v: rounded(now["summary"]["event_memory"]) for v, now in both.items()}
        parts = ("score_end", "score_handoffs")
        out[name]["changed"] = {
            t: {f"{part}.{k}": [old[part][k], tickets[1][t][part][k]]
                for part in parts for k in old[part] if old[part][k] != tickets[1][t][part][k]}
            for t, old in tickets[0].items()
            if any(old[part] != tickets[1][t][part] for part in parts)}  # fmt: skip
    return out


def proposals_of(message: dict) -> list:
    """The proposals a round's live read made: ``round9_proposals``, ``round10_proposals``."""
    return next(v for k, v in message.items() if k.endswith("_proposals"))


def model_comparison_differ() -> list[str]:
    """Check the paired scores against the two frozen model runs."""
    runs = [
        replace(run, method=arm)
        for arm in MODEL_ARMS
        for run in experiment.load_runs(R / MODEL_COMPARISON / f"{arm}.json")
    ]
    saved = load(f"{MODEL_COMPARISON}/comparison")["paired_comparison"]
    now = to_json(experiment.summarise(SPLITS["dev-portability"].tickets(), runs))
    return metrics_differ(MODEL_COMPARISON, saved, now)


def scored_again(tmp: Path) -> dict:
    out = {}
    for name in FULL:
        record = load(name)
        split = ["--split", record["split"], *(["--template"] if record.get("template") else [])]
        stem = tmp / name.replace("/", "-")
        out[f"{name} frozen"] = summary([*split, "--methods", "", "--frozen", f"{R}/{name}.json"],
                                        stem.with_suffix(".frozen.json"))  # fmt: skip
        out[f"{name} replay"] = summary([*split, "--methods", "event_memory",
                                         "--replay", f"{R}/{name}.json"],
                                        stem.with_suffix(".replay.json"))  # fmt: skip
    return json.loads(json.dumps(out))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", type=Path, help="save the summaries scored again")
    parser.add_argument("--against", type=Path, help="summaries that must be identical")
    args = parser.parse_args(argv)
    client.ChatClient._post = no_model
    differ = [d for name, rules in FULL.items() for d in snapshots_differ(name, rules)]
    differ += model_comparison_differ()
    with tempfile.TemporaryDirectory() as tmp:
        differ += round9(Path(tmp))
        saved = load("round10/replay")["records"]
        differ += [d for name, now in round10b(Path(tmp)).items()
                   for d in metrics_differ(f"round10/replay {name}", saved[name], now)]
        scored = scored_again(Path(tmp))
    if args.write:
        args.write.write_text(json.dumps(scored, ensure_ascii=False, indent=1, sort_keys=True))
    if args.against:
        before = json.loads(args.against.read_text())
        differ += [f"{k}: scored differently than in {args.against}"
                   for k in sorted(before.keys() | scored.keys()) if before.get(k) != scored.get(k)]
    for line in differ:
        print(line)
    print(f"{len(differ)} differences", file=sys.stderr)
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
