"""The benchmark: simulated tickets with planted events, and a scorer.

    python -m duebook.bench --seed 1 --tickets 50            # self-check, no model
    python -m duebook.bench --model z-ai/glm-5.3-flash --writer <another model>

Tickets of 50+ messages (or 200+ with the long profile) with at least two
handoffs, planted promises, fact changes, disputes and failed fixes, and fixed
dev/test splits (``SPLITS``).
"""

from .generate import (
    LONG,
    MAIN,
    PROFILES,
    SPLITS,
    STANDARD,
    Fate,
    Gold,
    GoldCommitment,
    GoldContradiction,
    GoldFact,
    GoldRepeatedFix,
    Profile,
    SimTicket,
    Split,
    generate,
)
from .rewrite import Rewriter, SameModel, require_different_models
from .run import Oracle, Report, evaluate, run_ticket
from .score import Counts, Score, score

__all__ = [
    "LONG",
    "MAIN",
    "PROFILES",
    "SPLITS",
    "STANDARD",
    "Counts",
    "Fate",
    "Gold",
    "GoldCommitment",
    "GoldContradiction",
    "GoldFact",
    "GoldRepeatedFix",
    "Oracle",
    "Profile",
    "Report",
    "Rewriter",
    "SameModel",
    "Score",
    "SimTicket",
    "Split",
    "evaluate",
    "generate",
    "require_different_models",
    "run_ticket",
    "score",
]
