"""Frozen Mendeley inputs and conversion into benchmark tickets."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from ..events import Actor, AssigneeChanged, MessagePosted, Role, StatusChanged
from .generate import Gold, SimTicket


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_frozen(path: Path):
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def write(path: Path, value, *, overwrite: bool = False) -> None:
    # Atomically publish the complete file. A failed write leaves no partial JSON.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        if overwrite:
            temporary.replace(path)
        else:
            path.hardlink_to(temporary)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("timestamp without offset")
    return parsed.astimezone(UTC)


def to_ticket(case) -> SimTicket:
    events = []
    for message in case["messages"]:
        event_id = f"{case['source']}:{message['id']}"
        at = instant(message["at"])
        role = Role(message["role"])
        actor = Actor(message["actor_id"], role)
        events.append(
            MessagePosted(
                id=event_id,
                ticket_id=case["id"],
                seq=len(events) + 1,
                at=at,
                author=actor,
                text=message["text"],
            )
        )
    if any(before.at > after.at for before, after in zip(events, events[1:], strict=False)):
        raise ValueError("bundle messages are out of order")
    for change in case["changes"]:
        kind = {"assignee": AssigneeChanged, "status": StatusChanged}[change["kind"]]
        events.append(
            kind(
                id=f"{case['source']}:{change['id']}",
                ticket_id=case["id"],
                seq=1,
                at=instant(change["at"]),
                previous=change["previous"],
                current=change["current"],
            )
        )
    events.sort(key=lambda event: (event.at, isinstance(event, MessagePosted)))
    events = [replace(event, seq=seq) for seq, event in enumerate(events, 1)]
    observed_at = instant(case["observed_at"]) if "observed_at" in case else events[-1].at
    if observed_at < events[-1].at:
        raise ValueError("observation ends before the final event")
    return SimTicket(case["id"], tuple(events), (), Gold(), observed_at)


def source_messages(case, ticket):
    return {
        event.seq: message
        for event, message in zip(
            (e for e in ticket.events if isinstance(e, MessagePosted)),
            case["messages"],
            strict=True,
        )
    }
