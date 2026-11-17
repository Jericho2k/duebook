"""A minimal client for any OpenAI-compatible chat endpoint.

Configured by a base URL, a key and a model name, and nothing else — so the
same code talks to OpenRouter, to a vendor directly, or to a local server
inside a closed network.

What a particular model needs sent with every request lives in ``MODELS``,
not in the caller. The default reader, DeepSeek V4.1 Flash, is a reasoning
model: left at a low reasoning effort it spends 1,000-2,300 hidden tokens per
message, five to ten times the cost of the answer, so reasoning is switched
off. And a provider's prompt cache only helps when every request of a run
goes to the same provider, so its routing is pinned.

A ``ResponseCache`` keeps answers to deterministic requests (temperature 0)
on disk, keyed by the whole request body: a method re-run on unchanged input
costs nothing, and a re-run reads exactly the answers it read before.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx

from .cost import Usage

OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """What one model needs sent with every request to OpenRouter."""

    #: OpenRouter's ``reasoning`` field: ``{"enabled": False}``, ``{"effort":
    #: "low"}``... None leaves the provider's default in place.
    reasoning: Mapping[str, Any] | None = None
    #: OpenRouter's ``provider`` routing field. ``require_parameters`` is
    #: always added: the same model is served by several hosts, and only those
    #: that honour the structured-output request may answer.
    provider: Mapping[str, Any] | None = None


#: Per-model settings. A model not listed gets the provider's defaults.
MODELS: Mapping[str, ModelSettings] = MappingProxyType(
    {
        # Reasoning off: with it at "low" the model spent 1,000-2,300 hidden
        # tokens per message ($0.0007-0.0027); off, it answers the same schema
        # correctly in about 300. Routing pinned so the fixed instructions hit
        # the prompt cache: DeepInfra's fp8 endpoint, the cheapest for output
        # and for cached input among the hosts that honour strict JSON schema
        # (there is no "deepseek" host for this model on OpenRouter).
        "deepseek/deepseek-v4.1-flash": ModelSettings(
            reasoning=MappingProxyType({"enabled": False}),
            provider=MappingProxyType({"order": ("deepinfra/fp8",), "allow_fallbacks": True}),
        ),
        # Round 1's reader.
        "z-ai/glm-5.3-flash": ModelSettings(reasoning=MappingProxyType({"effort": "low"})),
    }
)


def settings_for(model: str) -> ModelSettings:
    return MODELS.get(model, ModelSettings())


_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


class ModelError(RuntimeError):
    """The model could not be reached or did not return usable content."""

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        #: Whatever was spent before giving up. A failed call is still billed.
        self.usage = usage or Usage()


@dataclass(frozen=True, slots=True)
class Completion:
    data: dict[str, Any]
    #: What the call cost when it was made — also when it is read back from
    #: the cache, so a method's cost is the same on every run.
    usage: Usage
    #: Why earlier attempts were discarded. Empty when the first one succeeded.
    retried: tuple[str, ...] = ()
    #: True when read from the response cache: nothing was charged now.
    cached: bool = False
    #: The host that served it, when the endpoint says.
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseCache:
    """Answers to deterministic requests, on disk, keyed by the whole request.

    Only a request at temperature 0 is cached: at any other temperature a
    second answer is supposed to differ. The key is a hash of the request
    body, model included, so any change to the instructions, the card or the
    message is a different request. Entries are written atomically.
    """

    root: Path

    def key(self, body: Mapping[str, Any]) -> str:
        raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=list)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, body: Mapping[str, Any]) -> dict[str, Any] | None:
        path = self._path(self.key(body))
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def put(self, body: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        path = self._path(self.key(body))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(path)

    def _path(self, key: str) -> Path:
        return Path(self.root) / key[:2] / f"{key}.json"


@dataclass(slots=True)
class ChatClient:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = OPENROUTER_URL
    #: "json_schema" for strict structured output, "json_object" for endpoints
    #: that only guarantee valid JSON.
    structured: str = "json_schema"
    temperature: float = 0.0
    max_tokens: int = 4_000
    #: Reasoning and routing for this model; None takes them from ``MODELS``.
    settings: ModelSettings | None = None
    #: Where answers to temperature-0 requests are kept; None keeps none.
    cache: ResponseCache | None = None
    attempts: int = 3
    timeout: float = 60.0
    #: Extra request fields, merged last. Provider routing, reasoning effort, etc.
    extra: dict[str, Any] = field(default_factory=dict)
    http: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep

    def complete_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], name: str = "output"
    ) -> Completion:
        """Ask for a JSON object. Retries transport faults and unreadable output."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.structured == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            }
        else:
            body["response_format"] = {"type": "json_object"}
        if "openrouter.ai" in self.base_url:
            # Ask OpenRouter to report what it actually charged.
            body["usage"] = {"include": True}
            body |= openrouter_fields(self.settings or settings_for(self.model))
        body.update(self.extra)

        cache = self.cache if self.temperature == 0 else None
        if cache is not None:
            stored = cache.get(body)
            data = _content(stored) if stored is not None else None
            if isinstance(data, dict):
                return Completion(data, _usage(stored), cached=True,
                                  provider=stored.get("provider"))  # fmt: skip

        spent = Usage()
        problems: list[str] = []
        problem = ""
        for attempt in range(self.attempts):
            if attempt:
                problems.append(problem)
                self.sleep(min(2.0**attempt, 20.0))
            try:
                response = self._post(body)
            except httpx.TransportError as error:
                problem = f"transport: {error!r}"
                continue
            if response.status_code in _RETRYABLE:
                problem = f"http {response.status_code}"
                continue
            if response.status_code >= 400:
                raise ModelError(f"http {response.status_code}: {response.text[:300]}", spent)

            try:
                payload = response.json()
            except ValueError:
                problem = "response body is not JSON"
                continue
            spent += _usage(payload)
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                problem = "response has no message"
                continue
            if not content:
                # Typically a reasoning model that spent its budget thinking.
                problem = "empty content"
                continue
            try:
                data = json.loads(_strip_fence(content))
            except ValueError:
                problem = f"content is not valid JSON: {content[:200]!r}"
                continue
            if not isinstance(data, dict):
                problem = "content is not a JSON object"
                continue
            if cache is not None:
                cache.put(body, payload)
            return Completion(data, spent, tuple(problems), provider=payload.get("provider"))

        raise ModelError(f"gave up after {self.attempts} attempts: {problem}", spent)

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        http = self.http or httpx
        return http.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )


def openrouter_fields(settings: ModelSettings) -> dict[str, Any]:
    """The request fields that carry a model's reasoning and routing settings."""
    fields: dict[str, Any] = {
        "provider": {"require_parameters": True, **_plain(settings.provider or {})}
    }
    if settings.reasoning is not None:
        fields["reasoning"] = _plain(settings.reasoning)
    return fields


def _plain(value: Any) -> Any:
    """Read-only settings as the JSON a request carries."""
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return value


def _content(payload: dict[str, Any]) -> Any:
    try:
        return json.loads(_strip_fence(payload["choices"][0]["message"]["content"]))
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _usage(payload: dict[str, Any]) -> Usage:
    raw = payload.get("usage") or {}
    details = raw.get("prompt_tokens_details") or {}
    cost = raw.get("cost")
    return Usage(
        calls=1,
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
        reported_usd=float(cost) if isinstance(cost, int | float) else None,
    )


def _strip_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
