"""Durable state around the pure core: event log, card snapshots, alert outbox.

Needs PostgreSQL and psycopg 3 (``pip install duebook[store]``). Nothing in
the core imports this package.

The names below are imported on first use, not with the package, so that
``duebook.store.codec`` — plain JSON, no database — can be used where psycopg
is not installed, as the benchmark does to keep what a run proposed.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

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

__all__ = sorted(_WHERE)


def __getattr__(name: str) -> Any:
    if name not in _WHERE:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{_WHERE[name]}", __name__), name)
