"""Reading an event that arrived over HTTP: the codec's format, checked strictly.

``codec.decode`` trusts its input, because it normally reads back what
``codec.encode`` wrote. A webhook body is written by somebody else, so it is
decoded the same way and then every field is checked against its declared
type: a ``seq`` sent as the string "3" or an author id sent as a number would
otherwise be stored as given, and the event log is append-only — a malformed
event cannot be corrected later, only refused now.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from enum import Enum
from types import NoneType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from ..events import AssigneeChanged, Event, MessagePosted, StatusChanged
from ..store.codec import decode_event


class Invalid(ValueError):
    """The body is not an event in the codec format. The message says why."""


def read_event(raw: Any) -> Event:
    if not isinstance(raw, dict):
        raise Invalid("the body must be a JSON object")
    try:
        event = decode_event(raw)
    except Invalid:
        raise
    except (ValueError, TypeError, KeyError) as error:
        raise Invalid(str(error)) from error
    if not isinstance(event, MessagePosted | AssigneeChanged | StatusChanged):
        raise Invalid("an event must be a MessagePosted, AssigneeChanged or StatusChanged")
    _check(event, type(event), "event")
    if not event.id.strip() or not event.ticket_id.strip():
        raise Invalid("event.id and event.ticket_id must not be empty")
    return event


def _check(value: Any, hint: Any, path: str) -> None:
    origin = get_origin(hint)
    if origin in (Union, UnionType):
        options = get_args(hint)
        if value is None and NoneType in options:
            return
        for option in options:
            if option is NoneType:
                continue
            try:
                _check(value, option, path)
                return
            except Invalid:
                continue
        raise Invalid(f"{path} has the wrong type")
    if dataclasses.is_dataclass(hint):
        if not isinstance(value, hint):
            raise Invalid(f"{path} must be a {hint.__name__}")
        hints = get_type_hints(type(value))
        for f in dataclasses.fields(value):
            _check(getattr(value, f.name), hints[f.name], f"{path}.{f.name}")
        return
    if hint is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif hint in (str, bool, datetime) or (isinstance(hint, type) and issubclass(hint, Enum)):
        ok = isinstance(value, hint)
    else:
        ok = True  # no other field types appear on events
    if not ok:
        name = getattr(hint, "__name__", str(hint))
        raise Invalid(f"{path} must be {name}, got {type(value).__name__}")
