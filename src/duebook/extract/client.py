"""A minimal client for any OpenAI-compatible chat endpoint.

Configured by a base URL, a key and a model name, and nothing else — so the
same code talks to OpenRouter, to a vendor directly, or to a local server
inside a closed network.

What a particular model needs sent with every request lives in ``PRESETS``,
not in the caller: which hosts may serve it, and how hard it reasons. The
default reader, DeepSeek V4.1 Flash, is a reasoning model: left at a low
reasoning effort it spends 1,000-2,300 hidden tokens per message, five to ten
times the cost of the answer, so reasoning is off unless a caller asks for it
(``ChatClient.reasoning``). A provider's prompt cache only helps when every
request of a run goes to the same host, and the same model on another host
may answer differently, so a preset LOCKS the model to its hosts: no
fallback to anyone else.

Retries are few and deliberate. A 429 or a 5xx is retried once, and so is an
empty, cut-off or unreadable answer unless ``content_retries`` is 0. A timeout
is never retried: the answer may have been generated, and billed, after the
client stopped waiting.

A ``ResponseCache`` keeps answers to deterministic requests (temperature 0)
on disk, keyed by the whole request body: a method re-run on unchanged input
costs nothing, and a re-run reads exactly the answers it read before.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
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


#: OpenRouter's ``reasoning`` field, by name. "low" keeps the reasoning out
#: of the answer (it is still generated, and billed).
REASONING: Mapping[str, Mapping[str, Any]] = MappingProxyType(
    {
        "off": MappingProxyType({"enabled": False}),
        "low": MappingProxyType({"effort": "low", "exclude": True}),
    }
)


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """What one request to OpenRouter carries for its model."""

    #: OpenRouter's ``reasoning`` field: ``{"enabled": False}``, ``{"effort":
    #: "low", "exclude": True}``... None leaves the provider's default in place.
    reasoning: Mapping[str, Any] | None = None
    #: OpenRouter's ``provider`` routing field. ``require_parameters`` is
    #: always added: the same model is served by several hosts, and only those
    #: that honour the structured-output request may answer.
    provider: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Preset:
    """How one model is called: the hosts that may serve it and how hard it reasons."""

    #: OpenRouter provider slugs, in order of preference. Empty: any host.
    providers: tuple[str, ...] = ()
    #: A key of ``REASONING``, or None for the provider's default.
    reasoning: str | None = None
    #: False locks the model to ``providers``: if none of them can answer,
    #: the request fails rather than going to a host nobody chose.
    allow_fallbacks: bool = False

    def settings(self, reasoning: str | None = None) -> ModelSettings:
        """The request settings, with ``reasoning`` (a key of ``REASONING``) overriding."""
        effort = reasoning or self.reasoning
        if effort is not None and effort not in REASONING:
            raise ValueError(f"unknown reasoning {effort!r}: one of {', '.join(REASONING)}")
        provider = None
        if self.providers:
            provider = MappingProxyType(
                {"order": self.providers, "allow_fallbacks": self.allow_fallbacks}
            )
        return ModelSettings(REASONING[effort] if effort else None, provider)


#: Per-model presets. A model not listed gets the provider's defaults.
PRESETS: Mapping[str, Preset] = MappingProxyType(
    {
        # Reasoning off: with it at "low" the model spent 1,000-2,300 hidden
        # tokens per message; off, it answers the same schema correctly in
        # about 300. Locked to DeepInfra's fp8 endpoint, the cheapest for
        # output and for cached input among the hosts that honour strict JSON
        # schema (there is no "deepseek" host for this model on OpenRouter), so
        # the fixed instructions hit one prompt cache and one host answers.
        "deepseek/deepseek-v4.1-flash": Preset(("deepinfra/fp8",), "off"),
        # Round 1's reader.
        "z-ai/glm-5.3-flash": Preset(reasoning="low", allow_fallbacks=True),
        # The writer (served as z-ai/glm-5.3-flash). Rephrasing needs no
        # reasoning; with it on, the writer cost six times as much.
        "~z-ai/glm-flash-latest": Preset(("gmicloud/fp8", "tenstorrent/fp8", "novita/fp8"), "off"),
    }
)


def settings_for(model: str, reasoning: str | None = None) -> ModelSettings:
    """A model's request settings, from its preset; ``reasoning`` overrides the preset's."""
    return PRESETS.get(model, Preset(allow_fallbacks=True)).settings(reasoning)


class ModelError(RuntimeError):
    """The model could not be reached or did not return usable content."""

    def __init__(
        self, message: str, usage: Usage | None = None, *, provider: str | None = None,
        unreadable: bool = False,
    ) -> None:
        super().__init__(message)
        #: Whatever was spent before giving up. A failed call is still billed.
        self.usage = usage or Usage()
        self.provider = provider
        #: The model answered, but not readably: cut off, empty or not JSON.
        self.unreadable = unreadable


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
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        tmp.replace(path)

    def _path(self, key: str) -> Path:
        return Path(self.root) / key[:2] / f"{key}.json"


@dataclass(slots=True)
class ChatClient:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    base_url: str = OPENROUTER_URL
    #: "json_schema" for strict structured output, "json_object" for endpoints
    #: that only guarantee valid JSON.
    structured: str = "json_schema"
    temperature: float = 0.0
    max_tokens: int = 4_000
    #: Reasoning and routing for this model; None takes them from ``PRESETS``.
    settings: ModelSettings | None = None
    #: A key of ``REASONING`` ("off", "low") overriding the preset's, so one
    #: model can be called at two efforts. Ignored when ``settings`` is given.
    reasoning: str | None = None
    #: Where answers to temperature-0 requests are kept; None keeps none.
    cache: ResponseCache | None = None
    #: Retries after a 429 or a 5xx (or a refused connection: nothing was sent).
    http_retries: int = 1
    #: Retries after an empty or unreadable answer.
    content_retries: int = 1
    timeout: float = 60.0
    #: Extra request fields, merged last. Provider routing, reasoning effort, etc.
    extra: dict[str, Any] = field(default_factory=dict)
    http: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep

    def complete_json(
        self, messages: list[dict[str, str]], schema: dict[str, Any], name: str = "output"
    ) -> Completion:
        """Ask for a JSON object.

        A 429 or a 5xx is retried ``http_retries`` times, an empty or
        unreadable answer ``content_retries`` times. A timeout is never
        retried: the model may have generated the answer, and the provider
        billed it, after the client stopped waiting; the caller decides.
        """
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
            body |= openrouter_fields(self.settings or settings_for(self.model, self.reasoning))
        body.update(self.extra)

        cache = self.cache if self.temperature == 0 else None
        if cache is not None:
            stored = cache.get(body)
            data = _content(stored) if stored is not None else None
            if isinstance(data, dict):
                return Completion(data, _usage(stored), cached=True,
                                  provider=stored.get("provider"))  # fmt: skip

        spent = Usage()
        provider = None
        problems: list[str] = []
        left = {"http": self.http_retries, "content": self.content_retries}

        def again(kind: str, problem: str) -> None:
            """Note the problem, and raise unless a retry of its kind is left."""
            problems.append(problem)
            if left[kind] <= 0:
                tried = "; ".join(problems)
                raise ModelError(f"gave up after {len(problems)} attempts: {tried}", spent,
                                 provider=provider, unreadable=kind == "content")  # fmt: skip
            left[kind] -= 1
            if kind == "http":
                self.sleep(2.0)

        while True:
            try:
                response = self._post(body)
            except httpx.TimeoutException as error:
                raise ModelError(f"timeout, not retried (it may have been billed): {error!r}",
                                 spent, provider=provider) from error  # fmt: skip
            except httpx.ConnectError as error:
                again("http", f"connect: {error!r}")
                continue
            except httpx.RequestError as error:
                # The request went out; an answer may have been generated.
                raise ModelError(f"transport, not retried: {error!r}",
                                 spent, provider=provider) from error  # fmt: skip
            status = response.status_code
            if status == 429 or status >= 500:
                again("http", f"http {status}")
                continue
            if status >= 400:
                raise ModelError(f"http {status}: {response.text[:300]}",
                                 spent, provider=provider)  # fmt: skip

            try:
                payload = response.json()
            except ValueError:
                again("content", "response body is not JSON")
                continue
            if not isinstance(payload, dict):
                again("content", "response body is not a JSON object")
                continue
            host = payload.get("provider")
            if isinstance(host, str) and host:
                provider = host
            try:
                spent += _usage(payload)
            except (AttributeError, TypeError, ValueError, OverflowError):
                again("content", "response usage is unreadable")
                continue
            try:
                choice = payload["choices"][0]
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError):
                again("content", "response has no message")
                continue
            if choice.get("finish_reason") == "length":
                again("content", f"answer cut off at {self.max_tokens} tokens")
                continue
            if not content:
                # Typically a reasoning model that spent its budget thinking.
                again("content", "empty content")
                continue
            if not isinstance(content, str):
                again("content", "message content is not text")
                continue
            try:
                data = json.loads(_strip_fence(content))
            except ValueError:
                again("content", f"content is not valid JSON: {content[:200]!r}")
                continue
            if not isinstance(data, dict):
                again("content", "content is not a JSON object")
                continue
            if cache is not None:
                cache.put(body, payload)
            return Completion(data, spent, tuple(problems), provider=provider)

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
    completion = raw.get("completion_tokens_details") or {}
    cost = raw.get("cost")
    return Usage(
        calls=1,
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
        reported_usd=float(cost) if isinstance(cost, int | float) else None,
        reasoning_tokens=int(completion.get("reasoning_tokens") or 0),
    )


def _strip_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
