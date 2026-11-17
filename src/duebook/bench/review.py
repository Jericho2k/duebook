"""Sample benchmark conversations for a human to read before seeing the gold labels."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from ..events import MessagePosted
from .experiment import results_directory
from .generate import SPLITS, SimTicket
from .rewrite import with_rewritten_text


def _answers(ticket: SimTicket) -> list[str]:
    gold = ticket.gold
    tz = ticket.events[0].at.tzinfo
    lines = []
    for c in gold.commitments:
        details = [c.owner.value, c.fate.value]
        if c.due_at:
            details.append(f"due {c.due_at.astimezone(tz):%Y-%m-%d %H:%M}")
        if c.resolved_seq:
            details.append(f"resolved at #{c.resolved_seq}")
        lines.append(f"- #{c.seq} Promise to {c.action} ({', '.join(details)})")
    for f in gold.facts:
        lines.append(f"- #{f.seq} Fact ({f.side.value}) — {f.slot} = {f.value}")
    for c in gold.changes:
        lines.append(f"- #{c.seq} Change to {c.slot} from {c.old} to {c.new}")
    for d in gold.contradictions:
        outcome = f"settled at #{d.settled_seq} for {d.winner}" if d.settled_seq else "open"
        lines.append(f"- #{d.seqs[0]}, #{d.seqs[1]} Dispute over {d.slot} ({outcome})")
    for f in gold.repeated_fixes:
        lines.append(f"- #{f.suggested_seq} Fix “{f.action}”, failed at #{f.failed_seq}, "
                     f"suggested again at #{f.repeated_seq}")  # fmt: skip
    for case in gold.close_cases:
        promises = ", ".join(f"#{seq}" for seq in case.promise_seqs)
        outcome = f"fulfils #{case.target_seq}" if case.target_seq else "leaves all open"
        lines.append(f"- #{case.seq} {case.kind.value} ({outcome} among {promises})")
    if gold.handoffs:
        lines.append("- Handoff events " + ", ".join(f"#{seq}" for seq in gold.handoffs))
    return lines


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m duebook.bench.review", description=__doc__)
    parser.add_argument("--split", choices=SPLITS, default="test2")
    parser.add_argument("--tickets", type=int, default=4, help="maximum number to sample")
    parser.add_argument("--seed", type=int, default=0, help="sampling seed")
    parser.add_argument("--results", type=Path, help="results directory with the ticket cache")
    parser.add_argument("--out", type=Path, help="defaults to docs/review/<split>-spotcheck.md")
    args = parser.parse_args(argv)
    if args.tickets < 1:
        parser.error("--tickets must be positive")

    split = SPLITS[args.split]
    tickets = random.Random(args.seed).sample(split.tickets(), min(args.tickets, split.count))
    cache = (args.results or results_directory(split)) / "tickets" / f"{split.name}.json"
    source = "Generated template text."
    if cache.is_file():
        stored = json.loads(cache.read_text(encoding="utf-8"))
        if (stored["split"], stored["seed"], stored["profile"], stored["count"]) != (
            split.name, split.seed, split.profile.name, split.count
        ):
            parser.error(f"{cache} belongs to a different split configuration")
        tickets = [with_rewritten_text(t, stored["tickets"][t.ticket_id]["texts"]) for t in tickets]
        source = f"Archived rewrites from `{cache}`."

    lines = [
        f"# Human spot-check for {split.name}",
        "",
        f"{source} Sample of {len(tickets)} tickets with seed {args.seed}.",
        "",
        "Read the conversations and record realism notes before opening the answer key.",
        "",
        "- [ ] Does each ticket read like a real support chat?",
        "- [ ] Which messages sound like something a person would never write? Cite their numbers.",
        "- [ ] After checking the answer key, which planted events would you label differently?",
        "",
        "Times use the ticket's time zone. Message numbers follow the event log, "
        "with gaps for assignment events.",
        "",
        "## Conversations",
    ]
    for ticket in tickets:
        lines.extend(("", f"### {ticket.ticket_id}", "", "```text"))
        lines.extend(
            f"#{e.seq}  {e.at:%Y-%m-%d %H:%M}  {e.author.role.value} ({e.author.name})  {e.text}"
            for e in ticket.events if isinstance(e, MessagePosted)
        )  # fmt: skip
        lines.extend(("```", "", "Realism notes", "", "> ........................................"))

    lines.extend(("", "## Answer key"))
    for ticket in tickets:
        lines.extend(("", f"### {ticket.ticket_id}", "", *_answers(ticket), "",
                      "Label corrections", "", "> ........................................"))

    out = args.out or Path("docs/review") / f"{split.name}-spotcheck.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
