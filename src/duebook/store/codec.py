"""The stored form of events, proposals and cards: plain JSON, readable in SQL.

Every dataclass is written as an object tagged with its class name, so a
stored row says what it is without the code that wrote it, and a union such as
a deadline expression can be read back without guessing. Fields a newer
version adds are simply absent from older rows and take their defaults.

This module knows nothing about a database. It is the one place where the
shape of the domain meets the shape of storage, and it is checked by a single
property: ``decode(encode(x)) == x``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, time
from enum import Enum
from functools import cache
from types import NoneType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from .. import card as _card
from .. import events as _events
from .. import proposals as _proposals
from .. import temporal as _temporal
from ..card import Card
from ..events import Event
from ..proposals import Proposal

_TYPES: dict[str, type] = {
    cls.__name__: cls
    for module in (_card, _events, _proposals, _temporal)
    for cls in vars(module).values()
    if dataclasses.is_dataclass(cls) and isinstance(cls, type) and cls.__module__ == module.__name__
}
#: A working calendar is deployment configuration, not ticket data.
_TYPES.pop("WorkCalendar")

TAG = "type"


def encode(value: Any) -> Any:
    """A domain value as JSON-compatible data."""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("refusing to store a naive datetime")
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [encode(item) for item in value]
    if dataclasses.is_dataclass(value) and type(value).__name__ in _TYPES:
        body = {TAG: type(value).__name__}
        for f in serialized_fields(value):
            body[f.name] = encode(getattr(value, f.name))
        return body
    raise TypeError(f"cannot store {type(value).__name__}")


def serialized_fields(value: Any):
    return (f for f in dataclasses.fields(value)
            if not f.metadata.get("omit_default") or getattr(value, f.name) != f.default)


def decode(raw: Any) -> Any:
    """Read back a tagged object written by ``encode``."""
    if not isinstance(raw, dict) or raw.get(TAG) not in _TYPES:
        raise ValueError(f"not a stored object: {str(raw)[:80]!r}")
    cls = _TYPES[raw[TAG]]
    hints = _hints(cls)
    return cls(
        **{
            f.name: _read(raw[f.name], hints[f.name])
            for f in dataclasses.fields(cls)
            if f.name in raw
        }
    )


def decode_event(raw: Any) -> Event:
    return _expect(decode(raw), Event)


def decode_card(raw: Any) -> Card:
    return _expect(decode(raw), Card)


def decode_proposals(raw: Any) -> tuple[Proposal, ...]:
    return tuple(_expect(decode(item), Proposal) for item in raw)


def _expect(value: Any, kind: Any) -> Any:
    if not isinstance(value, kind):
        raise ValueError(f"stored a {type(value).__name__} where another type was expected")
    return value


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _read(raw: Any, hint: Any) -> Any:
    if raw is None:
        return None
    origin = get_origin(hint)
    if origin in (Union, UnionType):
        options = [a for a in get_args(hint) if a is not NoneType]
        # Optional[X] reads as X; a real union is told apart by its tag.
        return _read(raw, options[0]) if len(options) == 1 else decode(raw)
    if origin is tuple:
        args = get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_read(item, args[0]) for item in raw)
        return tuple(_read(item, arg) for item, arg in zip(raw, args, strict=True))
    if hint is datetime:
        return datetime.fromisoformat(raw)
    if hint is time:
        return time.fromisoformat(raw)
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint(raw)
    if dataclasses.is_dataclass(hint):
        return _expect(decode(raw), hint)
    return raw
