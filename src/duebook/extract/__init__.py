"""The model-facing half: turns a ticket event into proposals for the reducer."""

from .client import DEFAULT_MODEL, OPENROUTER_URL, ChatClient, Completion, ModelError
from .cost import PRICES, Estimate, Pricing, Usage, estimate
from .extractor import Extraction, Extractor
from .schema import OUTPUT_SCHEMA, Malformed, parse_output

__all__ = [
    "DEFAULT_MODEL",
    "OPENROUTER_URL",
    "OUTPUT_SCHEMA",
    "PRICES",
    "ChatClient",
    "Completion",
    "Estimate",
    "Extraction",
    "Extractor",
    "Malformed",
    "ModelError",
    "Pricing",
    "Usage",
    "estimate",
    "parse_output",
]
