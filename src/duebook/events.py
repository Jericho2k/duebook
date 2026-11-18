"""The input of the system: what happened on a ticket, in order.

Events are facts about the helpdesk, not interpretations of it. Everything the
system believes about a ticket is derived from this stream and nothing else,
which is what makes a card replayable, auditable and comparable between runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    BOT = "bot"
    SYSTEM = "system"


class Side(StrEnum):
    """Which party to the ticket a statement comes from.

    SYSTEM is neither: a billing callback or a status change is not anyone's
    word, so it is the only source allowed to verify a claim made by either side.
    """

    CUSTOMER = "customer"
    COMPANY = "company"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Actor:
    id: str
    role: Role
    name: str = ""

    @property
    def side(self) -> Side:
        if self.role is Role.CUSTOMER:
            return Side.CUSTOMER
        if self.role is Role.SYSTEM:
            return Side.SYSTEM
        return Side.COMPANY


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Common envelope. ``seq`` is the ticket-local order and the idempotency key."""

    id: str
    ticket_id: str
    seq: int
    at: datetime

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("event time must be timezone-aware")
        if self.seq < 1:
            raise ValueError("seq starts at 1")


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagePosted(Event):
    author: Actor
    text: str
    #: False for an internal note the customer never saw.
    public: bool = True


def other_side(event: Event) -> Side | None:
    """Only shared conversation turns supply evidence for a reply."""
    if isinstance(event, MessagePosted) and event.public:
        return {Side.CUSTOMER: Side.COMPANY, Side.COMPANY: Side.CUSTOMER}.get(event.author.side)
    return None


def remember_turn(turns: dict[Side, MessagePosted], event: Event) -> None:
    if isinstance(event, MessagePosted) and event.public and event.text.strip():
        turns[event.author.side] = event


@dataclass(frozen=True, slots=True, kw_only=True)
class AssigneeChanged(Event):
    previous: str | None
    current: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusChanged(Event):
    previous: str
    current: str
