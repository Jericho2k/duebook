"""Simulated tickets with planted events and the labels that go with them.

A benchmark needs tickets where the right answer is known. Real tickets do not
come with one, and labelling them by hand does not scale, so tickets are built
the other way round: the events are decided first — a promise that is kept, a
promise that is broken, a fact that changes, a disagreement, a failed fix
suggested a second time — and the conversation is written around them. The
labels are then true by construction.

Everything is driven by a seed: ``generate(seed, count)`` returns the same
tickets every time, so two extractors, or two versions of one, are compared on
identical input.

Each ticket also carries the proposals a perfect extractor would make. Running
those through the real reducer must score 1.0; that is the check that the
generator, the reducer and the scorer agree with each other before any model
is measured.

The text here comes from templates. Templates make the labels certain and the
language predictable, and an extractor should not be measured only on phrasing
its own authors wrote — see ``rewrite`` for putting the same planted events
into a different model's words.

Gold deadlines are resolved by ``temporal.resolve``, the same code the system
uses. That is intended: the benchmark measures whether the extractor read the
deadline expression correctly, not date arithmetic, which has its own tests.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum

from ..card import Party, StepOutcome
from ..events import Actor, AssigneeChanged, Event, MessagePosted, Role
from ..proposals import (
    AskQuestion,
    AssertFact,
    MakeCommitment,
    Proposal,
    RecordStep,
    ResolveCommitment,
    SetStepOutcome,
)
from ..temporal import (
    DayRef,
    DueExpr,
    Offset,
    Period,
    PeriodEnd,
    Unit,
    WeekdayRef,
    WorkCalendar,
    resolve,
)


class Fate(StrEnum):
    #: Confirmed done by the other side before the deadline.
    KEPT = "kept"
    #: The deadline passed and nothing was done.
    BROKEN = "broken"
    #: Still pending when the ticket ends.
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class GoldCommitment:
    seq: int
    owner: Party
    action: str
    due_at: datetime | None
    fate: Fate


@dataclass(frozen=True, slots=True)
class GoldChange:
    slot: str
    old: str
    new: str
    seq: int


@dataclass(frozen=True, slots=True)
class GoldContradiction:
    slot: str
    seqs: tuple[int, int]


@dataclass(frozen=True, slots=True)
class GoldRepeatedFix:
    action: str
    failed_seq: int
    #: The message that suggests the failed step again.
    repeated_seq: int


@dataclass(frozen=True, slots=True)
class Gold:
    commitments: tuple[GoldCommitment, ...] = ()
    changes: tuple[GoldChange, ...] = ()
    contradictions: tuple[GoldContradiction, ...] = ()
    repeated_fixes: tuple[GoldRepeatedFix, ...] = ()


@dataclass(frozen=True, slots=True)
class SimTicket:
    ticket_id: str
    events: tuple[Event, ...]
    #: What a perfect extractor proposes, per event, in the same order.
    proposals: tuple[tuple[Proposal, ...], ...]
    gold: Gold
    #: The moment the ticket is judged at: after its last event.
    end: datetime
    #: Per message event id, the spans a rewrite must keep word for word.
    anchors: dict[str, tuple[str, ...]] = field(default_factory=dict)


#: The templates below are written in the feminine; the customers match them.
CUSTOMERS = [Actor("cust-anna", Role.CUSTOMER, "Анна"), Actor("cust-maria", Role.CUSTOMER, "Мария")]
AGENTS = [
    Actor("agent-ivan", Role.AGENT, "Иван"),
    Actor("agent-olga", Role.AGENT, "Ольга"),
    Actor("agent-pavel", Role.AGENT, "Павел"),
]

#: (phrase, what it means). Only phrasings whose reading is not in doubt.
DEADLINES: list[tuple[str, DueExpr]] = [
    ("в течение 2 часов", Offset(2, Unit.HOUR)),
    ("через 3 часа", Offset(3, Unit.HOUR)),
    ("в течение 3 рабочих дней", Offset(3, Unit.BUSINESS_DAY)),
    ("завтра до 15:00", DayRef(1, time(15, 0))),
    ("сегодня до 18:00", DayRef(0, time(18, 0))),
    ("до пятницы", WeekdayRef(4)),
    ("до среды", WeekdayRef(2)),
    ("до конца дня", PeriodEnd(Period.DAY)),
    ("до конца недели", PeriodEnd(Period.WEEK)),
]

#: (owner, sentence with {due}, action, how the debtor's side reports it done).
PROMISES = [
    (Party.COMPANY, "Мы вернём вам деньги {due}", "вернуть деньги", "Деньги пришли, спасибо"),
    (Party.AGENT, "Я перезвоню вам {due}", "перезвонить", "Спасибо, что перезвонили"),
    (Party.COMPANY, "Отправим вам замену {due}", "отправить замену", "Замену получила, спасибо"),
    (Party.AGENT, "Уточню на складе и напишу вам {due}", "уточнить на складе и написать",
     "Ответ про склад получила, спасибо"),
    (Party.COMPANY, "Пришлём исправленный счёт {due}", "прислать исправленный счёт",
     "Счёт получила, всё верно"),
]  # fmt: skip

COMPLAINTS = [
    "Вы обещали {action} — и ничего! Сколько можно ждать?",
    "Мне обещали {action}, срок прошёл. Что происходит?",
]
FILLERS = ["Спасибо за ответ.", "Хорошо, поняла.", "Добрый день!", "Ясно, жду."]
FIXES = ["перезайти в приложение", "очистить кэш браузера", "переустановить приложение"]
PLANS = ["Базовый", "Про", "Бизнес"]


@dataclass(slots=True)
class _Builder:
    ticket_id: str
    rng: random.Random
    cal: WorkCalendar
    now: datetime
    customer: Actor
    agent: Actor
    events: list[Event] = field(default_factory=list)
    proposals: list[tuple[Proposal, ...]] = field(default_factory=list)
    anchors: dict[str, tuple[str, ...]] = field(default_factory=dict)
    commitments: list[GoldCommitment] = field(default_factory=list)
    changes: list[GoldChange] = field(default_factory=list)
    contradictions: list[GoldContradiction] = field(default_factory=list)
    repeated: list[GoldRepeatedFix] = field(default_factory=list)

    @property
    def seq(self) -> int:
        return len(self.events) + 1

    def say(self, author: Actor, text: str, *proposals: Proposal) -> int:
        seq = self.seq
        self.events.append(
            MessagePosted(id=f"e{seq}", ticket_id=self.ticket_id, seq=seq, at=self.now,
                          author=author, text=text)
        )  # fmt: skip
        self.proposals.append(tuple(proposals))
        self.anchors[f"e{seq}"] = tuple(dict.fromkeys(p.quote for p in proposals))
        return seq

    def assign(self, previous: Actor | None, current: Actor) -> None:
        seq = self.seq
        self.events.append(
            AssigneeChanged(id=f"e{seq}", ticket_id=self.ticket_id, seq=seq, at=self.now,
                            previous=previous.id if previous else None, current=current.id)
        )  # fmt: skip
        self.proposals.append(())
        self.agent = current

    def wait(self, low: int, high: int) -> None:
        self.now += timedelta(minutes=self.rng.randint(low, high))

    def jump(self, moment: datetime) -> None:
        """Move to a computed moment, kept on whole minutes in the ticket's timezone."""
        self.now = moment.astimezone(self.cal.tz).replace(second=0, microsecond=0)

    def filler(self) -> None:
        if self.rng.random() < 0.5:
            self.wait(1, 20)
            self.say(self.customer, self.rng.choice(FILLERS))


def generate(seed: int, count: int, cal: WorkCalendar | None = None) -> list[SimTicket]:
    """``count`` tickets, fully determined by ``seed``."""
    cal = cal or WorkCalendar()
    return [_ticket(random.Random(f"{seed}:{n}"), f"SIM-{seed}-{n:04d}", cal) for n in range(count)]


def _ticket(rng: random.Random, ticket_id: str, cal: WorkCalendar) -> SimTicket:
    # A working day in January 2027, between 09:00 and 13:00 local time.
    start = datetime(2027, 1, 11 + rng.randrange(5), 9 + rng.randrange(5), rng.randrange(60),
                     tzinfo=cal.tz)  # fmt: skip
    first, second = rng.sample(AGENTS, 2)
    b = _Builder(ticket_id, rng, cal, start, rng.choice(CUSTOMERS), first)

    order = str(rng.randint(1000, 9999))
    b.say(
        b.customer,
        f"Здравствуйте! У меня проблема с заказом {order}.",
        AssertFact("order.number", order, f"заказом {order}"),
    )
    b.assign(None, first)

    beats = [_kept, _broken, _change, _contradiction, _repeated_fix, _no_deadline]
    chosen = rng.sample(beats, rng.randint(2, len(beats)))
    promises = rng.sample(PROMISES, len(PROMISES))
    for beat in chosen:
        b.wait(5, 40)
        if beat in (_kept, _broken, _no_deadline):
            beat(b, promises.pop())
        elif beat is _repeated_fix:
            beat(b, second)
        else:
            beat(b)
        b.filler()

    return SimTicket(
        ticket_id=ticket_id,
        events=tuple(b.events),
        proposals=tuple(b.proposals),
        gold=Gold(tuple(b.commitments), tuple(b.changes), tuple(b.contradictions),
                  tuple(b.repeated)),
        end=b.now + timedelta(hours=1),
        anchors=b.anchors,
    )  # fmt: skip


def _promise(b: _Builder, promise, fate: Fate, with_deadline: bool = True) -> tuple[int, datetime]:
    owner, sentence, action, _ = promise
    if with_deadline:
        # A phrase that would already be in the past here ("today by 18:00",
        # said at 19:00) is not a deadline anybody would state.
        options = [
            (phrase, expr) for phrase, expr in DEADLINES
            if resolve(expr, b.now, b.cal).due_at > b.now + timedelta(minutes=30)
        ]  # fmt: skip
        phrase, expr = b.rng.choice(options)
        due_at = resolve(expr, b.now, b.cal).due_at
        quote = sentence.format(due=phrase)
        proposal = MakeCommitment(owner, action, quote, due=expr, due_quote=phrase)
    else:
        due_at = None
        quote = sentence.format(due="").strip()
        proposal = MakeCommitment(owner, action, quote)
    seq = b.say(b.agent, f"{quote}.", proposal)
    b.commitments.append(GoldCommitment(seq, owner, action, due_at, fate))
    return seq, due_at


def _kept(b: _Builder, promise) -> None:
    seq, due_at = _promise(b, promise, Fate.KEPT)
    # Confirmed by the customer somewhere in the first half of the time allowed.
    b.jump(b.now + (due_at - b.now) * b.rng.uniform(0.1, 0.5))
    confirmation = promise[3]
    b.say(b.customer, f"{confirmation}!", ResolveCommitment(f"e{seq}#0", confirmation))


def _broken(b: _Builder, promise) -> None:
    _, due_at = _promise(b, promise, Fate.BROKEN)
    b.jump(due_at + timedelta(minutes=b.rng.randint(30, 600)))
    # The customer recalls the promise. This is a claim about the company, not
    # a new promise by it: a correct extractor proposes nothing here.
    b.say(b.customer, b.rng.choice(COMPLAINTS).format(action=promise[2]))


def _no_deadline(b: _Builder, promise) -> None:
    _promise(b, promise, Fate.OPEN, with_deadline=False)


def _change(b: _Builder) -> None:
    old, new = b.rng.sample(PLANS, 2)
    b.say(b.customer, f"У меня тариф {old}.", AssertFact("account.plan", old, f"тариф {old}"))
    b.wait(5, 30)
    seq = b.say(
        b.agent,
        f"Перевёл вас на тариф {new}.",
        AssertFact("account.plan", new, f"на тариф {new}", change=True),
    )
    b.changes.append(GoldChange("account.plan", old, new, seq))


def _contradiction(b: _Builder) -> None:
    first = b.say(
        b.customer, "Заказ я так и не получила.",
        AssertFact("order.status", "не получен", "Заказ я так и не получила"),
    )  # fmt: skip
    b.wait(5, 30)
    second = b.say(
        b.agent, "По трекингу заказ вручён.",
        AssertFact("order.status", "вручён", "заказ вручён"),
    )  # fmt: skip
    b.contradictions.append(GoldContradiction("order.status", (first, second)))


def _repeated_fix(b: _Builder, successor: Actor) -> None:
    action = b.rng.choice(FIXES)
    suggested = b.say(
        b.agent, f"Попробуйте {action}.", RecordStep(action, f"Попробуйте {action}")
    )  # fmt: skip
    b.wait(5, 60)
    failed = b.say(
        b.customer, "Сделала, не помогло.",
        SetStepOutcome(f"e{suggested}#0", StepOutcome.FAILED, "не помогло"),
    )  # fmt: skip
    b.wait(30, 240)
    previous = b.agent
    b.assign(previous, successor)
    b.wait(5, 30)
    # The new operator has not read the thread and suggests the same thing.
    repeated = b.say(
        b.agent, f"Попробуйте {action}. Помогло?",
        RecordStep(action, f"Попробуйте {action}"),
        AskQuestion("Помогло?", "Помогло?"),
    )  # fmt: skip
    b.repeated.append(GoldRepeatedFix(action, failed, repeated))
