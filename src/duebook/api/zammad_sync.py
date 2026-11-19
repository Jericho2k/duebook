"""Poll one Zammad ticket and submit its unacknowledged events to /events."""

import json
import os

import httpx
import psycopg

from .. import signing
from ..settings import ConfigError, Settings
from ..store import Store, connect
from ..store.codec import encode
from .zammad import secure_base, source_events, stream_id


class SyncError(RuntimeError):
    """A failed poll that can be retried from the acknowledged prefix."""


def sync(settings: Settings, ticket_id: int, duebook_url: str) -> int:
    token = os.environ.get("DUEBOOK_ZAMMAD_SYNC_TOKEN", "").strip()
    if not all((settings.zammad_url, token, settings.webhook_secret)):
        raise ConfigError("sync needs DUEBOOK_ZAMMAD_URL, DUEBOOK_ZAMMAD_SYNC_TOKEN and "
                          "DUEBOOK_WEBHOOK_SECRET")
    base = secure_base(settings.zammad_url, "DUEBOOK_ZAMMAD_URL")
    target = secure_base(duebook_url, "--duebook-url")
    if ticket_id < 1:
        raise ConfigError("ticket ID must be positive")
    database = settings.require_database()
    try:
        _, remote = source_events(base, ticket_id, token)
    except httpx.HTTPStatusError as error:
        raise SyncError(f"Zammad returned HTTP {error.response.status_code}") from None
    except (httpx.RequestError, ValueError, TypeError, KeyError):
        raise SyncError("Zammad history or articles could not be read consistently") from None

    try:
        with connect(database) as conn:
            stored = list(Store(conn).events(stream_id(base, ticket_id)))
    except psycopg.Error:
        raise SyncError("could not read the Duebook event log") from None
    if remote[:len(stored)] != stored:
        raise SyncError("Zammad history differs from the acknowledged prefix")

    count = 0
    try:
        with httpx.Client(base_url=target, timeout=10) as client:
            for event in remote[len(stored):]:
                body = json.dumps(encode(event), ensure_ascii=False).encode()
                response = client.post("events", content=body, headers={
                    "Content-Type": "application/json",
                    signing.HEADER: signing.sign(settings.webhook_secret, body),
                })
                if response.status_code != 202:
                    raise SyncError(
                        f"Duebook returned HTTP {response.status_code} at seq {event.seq}"
                    )
                returned = response.json()
                if returned["ticket_id"] != event.ticket_id or returned["seq"] != event.seq:
                    raise ValueError("different event acknowledged")
                count += int(returned["new"])
    except (httpx.RequestError, ValueError, TypeError, KeyError):
        raise SyncError("Duebook could not acknowledge the event") from None
    return count
