"""The model-facing half: turns a ticket event into proposals for the reducer."""

from .client import (
    DEFAULT_MODEL,
    MODELS,
    OPENROUTER_URL,
    ChatClient,
    Completion,
    ModelError,
    ModelSettings,
    ResponseCache,
    settings_for,
)
from .cost import PRICES, Estimate, Pricing, Usage, estimate
from .extractor import Extraction, Extractor, Reader
from .schema import OUTPUT_SCHEMA, Malformed, parse_output

__all__ = [
    "DEFAULT_MODEL",
    "MODELS",
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
    "ModelSettings",
    "ResponseCache",
    "Pricing",
    "Reader",
    "Usage",
    "estimate",
    "parse_output",
    "settings_for",
]
