"""A tiny ticket builder so tests read like the conversation they describe."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from duebook import (
    Actor,
    AssigneeChanged,
    Card,
    MessagePosted,
    Party,
    Role,
    StepOutcome,
    WorkCalendar,
    apply,
)
from duebook.events import Event
from duebook.proposals import (
    AnswerQuestion,
    AskQuestion,
    AssertFact,
    MakeCommitment,
    RecordStep,
    SetStepOutcome,
)
from duebook.reducer import Outcome
from duebook.temporal import DayRef, WeekdayRef

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
        #: Every event with the proposals made for it, for tests that store a ticket.
        self.log: list[tuple[Event, tuple]] = []

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
        self.log.append((event, tuple(proposals)))
        self.last = apply(self.card, event, proposals, CAL)
        self.card = self.last.card
        return self.last


def story() -> Ticket:
    """A ticket that exercises every part of a card.

    Monday: Anna reports an unpaid-for delivery, Ivan promises a refund by
    Friday and a call tomorrow, tries a fix that fails, and the two disagree
    about the delivery status. The ticket then passes to Olga.
    """
    t = Ticket()
    t.assign(None, IVAN.id)
    t.say(
        ANNA,
        "Заказ 4815 оплатила 5 января, он не пришёл. Тариф Базовый.",
        AssertFact("order.number", "4815", "Заказ 4815"),
        AssertFact("order.status", "не пришёл", "он не пришёл"),
        AssertFact("plan", "Базовый", "Тариф Базовый"),
    )
    t.wait(minutes=20)
    t.say(
        IVAN,
        "По трекингу заказ вручён. Попробуйте перезайти в приложение. Какой адрес доставки?",
        AssertFact("order.status", "вручён", "заказ вручён"),
        RecordStep("перезайти в приложение", "Попробуйте перезайти в приложение"),
        AskQuestion("Какой адрес доставки?", "Какой адрес доставки?"),
    )
    t.wait(minutes=15)
    t.say(
        ANNA,
        "Перезашла, не помогло. Пришлю скриншот сегодня.",
        SetStepOutcome("e3#1", StepOutcome.FAILED, "Перезашла, не помогло"),
        MakeCommitment(
            Party.CUSTOMER, "прислать скриншот", "Пришлю скриншот сегодня", due=DayRef(0),
            due_quote="сегодня",
        ),
    )  # fmt: skip
    t.say(
        IVAN,
        "Перевёл вас на тариф Про. Вернём деньги до пятницы, завтра до 15:00 перезвоню.",
        AssertFact("plan", "Про", "на тариф Про", change=True),
        MakeCommitment(
            Party.COMPANY, "вернуть деньги", "Вернём деньги до пятницы", due=WeekdayRef(4),
            due_quote="до пятницы",
        ),
        MakeCommitment(
            Party.AGENT, "перезвонить", "завтра до 15:00 перезвоню", due=DayRef(1, time(15, 0)),
            due_quote="завтра до 15:00",
        ),
    )  # fmt: skip
    t.wait(minutes=5)
    t.say(
        ANNA,
        "Адрес: Москва, Лесная 12. Когда именно придут деньги?",
        AnswerQuestion("e3#2", "Москва, Лесная 12"),
        AssertFact("delivery.address", "Москва, Лесная 12", "Москва, Лесная 12"),
        AskQuestion("Когда именно придут деньги?", "Когда именно придут деньги?"),
    )
    t.assign(IVAN.id, OLGA.id)
    return t


def promise(
    ticket_id: str, start: datetime = START, text: str = "Перезвоню вам через 2 часа."
) -> Ticket:
    """An assigned ticket holding one agent promise due two hours after ``start``."""
    t = Ticket(ticket_id, start)
    t.assign(None, IVAN.id)
    t.say(
        IVAN,
        text,
        MakeCommitment(Party.AGENT, "перезвонить", text.rstrip("."), due_quote="через 2 часа"),
    )
    return t


def persist(store, ticket: Ticket, now: datetime | None = None) -> None:
    """Record a ticket's whole log, each event as of its own time unless ``now`` is given."""
    for event, proposals in ticket.log:
        store.record(event, proposals, now=now or event.at)
