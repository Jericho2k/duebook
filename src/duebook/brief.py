"""The handoff brief: what an operator taking over a ticket has to know, in order.

    brief = compose(card, now, budget=600)
    print(brief.text)

The brief is a rendering of the card and nothing else. No model writes it, so
it cannot say anything the card does not hold, and the same card at the same
moment always gives the same text.

It is ordered by what hurts most if missed:

0. messages nobody could read automatically (decision 0002);
1. promises already broken or about to be;
2. promises that changed hands or have nobody answerable;
3. the remaining unresolved promises;
4. open contradictions between the two sides;
5. current facts;
6. what changed ("was X, now Y");
7. steps that failed, so they are not suggested again;
8. open questions.

A promise the extractor proposed to close, but whose close the reducer did
not admit, is still listed as owed, with "возможно, выполнено по сообщению
№N — подтвердите": the operator decides, the system does not guess.

Every line ends with a reference to the message it rests on — its number in
the ticket, who wrote it, and the quoted words. A line without a source is not
rendered, because there is none: the card does not hold beliefs without one.

The brief is fitted to a token budget by removing whole sections from the
bottom of that order, and it says which were removed and how many items they
held. An operator can act on "there are 3 open questions I was not shown"; a
brief that silently ends cannot be trusted to be complete.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .card import (
    Card,
    Closure,
    Commitment,
    CommitmentState,
    Confidence,
    Fact,
    Party,
    Source,
    StepOutcome,
)
from .events import Side
from .lifecycle import AlertPolicy, Status, status_at
from .reducer import Reason
from .temporal import Precision, WorkCalendar

_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_OWNER = {Party.AGENT: "агент", Party.COMPANY: "компания", Party.CUSTOMER: "клиент"}
_SIDE = {Side.CUSTOMER: "клиент", Side.COMPANY: "компания", Side.SYSTEM: "система"}
_MAYBE = {
    Closure.RESOLVED: "возможно, выполнено",
    Closure.CANCELLED: "возможно, отменено",
    Closure.SUPERSEDED: "возможно, заменено новым обещанием",
}


def estimate_tokens(text: str) -> int:
    """A deliberately pessimistic token count for Russian text.

    Tokenizers differ, and Cyrillic costs more tokens per character than
    English. Two and a half characters per token overestimates for the common
    ones, which errs on the side of a brief that fits. Pass the real
    tokenizer's counter to ``compose`` when the exact figure matters.
    """
    return math.ceil(len(text) / 2.5)


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    #: The messages this line rests on. Never empty.
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class Section:
    key: str
    title: str
    lines: tuple[Line, ...]


@dataclass(frozen=True, slots=True)
class Dropped:
    """A section, or the tail of one, left out to fit the budget."""

    key: str
    title: str
    items: int


@dataclass(frozen=True, slots=True)
class Brief:
    text: str
    tokens: int
    budget: int | None
    sections: tuple[Section, ...]
    dropped: tuple[Dropped, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.dropped

    @property
    def fits(self) -> bool:
        """False only when the budget is too small for even the header and the notice."""
        return self.budget is None or self.tokens <= self.budget


@dataclass(slots=True)
class _Writer:
    """Everything a line needs besides the item itself."""

    card: Card
    now: datetime
    cal: WorkCalendar
    policy: AlertPolicy
    names: Mapping[str, str]
    quote_chars: int
    facts: dict[str, Fact] = field(init=False)

    def __post_init__(self) -> None:
        self.facts = {f.id: f for f in self.card.facts}

    def who(self, actor_id: str | None) -> str:
        return self.names.get(actor_id, actor_id) if actor_id else "никто"

    def ref(self, source: Source) -> str:
        quote = source.quote.strip()
        if len(quote) > self.quote_chars:
            quote = quote[: self.quote_chars].rstrip() + "…"
        return f"[№{source.seq} {self.who(source.author_id)}: «{quote}»]"

    def moment(self, at: datetime, precision: Precision = Precision.TIME) -> str:
        local = at.astimezone(self.cal.tz)
        day = f"{_WEEKDAYS[local.weekday()]} {local:%d.%m}"
        return f"конца {day}" if precision is Precision.DAY else f"{day} {local:%H:%M}"

    # ---------------------------------------------------------- commitments ----

    def commitment(self, c: Commitment, status: Status) -> Line:
        parts = [f"{_OWNER[c.owner]}: {c.action}"]
        sources = [c.source]
        if c.due is not None:
            due_at, precision = c.due.deadline.due_at, c.due.deadline.precision
            if status is Status.BREACHED:
                late = _span(self.now - due_at)
                parts.insert(
                    0, f"ПРОСРОЧЕНО на {late} (срок был до {self.moment(due_at, precision)})"
                )
            elif status is Status.DUE_SOON:
                left = _span(due_at - self.now)
                parts.insert(0, f"осталось {left} (срок до {self.moment(due_at, precision)})")
            else:
                parts.append(f"срок до {self.moment(due_at, precision)}")
            if c.due.confidence is Confidence.LOW:
                parts.append("срок распознан неуверенно, сверьте с сообщением")
        else:
            parts.append("срок не назван")

        if c.state is CommitmentState.CLAIMED and c.resolution is not None:
            parts.append(f"заявлено как выполненное, не подтверждено {self.ref(c.resolution)}")
            sources.append(c.resolution)
        for hint in c.suggestions:
            # A close the reducer would not admit: still owed, but worth a look.
            if hint.reason == Reason.CONTEXT_NAMES_OTHER:
                parts.append(f"подтверждение в сообщении №{hint.source.seq} относится, похоже, "
                             f"к тому, чего нет в карточке — проверьте {self.ref(hint.source)}")
            else:
                parts.append(f"{_MAYBE[hint.kind]} по сообщению №{hint.source.seq} — подтвердите "
                             f"{self.ref(hint.source)}")  # fmt: skip
            sources.append(hint.source)
        if c.owner is not Party.CUSTOMER:
            if c.orphaned:
                parts.append("БЕЗ ОТВЕТСТВЕННОГО")
            else:
                parts.append(f"отвечает {self.who(c.responsible)}")
            if c.inherited_from:
                parts.append(f"раньше отвечал: {self.who(c.inherited_from)}")
        return Line("; ".join(parts) + " " + self.ref(c.source), tuple(sources))

    # ---------------------------------------------------------------- facts ----

    def contradiction(self, first: Fact, second: Fact, slot: str) -> Line:
        said = " — ".join(
            f"{_SIDE[f.source.side]}: «{f.value}» {self.ref(f.source)}" for f in (first, second)
        )
        return Line(f"{slot}: {said}", (first.source, second.source))

    def fact(self, f: Fact) -> Line:
        text = f"{f.slot}: {f.value} (со слов: {_SIDE[f.source.side]}) {self.ref(f.source)}"
        return Line(text, (f.source,))

    def change(self, old: Fact, new: Fact) -> Line:
        text = (
            f"{old.slot}: было «{old.value}» {self.ref(old.source)}, "
            f"стало «{new.value}» {self.ref(new.source)}"
        )
        return Line(text, (old.source, new.source))


def compose(
    card: Card,
    now: datetime,
    *,
    budget: int | None = None,
    cal: WorkCalendar | None = None,
    policy: AlertPolicy | None = None,
    names: Mapping[str, str] | None = None,
    count: Callable[[str], int] = estimate_tokens,
    quote_chars: int = 80,
) -> Brief:
    """Render the card for an operator taking the ticket over at ``now``.

    ``budget`` is the most tokens the text may take, as measured by ``count``;
    None means no limit. ``names`` maps actor ids to display names.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    w = _Writer(card, now, cal or WorkCalendar(), policy or AlertPolicy(), names or {}, quote_chars)
    sections = [s for s in _sections(w) if s.lines]
    header = _header(w)

    kept, dropped = list(sections), []
    while True:
        text = _render(header, kept, dropped, budget)
        if budget is None or count(text) <= budget or not kept:
            break
        last = kept[-1]
        if len(kept) > 1 or len(last.lines) == 1:
            # Least important first: the whole lowest section goes.
            kept.pop()
            gone = len(last.lines)
        else:
            # Only the most important section is left. Its lines are already
            # in order of urgency, so it loses them from the end.
            kept[-1] = Section(last.key, last.title, last.lines[:-1])
            gone = 1
        if dropped and dropped[0].key == last.key:
            dropped[0] = Dropped(last.key, last.title, dropped[0].items + gone)
        else:
            dropped.insert(0, Dropped(last.key, last.title, gone))
    return Brief(text, count(text), budget, tuple(kept), tuple(dropped))


def _sections(w: _Writer) -> list[Section]:
    card = w.card
    open_ = [(c, status_at(c, w.now, w.policy)) for c in card.commitments if c.unresolved]
    urgent = [(c, s) for c, s in open_ if s in (Status.BREACHED, Status.DUE_SOON)]
    # Broken promises first, then the nearest deadlines; within each, what the
    # company owes comes before what the customer owes.
    urgent.sort(
        key=lambda pair: (
            pair[1] is not Status.BREACHED,
            pair[0].owner is Party.CUSTOMER,
            pair[0].due.deadline.due_at,
        )
    )
    rest = [(c, s) for c, s in open_ if s not in (Status.BREACHED, Status.DUE_SOON)]
    moved = [(c, s) for c, s in rest if c.orphaned or c.inherited_from]
    moved.sort(key=lambda pair: not pair[0].orphaned)
    other = [(c, s) for c, s in rest if not (c.orphaned or c.inherited_from)]

    disputes = [
        (w.facts[c.fact_ids[0]], w.facts[c.fact_ids[1]], c.slot)
        for c in card.open_contradictions()
        if all(i in w.facts for i in c.fact_ids)
    ]
    disputed = {i for c in card.open_contradictions() for i in c.fact_ids}
    failed = [s for s in card.steps if s.outcome is StepOutcome.FAILED]
    questions = [q for q in card.questions if q.open]

    return [
        Section("unread", "Не прочитано автоматически",
                tuple(Line(f"Сообщение №{u.source.seq} не удалось прочитать — проверьте его "
                           f"вручную {w.ref(u.source)}", (u.source,))
                      for u in card.unread)),
        Section("urgent", "Просрочено и на подходе",
                tuple(w.commitment(c, s) for c, s in urgent)),
        Section("moved", "Обещания, перешедшие по наследству или оставшиеся без ответственного",
                tuple(w.commitment(c, s) for c, s in moved)),
        Section("open", "Остальные невыполненные обещания",
                tuple(w.commitment(c, s) for c, s in other)),
        Section("contradictions", "Открытые противоречия",
                tuple(w.contradiction(*d) for d in disputes)),
        # A disputed value is not a fact yet; it is shown once, as a dispute.
        Section("facts", "Что известно сейчас",
                tuple(w.fact(f) for f in card.current_facts() if f.id not in disputed)),
        Section("changes", "Что изменилось",
                tuple(w.change(old, new) for old, new in card.changed_facts())),
        Section("failed", "Не помогло — не предлагать снова",
                tuple(
                    Line(
                        f"{s.action} {w.ref(s.outcome_source or s.source)}",
                        (s.outcome_source or s.source,),
                    )
                    for s in failed
                )),
        Section("questions", "Открытые вопросы",
                tuple(
                    Line(f"{q.text} (ответа ждём от: {_SIDE[q.waiting_on]}) {w.ref(q.source)}",
                         (q.source,))
                    for q in questions
                )),
    ]  # fmt: skip


def _header(w: _Writer) -> str:
    card = w.card
    parts = [f"Тикет {card.ticket_id} на {w.moment(w.now)}"]
    if card.assignee_seq is not None:
        parts.append(f"ответственный: {w.who(card.assignee)} [событие №{card.assignee_seq}]")
    if card.status_seq is not None:
        parts.append(f"статус: {card.status} [событие №{card.status_seq}]")
    parts.append(f"учтено событий: {card.last_seq}")
    return " · ".join(parts)


def _render(
    header: str, sections: list[Section], dropped: list[Dropped], budget: int | None
) -> str:
    blocks = [header]
    for section in sections:
        blocks.append(
            "\n".join([f"{section.title}:", *(f"- {line.text}" for line in section.lines)])
        )
    if not sections and not dropped:
        blocks.append("По тикету пока ничего не зафиксировано.")
    if dropped:
        names = ", ".join(f"{d.title.lower()} ({d.items})" for d in dropped)
        blocks.append(f"Не поместилось в лимит {budget} токенов: {names}.")
    return "\n\n".join(blocks)


def _span(delta: timedelta) -> str:
    """A duration in the two largest units; abbreviations sidestep Russian plurals."""
    minutes = max(int(delta.total_seconds() // 60), 0)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days} дн {hours} ч" if hours else f"{days} дн"
    if hours:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    return f"{minutes} мин"
