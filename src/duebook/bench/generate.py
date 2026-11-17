"""Simulated tickets with planted events and the labels that go with them.

A benchmark needs tickets where the right answer is known. Real tickets do not
come with one, and labelling them by hand does not scale, so tickets are built
the other way round: the events are decided first — a promise that is kept, a
promise that is broken, a fact that changes, a disagreement, a failed fix
suggested a second time — and the conversation is written around them. The
labels are then true by construction.

Long tickets are mostly not about anything. A real ticket that runs for a week
is a handful of things that matter buried in status pings, thanks and "any
news?". So a ticket here is planned in two passes:

1. the planted events are laid out on a multi-day timeline, each at a moment
   that makes its label true — a kept promise is confirmed before its deadline,
   a broken one is complained about after it, a fix that failed is suggested
   again by the operator who took the ticket over;
2. filler is scattered across the same span until the ticket reaches its
   length. Filler carries no promise, fact or failed step, so it changes no
   label; it only makes the planted events harder to find and the transcript
   longer to read.

Planted events are spread over the whole ticket on purpose. The first promise
with a long deadline is made early and kept or broken late, so a method that
only remembers the recent past loses it.

Everything is driven by a seed: ``generate(seed, count)`` returns the same
tickets every time, so two extractors, or two versions of one, are compared on
identical input. ``SPLITS`` fixes which seeds are for development and which
for the reported numbers.

Each ticket also carries the proposals a perfect extractor would make. Running
those through the real reducer must score 1.0 on every measure; that is the
check that the generator, the reducer and the scorer agree with each other
before any model is measured.

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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum

from ..card import Party, StepOutcome
from ..events import Actor, AssigneeChanged, Event, MessagePosted, Role, Side
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
    DateRef,
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
    #: The message in which the other side confirmed it done (KEPT only).
    resolved_seq: int | None = None


@dataclass(frozen=True, slots=True)
class GoldFact:
    """A value somebody stated, and how to recognise it in anyone's words.

    Methods name facts differently — one says ``account.plan``, another
    "тариф клиента" — so a fact is recognised by its value, not its name.
    ``key`` holds the tokens that identify the value: a number must appear
    exactly, a word by its stem, so "Премиум" is found in "на тарифе Премиум".
    """

    slot: str
    value: str
    key: tuple[str, ...]
    seq: int
    side: Side


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
    #: (customer's value, company's value).
    values: tuple[str, str] = ("", "")
    #: When one side conceded, and the value that stood. None while open.
    settled_seq: int | None = None
    winner: str | None = None

    def open_at(self, seq: int) -> bool:
        return self.seqs[1] <= seq and (self.settled_seq is None or self.settled_seq > seq)


@dataclass(frozen=True, slots=True)
class GoldRepeatedFix:
    action: str
    failed_seq: int
    #: The message that suggests the failed step again.
    repeated_seq: int
    #: The message that first suggested it.
    suggested_seq: int = 0
    #: Stems by which the step is recognised in a paraphrase, any one suffices.
    key: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Gold:
    commitments: tuple[GoldCommitment, ...] = ()
    changes: tuple[GoldChange, ...] = ()
    contradictions: tuple[GoldContradiction, ...] = ()
    repeated_fixes: tuple[GoldRepeatedFix, ...] = ()
    #: Every value planted as a fact, including the ones later replaced.
    facts: tuple[GoldFact, ...] = ()
    #: The seq of every handoff from one operator to another.
    handoffs: tuple[int, ...] = ()


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

    @property
    def messages(self) -> int:
        return sum(isinstance(e, MessagePosted) for e in self.events)


# ------------------------------------------------------------- profiles ----


@dataclass(frozen=True, slots=True)
class Profile:
    """How long a ticket is and how much is planted in it. Ranges are inclusive."""

    name: str
    #: Messages, not counting assignee changes.
    messages: tuple[int, int]
    #: Calendar days from the first message to the last planned one.
    days: tuple[int, int]
    #: Handoffs from one operator to another, after the first assignment.
    handoffs: tuple[int, int]
    kept: tuple[int, int]
    broken: tuple[int, int]
    #: Promises with no deadline ("we'll look into it").
    vague: tuple[int, int]
    #: Promises whose deadline is still ahead when the ticket ends.
    pending: tuple[int, int]
    #: Customer promises, confirmed by the company.
    customer: tuple[int, int]
    #: How many of the four fact beats (two changes, two disputes) to plant.
    fact_beats: tuple[int, int]
    #: Failed fixes suggested again after a handoff; at most one per handoff.
    fixes: tuple[int, int]


STANDARD = Profile(
    "standard", messages=(50, 64), days=(3, 8), handoffs=(2, 3), kept=(1, 2), broken=(1, 2),
    vague=(0, 1), pending=(0, 1), customer=(0, 1), fact_beats=(2, 4), fixes=(1, 2),
)  # fmt: skip
LONG = Profile(
    "long", messages=(200, 230), days=(18, 26), handoffs=(3, 4), kept=(3, 5), broken=(3, 4),
    vague=(1, 2), pending=(1, 1), customer=(1, 2), fact_beats=(4, 4), fixes=(2, 3),
)  # fmt: skip
PROFILES = {p.name: p for p in (STANDARD, LONG)}


@dataclass(frozen=True, slots=True)
class Split:
    """A fixed set of tickets. Prompts and code are tuned on ``dev`` only;
    ``test`` is run once, for the reported numbers."""

    name: str
    seed: int
    count: int
    profile: Profile = STANDARD

    def tickets(self, cal: WorkCalendar | None = None) -> list[SimTicket]:
        return generate(self.seed, self.count, cal, self.profile)


SPLITS = {
    s.name: s
    for s in (
        Split("dev", seed=101, count=6),
        Split("test", seed=202, count=20),
        # Two tickets of 200+ messages, to show how each method's cost scales.
        Split("long", seed=303, count=2, profile=LONG),
    )
}


# --------------------------------------------------------- the cast, text ----

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
#: Deadlines days away, for promises made early and settled late.
LONG_DEADLINES: list[tuple[str, DueExpr]] = [
    ("в течение 3 рабочих дней", Offset(3, Unit.BUSINESS_DAY)),
    ("в течение 5 рабочих дней", Offset(5, Unit.BUSINESS_DAY)),
    ("через неделю", Offset(1, Unit.WEEK)),
]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря"]  # fmt: skip

#: (owner, sentence with {due}, action, how the other side confirms it done).
PROMISES = [
    (Party.COMPANY, "Мы вернём вам деньги {due}", "вернуть деньги", "Деньги пришли, спасибо"),
    (Party.AGENT, "Я перезвоню вам {due}", "перезвонить", "Спасибо, что перезвонили"),
    (Party.COMPANY, "Отправим вам замену {due}", "отправить замену", "Замену получила, спасибо"),
    (Party.AGENT, "Уточню на складе и напишу вам {due}", "уточнить на складе и написать",
     "Ответ про склад получила, спасибо"),
    (Party.COMPANY, "Пришлём исправленный счёт {due}", "прислать исправленный счёт",
     "Счёт получила, всё верно"),
    (Party.COMPANY, "Начислим вам компенсацию бонусами {due}", "начислить компенсацию бонусами",
     "Бонусы вижу, спасибо"),
    (Party.AGENT, "Я проверю статус возврата и отпишусь {due}",
     "проверить статус возврата и отписаться", "Спасибо, что отписались по возврату"),
    (Party.COMPANY, "Курьер заберёт товар {due}", "забрать товар курьером",
     "Курьер товар забрал, спасибо"),
    (Party.COMPANY, "Пришлём вам акт сверки {due}", "прислать акт сверки",
     "Акт сверки получила, спасибо"),
    (Party.AGENT, "Передам вопрос в бухгалтерию и сообщу результат {due}",
     "сообщить результат из бухгалтерии", "Ответ бухгалтерии получила, спасибо"),
    (Party.COMPANY, "Восстановим доступ к личному кабинету {due}",
     "восстановить доступ к личному кабинету", "Доступ к кабинету появился, спасибо"),
    (Party.COMPANY, "Вышлем вам трек-номер посылки {due}", "выслать трек-номер",
     "Трек-номер получила, спасибо"),
]  # fmt: skip
#: The customer's own promises, confirmed by the company.
CUSTOMER_PROMISES = [
    (Party.CUSTOMER, "Пришлю скриншот ошибки {due}", "прислать скриншот ошибки",
     "Скриншот получили, спасибо"),
    (Party.CUSTOMER, "Пришлю фото чека {due}", "прислать фото чека", "Фото чека получили, спасибо"),
    (Party.CUSTOMER, "Заполню заявление на возврат {due}", "заполнить заявление на возврат",
     "Заявление получили, спасибо"),
]  # fmt: skip
#: Deadlines a customer gives for their own errand.
CUSTOMER_DEADLINES = [p for p in DEADLINES if isinstance(p[1], DayRef | PeriodEnd)]

COMPLAINTS = [
    "Вы обещали {action} — и ничего! Сколько можно ждать?",
    "Мне обещали {action}, срок прошёл. Что происходит?",
    "Срок давно прошёл, а мне обещали {action}. Когда?",
]
#: (step, the customer's report that it failed, stems that recognise the step
#: in a paraphrase — any one suffices).
FIXES = [
    ("перезайти в приложение", "Перезашла в приложение — не помогло.", ("перезаш", "перезай")),
    ("очистить кэш браузера", "Кэш очистила, не помогло.", ("кэш", "кеш")),
    ("переустановить приложение", "Переустановила приложение, не помогло.", ("переустан",)),
    ("обновить приложение до последней версии", "Обновила приложение, не помогло.", ("обнов",)),
    ("сбросить пароль", "Пароль сбросила, не помогло.", ("парол",)),
    ("перезагрузить телефон", "Перезагрузила телефон — не помогло.", ("перезагру",)),
]
#: Plan names chosen not to occur as ordinary words, so a value is unambiguous.
PLANS = ["Лайт", "Ультра", "Премиум", "Платинум"]
STREETS = [("Лесная", "лесн"), ("Садовая", "садов"), ("Полевая", "полев"),
           ("Речная", "речн"), ("Озёрная", "озерн"), ("Берёзовая", "березов")]  # fmt: skip
PRODUCTS = [("чайник", "KT"), ("пылесос", "VC"), ("блендер", "BL"), ("утюг", "IR")]

#: Filler: says something, changes nothing. No promise, fact, step or outcome.
CUSTOMER_FILLER = [
    "Добрый день! Есть новости по моему обращению?",
    "Спасибо.",
    "Хорошо, жду.",
    "Понятно.",
    "Напоминаю о себе.",
    "Подскажите, есть какие-нибудь новости?",
    "Ок, спасибо за информацию.",
    "Здравствуйте, я всё ещё жду ответа.",
    "Ясно.",
    "Спасибо, что держите в курсе.",
    "Добрый день. Что там с моим вопросом?",
    "Хорошо, поняла.",
    "Ладно.",
    "Есть движение по заявке?",
]
AGENT_FILLER = [
    "Добрый день! Ваше обращение у нас в работе.",
    "Спасибо за ожидание.",
    "Новой информации пока нет.",
    "Здравствуйте! Видим ваше сообщение.",
    "Приносим извинения за ожидание.",
    "Пожалуйста, обращайтесь.",
    "Спасибо за терпение.",
    "Благодарим за обращение.",
    "Понимаю ваше беспокойство.",
    "Добрый день! Заявка на рассмотрении.",
]


# -------------------------------------------------------------- planning ----


@dataclass(slots=True)
class _Item:
    """A planned message (or handoff) at a moment, written when its turn comes."""

    at: datetime
    order: int
    write: Callable[[_Builder], None]
    message: bool = True


@dataclass(slots=True)
class _Builder:
    ticket_id: str
    rng: random.Random
    cal: WorkCalendar
    now: datetime
    customer: Actor
    agent: Actor | None = None
    events: list[Event] = field(default_factory=list)
    proposals: list[tuple[Proposal, ...]] = field(default_factory=list)
    anchors: dict[str, tuple[str, ...]] = field(default_factory=dict)
    commitments: list[GoldCommitment] = field(default_factory=list)
    changes: list[GoldChange] = field(default_factory=list)
    contradictions: list[GoldContradiction] = field(default_factory=list)
    repeated: list[GoldRepeatedFix] = field(default_factory=list)
    facts: list[GoldFact] = field(default_factory=list)
    handoffs: list[int] = field(default_factory=list)

    @property
    def seq(self) -> int:
        return len(self.events) + 1

    def say(self, role: Role, text: str, *proposals: Proposal) -> int:
        author = self.customer if role is Role.CUSTOMER else self.agent
        seq = self.seq
        self.events.append(
            MessagePosted(id=f"e{seq}", ticket_id=self.ticket_id, seq=seq, at=self.now,
                          author=author, text=text)
        )  # fmt: skip
        self.proposals.append(tuple(proposals))
        self.anchors[f"e{seq}"] = tuple(dict.fromkeys(p.quote for p in proposals))
        return seq

    def assign(self, current: Actor) -> None:
        seq = self.seq
        previous = self.agent
        self.events.append(
            AssigneeChanged(id=f"e{seq}", ticket_id=self.ticket_id, seq=seq, at=self.now,
                            previous=previous.id if previous else None, current=current.id)
        )  # fmt: skip
        self.proposals.append(())
        if previous is not None:
            self.handoffs.append(seq)
        self.agent = current

    def fact(self, role: Role, text: str, slot: str, value: str, quote: str,
             key: tuple[str, ...], change: bool = False) -> int:  # fmt: skip
        seq = self.say(role, text, AssertFact(slot, value, quote, change))
        side = Side.CUSTOMER if role is Role.CUSTOMER else Side.COMPANY
        self.facts.append(GoldFact(slot, value, key, seq, side))
        return seq


@dataclass(slots=True)
class _Plan:
    rng: random.Random
    cal: WorkCalendar
    start: datetime
    span: timedelta
    items: list[_Item] = field(default_factory=list)

    def add(self, at: datetime, write: Callable[[_Builder], None], message: bool = True) -> None:
        self.items.append(_Item(_minute(at, self.cal), len(self.items), write, message))

    def moment(self, low: float, high: float) -> datetime:
        """A moment between two fractions of the span, during working hours."""
        lo, hi = self.start + self.span * low, self.start + self.span * high
        return self.within(lo, hi, Role.AGENT)

    def within(self, lo: datetime, hi: datetime, role: Role) -> datetime:
        """A moment in (lo, hi), preferably one when ``role`` would be awake."""
        lo, hi = lo + timedelta(minutes=1), max(hi, lo + timedelta(minutes=2))
        pick = lo
        for _ in range(30):
            pick = lo + (hi - lo) * self.rng.random()
            if _awake(pick, role, self.cal):
                break
        return _minute(pick, self.cal) if _minute(pick, self.cal) > lo else lo

    def after(self, moment: datetime, role: Role) -> datetime:
        """The first moment at or after ``moment`` when ``role`` would write."""
        local = moment.astimezone(self.cal.tz)
        first, last = _HOURS[role]
        if local.hour < first:
            local = local.replace(hour=first, minute=self.rng.randrange(60))
        elif local.hour >= last:
            local = (local + timedelta(days=1)).replace(hour=first, minute=self.rng.randrange(60))
        return _minute(local, self.cal)


#: When each side writes, local time: [first hour, last hour).
_HOURS = {Role.CUSTOMER: (8, 23), Role.AGENT: (9, 20)}


def _awake(moment: datetime, role: Role, cal: WorkCalendar) -> bool:
    first, last = _HOURS[role]
    return first <= moment.astimezone(cal.tz).hour < last


def _minute(moment: datetime, cal: WorkCalendar) -> datetime:
    return moment.astimezone(cal.tz).replace(second=0, microsecond=0)


def generate(
    seed: int, count: int, cal: WorkCalendar | None = None, profile: Profile = STANDARD
) -> list[SimTicket]:
    """``count`` tickets, fully determined by ``seed`` and ``profile``."""
    cal = cal or WorkCalendar()
    tag = "" if profile is STANDARD else f"{profile.name[0].upper()}"
    return [
        _ticket(random.Random(f"{seed}:{profile.name}:{n}"), f"SIM-{seed}-{tag}{n:04d}", cal,
                profile)
        for n in range(count)
    ]  # fmt: skip


def _ticket(rng: random.Random, ticket_id: str, cal: WorkCalendar, profile: Profile) -> SimTicket:
    # A working day in January 2027, between 09:00 and 13:00 local time.
    start = datetime(2027, 1, 11 + rng.randrange(5), 9 + rng.randrange(5), rng.randrange(60),
                     tzinfo=cal.tz)  # fmt: skip
    span = timedelta(days=rng.randint(*profile.days), hours=rng.randrange(8))
    plan = _Plan(rng, cal, start, span)
    customer = rng.choice(CUSTOMERS)

    # Who holds the ticket, in turn: never the same operator twice in a row.
    handoffs = rng.randint(*profile.handoffs)
    agents = [rng.choice(AGENTS)]
    for _ in range(handoffs):
        agents.append(rng.choice([a for a in AGENTS if a != agents[-1]]))

    order = str(rng.randint(100_000, 999_999))
    plan.add(start, lambda b: b.fact(
        Role.CUSTOMER, f"Здравствуйте! У меня проблема с заказом {order}.",
        "order.number", order, f"заказом {order}", (order,),
    ))  # fmt: skip
    plan.add(start, lambda b: b.assign(agents[0]), message=False)
    handoff_times = []
    for i, agent in enumerate(agents[1:], 1):
        at = plan.moment((i - 0.15) / (handoffs + 1), (i + 0.15) / (handoffs + 1))
        handoff_times.append(at)
        plan.add(at, lambda b, agent=agent: b.assign(agent), message=False)

    promises = rng.sample(PROMISES, len(PROMISES))
    kept, broken = rng.randint(*profile.kept), rng.randint(*profile.broken)
    fates = [Fate.KEPT] * kept + [Fate.BROKEN] * broken
    rng.shuffle(fates)
    for n, fate in enumerate(fates):
        if n == 0:
            # Made early, with days to run: settled late in the ticket.
            _deadline_promise(plan, promises.pop(), fate, plan.moment(0.02, 0.3), LONG_DEADLINES)
        else:
            _deadline_promise(plan, promises.pop(), fate, plan.moment(0.02, 0.85), DEADLINES)
    for _ in range(rng.randint(*profile.vague)):
        _vague_promise(plan, promises.pop(), plan.moment(0.02, 0.95))
    for promise in rng.sample(CUSTOMER_PROMISES, rng.randint(*profile.customer)):
        _customer_promise(plan, promise, plan.moment(0.02, 0.85))

    # At least one change and one dispute in every ticket, then more at random.
    changes, disputes = [_plan_change, _address_change], [_amount_dispute, _model_dispute]
    beats = [changes.pop(rng.randrange(2)), disputes.pop(rng.randrange(2))]
    beats += rng.sample(changes + disputes, rng.randint(*profile.fact_beats) - 2)
    for beat in beats:
        beat(plan)
    fixes = rng.sample(FIXES, rng.randint(*profile.fixes))
    chosen = rng.sample(range(len(handoff_times)), min(len(fixes), len(handoff_times)))
    for fix, i in zip(fixes, chosen, strict=False):
        # Suggested by the operator who hands over at handoff i, so the one who
        # repeats it is someone else.
        since = handoff_times[i - 1] if i else start
        _repeated_fix(plan, *fix, since, handoff_times[i])

    # Planned last: by now it is known when the ticket ends, and this promise's
    # deadline must still be ahead then.
    pending = rng.randint(*profile.pending)
    for _ in range(pending):
        _pending_promise(plan, promises.pop(), plan.moment(0.86, 0.97))

    planted = sum(item.message for item in plan.items)
    target = rng.randint(*profile.messages)
    for _ in range(max(target - planted, 0)):
        role = Role.CUSTOMER if rng.random() < 0.55 else Role.AGENT
        at = plan.within(start + timedelta(minutes=5), start + span, role)
        text = rng.choice(CUSTOMER_FILLER if role is Role.CUSTOMER else AGENT_FILLER)
        plan.add(at, lambda b, role=role, text=text: b.say(role, text))

    b = _Builder(ticket_id, rng, cal, start, customer)
    for item in sorted(plan.items, key=lambda i: (i.at, i.order)):
        b.now = item.at
        item.write(b)

    return SimTicket(
        ticket_id=ticket_id,
        events=tuple(b.events),
        proposals=tuple(b.proposals),
        gold=Gold(tuple(b.commitments), tuple(b.changes), tuple(b.contradictions),
                  tuple(b.repeated), tuple(b.facts), tuple(b.handoffs)),
        end=b.now + timedelta(hours=1),
        anchors=b.anchors,
    )  # fmt: skip


def _end(plan: _Plan) -> datetime:
    """When the ticket will end given what is planned so far (filler stays inside the span)."""
    return max([plan.start + plan.span, *(i.at for i in plan.items)]) + timedelta(hours=1)


# ----------------------------------------------------------------- beats ----


def _choose_deadline(
    plan: _Plan, at: datetime, options: list[tuple[str, DueExpr]], after: datetime
) -> tuple[str, DueExpr, datetime]:
    # A phrase that would already be in the past ("today by 18:00", said at
    # 19:00) is not a deadline anybody would state.
    usable = [(p, e) for p, e in options if resolve(e, at, plan.cal).due_at > after]
    phrase, expr = plan.rng.choice(usable)
    return phrase, expr, resolve(expr, at, plan.cal).due_at


def _date_deadline(at: datetime, days: int, cal: WorkCalendar) -> tuple[str, DueExpr]:
    day = (at.astimezone(cal.tz) + timedelta(days=days)).date()
    return f"до {day.day} {_MONTHS[day.month - 1]}", DateRef(day.day, day.month)


def _committed(b: _Builder, role: Role, promise, phrase: str | None, expr, due_at, fate) -> int:
    owner, sentence, action, _ = promise
    if phrase is None:
        quote = sentence.format(due="").strip()
        proposal = MakeCommitment(owner, action, quote)
    else:
        quote = sentence.format(due=phrase)
        proposal = MakeCommitment(owner, action, quote, due=expr, due_quote=phrase)
    seq = b.say(role, f"{quote}.", proposal)
    b.commitments.append(GoldCommitment(seq, owner, action, due_at, fate))
    return seq


def _deadline_promise(plan: _Plan, promise, fate: Fate, at: datetime, options) -> None:
    options = list(options)
    if options is not DEADLINES and plan.rng.random() < 0.3:
        options = [_date_deadline(at, plan.rng.randint(5, 9), plan.cal)]
    phrase, expr, due_at = _choose_deadline(plan, at, options, at + timedelta(minutes=30))
    made: list[int] = []

    def make(b: _Builder) -> None:
        made.append(_committed(b, Role.AGENT, promise, phrase, expr, due_at, fate))

    plan.add(at, make)
    if fate is Fate.KEPT:
        # Confirmed by the customer well inside the time allowed.
        when = plan.within(at + (due_at - at) * 0.15, at + (due_at - at) * 0.7, Role.CUSTOMER)
        plan.add(when, lambda b: _confirm(b, Role.CUSTOMER, promise[3], made[0]))
    else:
        # The customer recalls the promise. This is a claim about the company,
        # not a new promise by it: a correct extractor proposes nothing here.
        when = plan.after(due_at + timedelta(minutes=plan.rng.randint(30, 1500)), Role.CUSTOMER)
        complaint = plan.rng.choice(COMPLAINTS).format(action=promise[2])
        plan.add(when, lambda b: b.say(Role.CUSTOMER, complaint))


def _confirm(b: _Builder, role: Role, confirmation: str, seq: int) -> None:
    done = b.say(role, f"{confirmation}!", ResolveCommitment(f"e{seq}#0", confirmation))
    index = next(i for i, c in enumerate(b.commitments) if c.seq == seq)
    gold = b.commitments[index]
    b.commitments[index] = GoldCommitment(gold.seq, gold.owner, gold.action, gold.due_at,
                                          gold.fate, done)  # fmt: skip


def _vague_promise(plan: _Plan, promise, at: datetime) -> None:
    plan.add(at, lambda b: _committed(b, Role.AGENT, promise, None, None, None, Fate.OPEN))


def _pending_promise(plan: _Plan, promise, at: datetime) -> None:
    after = _end(plan) + timedelta(hours=2)
    options = [*LONG_DEADLINES, _date_deadline(at, plan.rng.randint(8, 12), plan.cal)]
    if not any(resolve(e, at, plan.cal).due_at > after for _, e in options):
        options = [_date_deadline(at, (after - at).days + 2, plan.cal)]
    phrase, expr, due_at = _choose_deadline(plan, at, options, after)
    plan.add(at, lambda b: _committed(b, Role.AGENT, promise, phrase, expr, due_at, Fate.OPEN))


def _customer_promise(plan: _Plan, promise, at: datetime) -> None:
    at = plan.within(at - timedelta(hours=1), at, Role.CUSTOMER)
    phrase, expr, due_at = _choose_deadline(
        plan, at, CUSTOMER_DEADLINES, at + timedelta(minutes=90)
    )
    made: list[int] = []
    plan.add(at, lambda b: made.append(
        _committed(b, Role.CUSTOMER, promise, phrase, expr, due_at, Fate.KEPT)
    ))  # fmt: skip
    # Delivered quickly; the company says it arrived, which is what settles it.
    when = plan.within(at + (due_at - at) * 0.1, at + (due_at - at) * 0.6, Role.AGENT)
    plan.add(when, lambda b: _confirm(b, Role.AGENT, promise[3], made[0]))


def _plan_change(plan: _Plan) -> None:
    """The company changes a value the customer stated: the old one goes stale."""
    old, new = plan.rng.sample(PLANS, 2)
    first = plan.moment(0.02, 0.5)
    second = plan.within(first + timedelta(hours=1), first + plan.span * 0.4, Role.AGENT)
    plan.add(first, lambda b: b.fact(
        Role.CUSTOMER, f"У меня тариф {old}.", "account.plan", old, f"тариф {old}",
        (old.lower(),),
    ))  # fmt: skip

    def change(b: _Builder) -> None:
        seq = b.fact(Role.AGENT, f"Перевёл вас на тариф {new}.", "account.plan", new,
                     f"на тариф {new}", (new.lower(),), change=True)  # fmt: skip
        b.changes.append(GoldChange("account.plan", old, new, seq))

    plan.add(second, change)


def _address_change(plan: _Plan) -> None:
    """The customer updates their own statement: a change, not a dispute."""
    (s1, k1), (s2, k2) = plan.rng.sample(STREETS, 2)
    old = f"ул. {s1}, {plan.rng.randint(2, 99)}"
    new = f"ул. {s2}, {plan.rng.randint(2, 99)}"
    first = plan.moment(0.02, 0.45)
    second = plan.within(first + timedelta(hours=2), first + plan.span * 0.45, Role.CUSTOMER)
    plan.add(first, lambda b: b.fact(
        Role.CUSTOMER, f"Адрес доставки: {old}.", "delivery.address", old, old,
        (k1, old.rsplit(" ", 1)[1]),
    ))  # fmt: skip

    def change(b: _Builder) -> None:
        seq = b.fact(
            Role.CUSTOMER,
            f"Я переехала, новый адрес доставки — {new}.",
            "delivery.address",
            new,
            new,
            (k2, new.rsplit(" ", 1)[1]),
            change=True,
        )
        b.changes.append(GoldChange("delivery.address", old, new, seq))  # fmt: skip

    plan.add(second, change)


def _amount_dispute(plan: _Plan) -> None:
    """The sides disagree about how much was charged; half the time one concedes."""
    said, recorded = (str(n * 100) for n in plan.rng.sample(range(15, 99), 2))
    first = plan.moment(0.02, 0.6)
    second = plan.within(first + timedelta(minutes=5), first + timedelta(hours=3), Role.AGENT)
    seqs: list[int] = []
    plan.add(first, lambda b: seqs.append(b.fact(
        Role.CUSTOMER, f"С карты списали {said} рублей.", "payment.amount", said,
        f"списали {said} рублей", (said,),
    )))  # fmt: skip

    def answer(b: _Builder) -> None:
        seq = b.fact(
            Role.AGENT,
            f"По нашим данным списано {recorded} рублей.",
            "payment.amount",
            recorded,
            f"списано {recorded} рублей",
            (recorded,),
        )
        b.contradictions.append(GoldContradiction("payment.amount", (seqs[0], seq),
                                                  (said, recorded)))  # fmt: skip

    plan.add(second, answer)
    if plan.rng.random() < 0.5:
        return
    third = plan.within(second + timedelta(hours=1), second + timedelta(days=2), Role.AGENT)
    if plan.rng.random() < 0.5:
        role, text, value = (Role.CUSTOMER, f"Проверила выписку — действительно, списали "
                             f"{recorded} рублей.", recorded)  # fmt: skip
        quote = f"списали {recorded} рублей"
    else:
        role, text, value = (Role.AGENT, f"Проверил ещё раз — вы правы, списано {said} рублей.",
                             said)  # fmt: skip
        quote = f"списано {said} рублей"

    def settle(b: _Builder) -> None:
        seq = b.say(role, text, AssertFact("payment.amount", value, quote))
        i = next(i for i, c in enumerate(b.contradictions) if c.slot == "payment.amount")
        gold = b.contradictions[i]
        b.contradictions[i] = GoldContradiction(gold.slot, gold.seqs, gold.values, seq, value)

    plan.add(third, settle)


def _model_dispute(plan: _Plan) -> None:
    """The customer says one model arrived, the company's records another. Stays open."""
    product, prefix = plan.rng.choice(PRODUCTS)
    got, sent = (f"{prefix}-{n}" for n in plan.rng.sample(range(100, 999), 2))
    first = plan.moment(0.02, 0.7)
    second = plan.within(first + timedelta(minutes=5), first + timedelta(hours=5), Role.AGENT)
    seqs: list[int] = []
    plan.add(first, lambda b: seqs.append(b.fact(
        Role.CUSTOMER, f"Мне пришёл {product} модели {got}.", "device.model", got, got,
        (got.split("-")[1],),
    )))  # fmt: skip

    def answer(b: _Builder) -> None:
        seq = b.fact(Role.AGENT, f"По накладной вам отправлен {sent}.", "device.model", sent,
                     sent, (sent.split("-")[1],))  # fmt: skip
        b.contradictions.append(GoldContradiction("device.model", (seqs[0], seq), (got, sent)))

    plan.add(second, answer)


def _repeated_fix(
    plan: _Plan,
    action: str,
    failure: str,
    key: tuple[str, ...],
    since: datetime,
    handoff: datetime,
) -> None:
    """A fix fails, the ticket changes hands, and the new operator suggests it again."""
    lo = max(handoff - timedelta(hours=30), since + timedelta(minutes=10))
    suggested = plan.within(lo, handoff - timedelta(hours=3), Role.AGENT)
    failed = plan.within(suggested + timedelta(minutes=5), suggested + timedelta(hours=2),
                         Role.CUSTOMER)  # fmt: skip
    failed = min(failed, handoff - timedelta(minutes=5))
    repeated = plan.within(handoff + timedelta(minutes=10), handoff + timedelta(hours=3),
                           Role.AGENT)  # fmt: skip
    seqs: list[int] = []
    plan.add(suggested, lambda b: seqs.append(
        b.say(Role.AGENT, f"Попробуйте {action}.", RecordStep(action, f"Попробуйте {action}"))
    ))  # fmt: skip
    plan.add(failed, lambda b: seqs.append(b.say(
        Role.CUSTOMER, failure,
        SetStepOutcome(f"e{seqs[0]}#0", StepOutcome.FAILED, "не помогло"),
    )))  # fmt: skip

    def again(b: _Builder) -> None:
        # The new operator has not read the thread and suggests the same thing.
        seq = b.say(Role.AGENT, f"Попробуйте {action}. Помогло?",
                    RecordStep(action, f"Попробуйте {action}"),
                    AskQuestion("Помогло?", "Помогло?"))  # fmt: skip
        b.repeated.append(GoldRepeatedFix(action, seqs[1], seq, seqs[0], key))

    plan.add(repeated, again)
