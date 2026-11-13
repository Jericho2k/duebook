"""What extraction costs, measured and projected.

Measured: every model call returns a ``Usage``. Where the provider reports the
amount it actually charged (OpenRouter does), that figure is used; otherwise
the cost is computed from token counts and a price list.

Projected: ``estimate`` answers "what will a ticket of N messages cost" before
any money is spent, and puts the answer next to the cost of the naive approach
of re-reading the whole transcript on every message. The difference is the
point of the method — incremental extraction reads one message and a bounded
card per event, so its cost grows linearly with the ticket while re-reading
grows quadratically.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pricing:
    """US dollars per million tokens."""

    input_per_m: float
    output_per_m: float
    cached_input_per_m: float | None = None


#: Snapshots for planning only — prices move. The charged cost a provider
#: reports always takes precedence over anything computed from these.
PRICES: dict[str, Pricing] = {
    "z-ai/glm-5.3-flash": Pricing(0.075, 0.25, 0.015),
}


@dataclass(frozen=True, slots=True)
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: The part of ``prompt_tokens`` served from the provider's prompt cache.
    cached_tokens: int = 0
    #: What the provider says it charged, when it says.
    reported_usd: float | None = None

    def __add__(self, other: Usage) -> Usage:
        if self.reported_usd is None and other.reported_usd is None:
            reported = None
        else:
            reported = (self.reported_usd or 0.0) + (other.reported_usd or 0.0)
        return Usage(
            calls=self.calls + other.calls,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reported_usd=reported,
        )

    def usd(self, pricing: Pricing | None = None) -> float | None:
        """The charged cost if reported, else computed, else unknown."""
        if self.reported_usd is not None:
            return self.reported_usd
        if pricing is None:
            return None
        cached_rate = pricing.cached_input_per_m
        cached = self.cached_tokens if cached_rate is not None else 0
        fresh = self.prompt_tokens - cached
        return (
            fresh * pricing.input_per_m
            + cached * (cached_rate or 0.0)
            + self.completion_tokens * pricing.output_per_m
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class Estimate:
    messages: int
    #: Incremental extraction: instructions + card + one message, per message.
    incremental_usd: float
    incremental_prompt_tokens: int
    #: Re-reading the whole transcript on every message.
    reread_usd: float
    reread_prompt_tokens: int

    @property
    def ratio(self) -> float:
        return self.reread_usd / self.incremental_usd if self.incremental_usd else 0.0


def estimate(
    messages: int,
    pricing: Pricing,
    *,
    tokens_per_message: int = 120,
    instruction_tokens: int = 1_300,
    card_tokens: int = 600,
    output_tokens: int = 150,
) -> Estimate:
    """Project the cost of one ticket. Prompt caching is ignored, so this is an upper bound."""
    per_call = instruction_tokens + card_tokens + tokens_per_message
    incremental_prompt = messages * per_call
    # Call i re-reads messages 1..i.
    reread_prompt = (
        messages * instruction_tokens + tokens_per_message * messages * (messages + 1) // 2
    )
    output = messages * output_tokens

    def usd(prompt: int) -> float:
        return (prompt * pricing.input_per_m + output * pricing.output_per_m) / 1_000_000

    return Estimate(
        messages=messages,
        incremental_usd=usd(incremental_prompt),
        incremental_prompt_tokens=incremental_prompt,
        reread_usd=usd(reread_prompt),
        reread_prompt_tokens=reread_prompt,
    )
