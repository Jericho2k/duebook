"""Command line for the benchmark. Without --model it runs the self-check only."""

from __future__ import annotations

import argparse
import os

from ..extract import ChatClient, Extractor
from . import Oracle, Rewriter, evaluate, generate, require_different_models


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m duebook.bench", description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--tickets", type=int, default=50)
    parser.add_argument("--model", help="extracting model; omit to run the oracle self-check")
    parser.add_argument(
        "--writer", help="model that rewrites the tickets; must differ from --model"
    )
    parser.add_argument("--max-usd", type=float, default=0.5, help="stop once this was charged")
    args = parser.parse_args()

    tickets = generate(args.seed, args.tickets)
    if not args.model:
        print("oracle self-check (no model called)")
        print(evaluate(tickets, Oracle.of(tickets)).summary())
        return

    key = os.environ.get("OPENROUTER_API_KEY", "proxy-injected")
    spent = 0.0
    if args.writer:
        require_different_models(args.writer, args.model)
        rewriter = Rewriter(ChatClient(api_key=key, model=args.writer, temperature=0.8))
        rewritten = []
        for ticket in tickets:
            if (rewriter.spent.usd() or 0.0) >= args.max_usd / 2:
                break  # half the budget is for writing, the rest for reading
            rewritten.append(rewriter.rewrite(ticket))
        tickets = rewritten
        spent = rewriter.spent.usd() or 0.0
        print(f"rewritten by {args.writer}: {len(tickets)} tickets, ${spent:.4f}, "
              f"{rewriter.discarded} rewrites discarded")  # fmt: skip
    else:
        print("WARNING: template text only; pass --writer to rewrite it with another model")

    extractor = Extractor(ChatClient(api_key=key, model=args.model))
    print(f"extracted by {args.model}")
    print(evaluate(tickets, extractor, max_usd=args.max_usd - spent).summary())


if __name__ == "__main__":
    main()
