"""The methods the event memory is compared against.

(a) ``FullContext`` — the whole transcript in the model's context at each
checkpoint; (b) ``RollingSummary`` — a summary updated after every event;
(c) ``Mem0Memory`` — the open-source Mem0 memory layer, an optional extra
(``pip install -e ".[mem0]"``); (d) ``GraphitiMemory`` — Zep's temporal
knowledge graph, likewise (``pip install -e ".[graphiti]"``).

All four use the extracting model of the event memory and answer the same
question in the same format (``answer``), so the comparison isolates the
memory method.
"""

from .answer import ANSWER_SCHEMA, INSTRUCTIONS, parse_answer, parse_due_at, render_event
from .full_context import FullContext
from .graphiti_memory import GraphitiMemory
from .graphiti_memory import available as graphiti_available
from .mem0_memory import Mem0Memory
from .mem0_memory import available as mem0_available
from .rolling import RollingSummary

__all__ = [
    "ANSWER_SCHEMA",
    "INSTRUCTIONS",
    "FullContext",
    "GraphitiMemory",
    "Mem0Memory",
    "RollingSummary",
    "graphiti_available",
    "mem0_available",
    "parse_answer",
    "parse_due_at",
    "render_event",
]
