"""A tiny ticket builder so tests read like the conversation they describe."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from duebook import Actor, AssigneeChanged, Card, MessagePosted, Role, WorkCalendar, apply
from duebook.reducer import Outcome

MSK = ZoneInfo("Europe/Moscow")
CAL = WorkCalendar(tz=MSK)

CUSTOMER = Actor("cust-1", Role.CUSTOMER, "Анна")
ANNA = CUSTOMER
IVAN = Actor("agent-ivan", Role.AGENT, "Иван")
OLGA = Actor("agent-olga", Role.AGENT, "Ольга")
BOT = Actor("bot", Role.BOT)
BILLING = Actor("billing", Role.SYSTEM)

#: Monday 11 January 2027, 10:00 Moscow time.
START = datetime(2027, 1, 11, 10, 0, tzinfo=MSK)


class Ticket:
    def __init__(self, ticket_id: str = "T-1", start: datetime = START) -> None:
        self.card = Card(ticket_id)
        self.now = start
        self.last: Outcome | None = None

    def wait(self, **delta) -> Ticket:
        self.now += timedelta(**delta)
        return self

    def say(self, author: Actor, text: str, *proposals, public: bool = True) -> Outcome:
        seq = self.card.last_seq + 1
        event = MessagePosted(
            id=f"e{seq}",
            ticket_id=self.card.ticket_id,
            seq=seq,
            at=self.now,
            author=author,
            text=text,
            public=public,
        )
        return self._apply(event, proposals)

    def assign(self, previous: str | None, current: str | None) -> Outcome:
        seq = self.card.last_seq + 1
        event = AssigneeChanged(
            id=f"e{seq}",
            ticket_id=self.card.ticket_id,
            seq=seq,
            at=self.now,
            previous=previous,
            current=current,
        )
        return self._apply(event, ())

    def _apply(self, event, proposals) -> Outcome:
        self.last = apply(self.card, event, proposals, CAL)
        self.card = self.last.card
        return self.last
