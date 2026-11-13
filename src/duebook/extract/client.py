"""A minimal client for any OpenAI-compatible chat endpoint.

Configured by a base URL, a key and a model name, and nothing else — so the
same code talks to OpenRouter, to a vendor directly, or to a local server
inside a closed network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .cost import Usage

OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"

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
    usage: Usage
    #: Why earlier attempts were discarded. Empty when the first one succeeded.
    retried: tuple[str, ...] = ()


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
    #: Sent as ``reasoning.effort`` to OpenRouter. Extraction is a reading task:
    #: left at a provider's default, a reasoning model spends ten times the
    #: tokens thinking and regularly runs out of budget before answering.
    #: None leaves the provider's default in place.
    reasoning_effort: str | None = "low"
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
            # Ask OpenRouter to report what it actually charged, and to route
            # only to upstreams that honour the structured-output request: the
            # same model is served by several hosts and not all of them do.
            body["usage"] = {"include": True}
            body["provider"] = {"require_parameters": True}
            if self.reasoning_effort:
                body["reasoning"] = {"effort": self.reasoning_effort}
        body.update(self.extra)

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
            return Completion(data, spent, tuple(problems))

        raise ModelError(f"gave up after {self.attempts} attempts: {problem}", spent)

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        http = self.http or httpx
        return http.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )


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
