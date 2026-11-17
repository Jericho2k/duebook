"""The model-facing half: turns a ticket event into proposals for the reducer."""

from .client import (
    DEFAULT_MODEL,
    OPENROUTER_URL,
    PRESETS,
    REASONING,
    ChatClient,
    Completion,
    ModelError,
    ModelSettings,
    Preset,
    ResponseCache,
    settings_for,
)
from .cost import PRICES, Estimate, Pricing, Usage, estimate
from .extractor import Extraction, Extractor, Reader
from .schema import OUTPUT_SCHEMA, Malformed, parse_output
from .verifier import VERIFIER_PROMPT, VERIFY_SCHEMA, Verifier, VerifyingReader, target_of

__all__ = [
    "DEFAULT_MODEL",
    "OPENROUTER_URL",
    "OUTPUT_SCHEMA",
    "VERIFIER_PROMPT",
    "VERIFY_SCHEMA",
    "PRICES",
    "ChatClient",
    "Completion",
    "Estimate",
    "Extraction",
    "Extractor",
    "Malformed",
    "ModelError",
    "ModelSettings",
    "PRESETS",
    "Preset",
    "REASONING",
    "ResponseCache",
    "Pricing",
    "Reader",
    "Usage",
    "Verifier",
    "VerifyingReader",
    "estimate",
    "parse_output",
    "settings_for",
    "target_of",
]
