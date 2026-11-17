"""Live smoke test: a short ticket through a real model and the reducer.

    OPENROUTER_API_KEY=... python scripts/smoke.py [model] [--reasoning=low]

Prints what the model proposed, what the reducer refused, the final card and
what it cost. Not part of the test suite: it spends money and needs a network.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from duebook import (
    Actor,
    AssigneeChanged,
    Card,
    MessagePosted,
    Role,
    WorkCalendar,
    apply,
    notices,
    status_at,
)
from duebook.extract import DEFAULT_MODEL, PRICES, ChatClient, Extractor, Usage

MSK = ZoneInfo("Europe/Moscow")
ANNA = Actor("cust-1", Role.CUSTOMER, "Анна")
IVAN = Actor("agent-ivan", Role.AGENT, "Иван")
OLGA = Actor("agent-olga", Role.AGENT, "Ольга")
START = datetime(2027, 1, 11, 10, 0, tzinfo=MSK)  # a Monday

SCRIPT = [
    (0, ANNA, "Здравствуйте! Заказ 4815 оплатила ещё 5 января, а он до сих пор не пришёл. "
              "С карты списали 3200 рублей."),
    (20, IVAN, "Анна, добрый день! Вижу заказ 4815, он застрял на складе. Подскажите, "
               "какой адрес доставки вы указывали?"),
    (35, ANNA, "Москва, Лесная 12. И вообще, я уже хочу вернуть деньги."),
    (50, IVAN, "Понял вас. Оформил возврат, вернём деньги до пятницы. Завтра до 15:00 "
               "перезвоню вам и подтвержу, что заявка ушла в банк."),
    (60, ANNA, "Хорошо, жду звонка. Пришлю вам скриншот списания сегодня вечером."),
    ("assign", IVAN.id, OLGA.id),
    (60 * 30, ANNA, "Мне так никто и не перезвонил! Вы обещали вернуть деньги, где они?"),
    (60 * 31, OLGA, "Анна, приношу извинения. Заявка на возврат ушла в банк, но не успеваем "
                    "к пятнице — деньги вернём в следующий вторник."),
    (60 * 24 * 9, ANNA, "Деньги пришли, спасибо."),
]  # fmt: skip


def main() -> None:
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    model = names[0] if names else DEFAULT_MODEL
    cal = WorkCalendar(tz=MSK)
    effort = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--reasoning=")), None)
    key = os.environ.get("OPENROUTER_API_KEY", "proxy-injected")
    client = ChatClient(api_key=key, model=model, reasoning=effort)
    extractor = Extractor(client, cal)
    card, spent, at = Card("T-1"), Usage(), START

    for seq, step in enumerate(SCRIPT, 1):
        if step[0] == "assign":
            event = AssigneeChanged(
                id=f"e{seq}", ticket_id="T-1", seq=seq, at=at, previous=step[1], current=step[2]
            )
            print(f"\n[{seq}] handoff {step[1]} -> {step[2]}")
        else:
            at = START + timedelta(minutes=step[0])
            event = MessagePosted(
                id=f"e{seq}", ticket_id="T-1", seq=seq, at=at, author=step[1], text=step[2]
            )
            print(f"\n[{seq}] {at:%a %d.%m %H:%M} {step[1].name}: {step[2]}")
        extraction = extractor.extract(card, event)
        outcome = apply(card, event, extraction.proposals, cal)
        card, spent = outcome.card, spent + extraction.usage
        refused = {id(r.proposal): r.reason.value for r in outcome.rejections}
        for p in extraction.proposals:
            mark = f"REFUSED ({refused[id(p)]})" if id(p) in refused else "ok"
            print(f"    {mark}: {p}")
        for problem in extraction.retried:
            print(f"    RETRIED: {problem[:160]}")
        u = extraction.usage
        if u.calls:
            print(f"    [{u.calls} call(s), {u.prompt_tokens}+{u.completion_tokens} tokens]")
        for m in extraction.malformed:
            print(f"    MALFORMED {m.section}: {m.error}")

    print("\n=== CARD ===")
    for f in card.facts:
        state = "current" if f.current else f"superseded by {f.superseded_by}"
        print(f"fact  {f.slot} = {f.value!r} [{f.source.side.value}, msg {f.source.seq}] {state}")
    for c in card.commitments:
        due = "-"
        if c.due:
            due = (
                f"{c.due.deadline.due_at.astimezone(MSK):%a %d.%m %H:%M} ({c.due.confidence.name})"
            )
        print(
            f"commit {c.id} [{c.owner.value}] {c.action!r} due {due} -> {status_at(c, at).value}"
            f" | responsible {c.responsible} inherited_from {c.inherited_from}"
            f" | quote {c.source.quote!r}"
        )
    for s in card.steps:
        print(f"step  {s.action!r} [{s.outcome.value}]")
    for q in card.questions:
        print(f"quest {q.text!r} waiting on {q.waiting_on.value} open={q.open}")
    for c in card.open_contradictions():
        print(f"CONTRADICTION on {c.slot}")
    print("notices at end:", [n.key for n in notices(card, at)])

    print("\n=== COST ===")
    print(
        f"model {model}: {spent.calls} calls, {spent.prompt_tokens} prompt tokens "
        f"({spent.cached_tokens} cached), {spent.completion_tokens} completion tokens"
    )
    print(f"charged: ${spent.usd(PRICES.get(model)):.6f}")


if __name__ == "__main__":
    main()
