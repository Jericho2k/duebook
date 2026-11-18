import io
import json
from datetime import timedelta

import httpx
import pytest
from helpers import IVAN, START, persist, promise

from duebook import Notice, NoticeKind, signing
from duebook.channels import DeliveryError, LogChannel, WebhookChannel
from duebook.store import Worker

NOTICE = Notice("T-1:C1:breached:x", NoticeKind.BREACHED, "T-1", "C1", START, IVAN.id)
URL = "https://alerts.example/duebook"


class Receiver:
    """An HTTP endpoint that records requests and answers with the statuses given."""

    def __init__(self, *statuses: int):
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.statuses.pop(0) if self.statuses else 200)

    def channel(self, secret: str = "") -> WebhookChannel:
        return WebhookChannel(URL, secret, client=httpx.Client(transport=httpx.MockTransport(self)))


def test_the_log_channel_writes_one_json_line_per_alert_with_its_key():
    out = io.StringIO()
    LogChannel(out).send(NOTICE)
    LogChannel(out).send(NOTICE)
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    alert = json.loads(lines[0])["alert"]
    assert alert == {
        "key": NOTICE.key, "kind": "breached", "ticket_id": "T-1", "commitment_id": "C1",
        "due_at": START.isoformat(), "recipient": IVAN.id, "seq": None,
    }  # fmt: skip


def test_the_webhook_channel_posts_the_alert_with_its_key_as_the_idempotency_key():
    receiver = Receiver()
    receiver.channel().send(NOTICE)
    (request,) = receiver.requests
    assert request.method == "POST" and str(request.url) == URL
    assert request.headers["Idempotency-Key"] == NOTICE.key
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content)["recipient"] == IVAN.id
    assert signing.HEADER not in request.headers


def test_the_webhook_channel_signs_the_exact_bytes_it_sends():
    receiver = Receiver()
    receiver.channel(secret="s3cret").send(NOTICE)
    (request,) = receiver.requests
    assert signing.verify("s3cret", request.content, request.headers[signing.HEADER])


@pytest.mark.parametrize("status", [301, 400, 404, 429, 500, 503])
def test_anything_but_a_2xx_answer_is_a_failed_delivery(status):
    with pytest.raises(DeliveryError):
        Receiver(status).channel().send(NOTICE)


@pytest.mark.usefixtures("conn")
def test_a_receiver_error_sends_the_alert_back_to_the_queue_under_the_same_key(store, conn):
    persist(store, promise("T-1"), now=START + timedelta(hours=3))
    receiver = Receiver(503, 202)
    worker = Worker(store, receiver.channel(), "w1")
    late = START + timedelta(hours=3)

    assert worker.run_once(late).failed == 1
    assert store.outbox.pending() == 1
    assert worker.run_once(late + timedelta(minutes=1)).sent == 1
    keys = {r.headers["Idempotency-Key"] for r in receiver.requests}
    assert len(receiver.requests) == 2 and len(keys) == 1
    assert store.outbox.pending() == 0
