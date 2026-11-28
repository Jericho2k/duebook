"""Durable state around the pure core: event log, card snapshots, alert outbox.

Needs PostgreSQL and psycopg 3 (``pip install duebook[store]``). Nothing in
the core imports this package.

The names below are imported on first use, not with the package, so that
``duebook.store.codec`` — plain JSON, no database — can be used where psycopg
is not installed, as the benchmark does to keep what a run proposed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from importlib import import_module
from typing import Any

from ..card import Card, Commitment
from ..events import CheckKind, MessagePosted, PromiseCheck

_WHERE = {
    "connect": "db",
    "migrate": "db",
    "Claim": "outbox",
    "Outbox": "outbox",
    "Conflict": "store",
    "Hold": "store",
    "Recorded": "store",
    "Store": "store",
    "Channel": "worker",
    "Round": "worker",
    "Worker": "worker",
}

__all__ = sorted((*_WHERE, "promise_check"))


def __getattr__(name: str) -> Any:
    if name not in _WHERE:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{_WHERE[name]}", __name__), name)


def promise_check(card: Card, c: Commitment, kind: CheckKind, at: datetime,
                  messages: Iterable[MessagePosted]) -> PromiseCheck:
    # UTF-8 bytes bound the token count of the JSON evidence.
    remaining = 6400
    evidence = []
    for m in messages:  # newest first
        if not m.public or m.seq > card.last_seq or m.at > at:
            continue
        if m.seq <= c.source.seq:
            break
        row = json.dumps([m.id, m.author.side.value, m.text], ensure_ascii=False)
        remaining -= len(row.encode()) + 2
        if remaining < 0:
            break
        evidence.append(m)
    position = card.last_seq if kind is CheckKind.HANDOFF else at.isoformat()
    return PromiseCheck(id=f"check:{kind}:{c.id}:{position}",
                        ticket_id=card.ticket_id, seq=card.last_seq, at=at,
                        commitment_id=c.id, kind=kind, messages=tuple(evidence))
