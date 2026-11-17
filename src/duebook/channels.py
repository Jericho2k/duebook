"""Where the delivery worker sends alerts.

The worker's one demand on a channel is that it treat ``Notice.key`` as an
idempotency key: a worker that dies between sending and recording leaves the
notice to be sent again under the same key. Each channel here passes the key
on in a form its receiver can act on.

* ``LogChannel`` writes one JSON line per alert to stdout, for log pipelines
  and for running without a receiver. The key is in the line; a consumer that
  deduplicates on it sees each alert once.
* ``WebhookChannel`` POSTs the alert as JSON with an ``Idempotency-Key``
  header, signed with the same HMAC scheme duebook asks of incoming webhooks.
  Anything but a 2xx answer raises, and the worker retries with backoff: an
  alert is never recorded as delivered on a receiver's error page.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

import httpx

from . import signing
from .lifecycle import Notice


class DeliveryError(RuntimeError):
    """The receiver did not accept the alert."""


def payload(notice: Notice) -> dict[str, Any]:
    """An alert as JSON-compatible data."""
    return {
        "key": notice.key,
        "kind": notice.kind.value,
        "ticket_id": notice.ticket_id,
        "commitment_id": notice.commitment_id,
        "due_at": notice.due_at.isoformat(),
        "recipient": notice.recipient,
    }


@dataclass(slots=True)
class LogChannel:
    stream: TextIO | None = None

    def send(self, notice: Notice) -> None:
        stream = self.stream or sys.stdout
        stream.write(json.dumps({"alert": payload(notice)}, ensure_ascii=False) + "\n")
        stream.flush()


@dataclass(slots=True)
class WebhookChannel:
    url: str
    #: Signs each request when set: ``X-Duebook-Signature: sha256=<hex>``.
    secret: str = ""
    timeout: float = 10.0
    http: httpx.Client = field(init=False)
    #: A client to use instead of a new one; tests pass one with a mock transport.
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        self.http = self.client or httpx.Client(timeout=self.timeout)

    def send(self, notice: Notice) -> None:
        body = json.dumps(payload(notice), ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "Idempotency-Key": notice.key}
        if self.secret:
            headers[signing.HEADER] = signing.sign(self.secret, body)
        response = self.http.post(self.url, content=body, headers=headers)
        if not response.is_success:
            raise DeliveryError(f"{self.url} answered {response.status_code}")

    def close(self) -> None:
        self.http.close()
