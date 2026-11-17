"""The benchmark: simulated tickets with planted events, and a scorer.

    python -m duebook.bench --seed 1 --tickets 50            # self-check, no model
    python -m duebook.bench --model z-ai/glm-5.3-flash --writer <another model>

A first cut. It generates tickets and scores commitments, deadlines and breach
alerts. Fact changes, contradictions and repeated fixes are planted and
labelled but not scored yet, and there are no baselines to compare against.
"""

from .generate import Fate, Gold, GoldCommitment, SimTicket, generate
from .rewrite import Rewriter, SameModel, require_different_models
from .run import Oracle, Report, evaluate, run_ticket
from .score import Counts, Score, score

__all__ = [
    "Counts",
    "Fate",
    "Gold",
    "GoldCommitment",
    "Oracle",
    "Report",
    "Rewriter",
    "SameModel",
    "Score",
    "SimTicket",
    "evaluate",
    "generate",
    "require_different_models",
    "run_ticket",
    "score",
]
