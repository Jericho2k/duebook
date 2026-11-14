# duebook

Structured event memory for long-running customer support tickets.

A ticket that runs for weeks and passes between operators loses three things:
its current state, the promises made on it, and the knowledge of what has
already been tried. A summary does not fix this — a summary retells, it does
not keep account. duebook keeps account: it maintains a **ticket card** that is
always current and treats every promise as an entity with an owner, a deadline
and a lifecycle.

## The method in one line

    card' = apply(card, event, proposals)

A language model reads each new ticket event and **proposes** changes. A
deterministic layer **decides** which are admitted. The card is the fold of the
event stream, so it can be rebuilt, audited, and inspected as of any moment.

## Rules the deterministic layer enforces

| Rule | Why |
|---|---|
| Every proposal must quote a literal span of the message it comes from | A model's assumption must never be stored as something a person said |
| Nobody can bind the other side | "You promised me a refund" is a customer's claim, not a company promise |
| The debtor's "done" is a *claim*; only the other side or a system of record *verifies* | "We sent the refund" and "the refund arrived" are different facts |
| The debtor cannot cancel their own promise, only renegotiate it | A dropped promise must stay visible |
| One side updating its own statement *supersedes*; two sides disagreeing is a *contradiction* | A change is history, a disagreement is an open problem |
| A promise follows the ticket on handoff and is marked *inherited*; with no assignee it is *orphaned* | Handoff is where promises are lost |
| The model never computes a date | It emits a structured expression; code resolves it against the message time, timezone and working calendar |
| The model does not get to name a fact's slot freely | One thing must have one slot, or an update looks like a second fact and a disagreement goes unnoticed |

## One name for one thing

In live runs the model filed a refund under `order.refund` in one message and
`order.refund_status` in the next. Every rule about facts compares slots, so
the update was stored as a second, unrelated fact. `duebook.slots` normalises
the proposed name before the reducer compares anything: spelling is folded
(`orderStatus`, `Order-Status`), known aliases lead to a canonical slot from a
controlled vocabulary (`order.refund_status` -> `refund.status`), and a name
outside the vocabulary joins the slot the card already has for the same thing
(`warranty_info` -> `warranty`). The vocabulary is also written into the
model's instructions. It is open on purpose: an unforeseen slot is kept, it
just cannot split in two. Pass your own `Vocabulary` for another domain.

## Deadlines with a confidence level

The same phrase is read twice, independently: by the model and by a rule
parser for Russian and English ("до пятницы", "в течение 3 рабочих дней",
"by Friday 5 pm"). Agreement gives a high-confidence deadline. Disagreement
gives a low-confidence one, which is shown on the card and never alerted on —
a false alarm teaches people to ignore the next one.

A deadline resolves to the latest plausible moment: "by Friday" is the end of
Friday.

## Extraction and what it costs

The extractor shows the model the card and the one message that just arrived —
never the whole transcript — and works against any OpenAI-compatible endpoint:

```python
from duebook import apply
from duebook.extract import ChatClient, Extractor

extractor = Extractor(ChatClient(api_key=key))  # OpenRouter by default
extraction = extractor.extract(card, event)
card = apply(card, event, extraction.proposals).card
extraction.usage.usd()  # what this message cost
```

Every call returns its token counts and, where the provider reports it, the
amount actually charged. `duebook.extract.estimate` projects the cost of a
ticket in advance and compares it with re-reading the whole transcript on every
message: incremental cost grows linearly with ticket length, re-reading grows
quadratically.

A failed extraction raises. It never returns "nothing found", so a message the
model could not read is retried rather than silently skipped.

## Storage: an event log, a snapshot, an outbox

`duebook.store` puts the pure core on PostgreSQL (psycopg 3, plain SQL
migrations in `src/duebook/store/migrations`, no ORM).

```python
from duebook.store import Store, Worker, connect, migrate

conn = connect("postgresql://...")
migrate(conn)
store = Store(conn)
store.record(event, proposals, now=now)  # one transaction, see below
store.card(ticket_id)  # the current snapshot
store.replay(ticket_id, upto=12)  # the card as of event 12, from the log alone
```

One transaction per event writes the event, the proposals made for it, the
new card snapshot and any alert that became due. The log is append-only — a
trigger refuses UPDATE and DELETE — and unique on `(ticket_id, seq)`, so a
redelivered event is recognised and a *different* event at an occupied
position is reported as a conflict instead of silently dropped.

The proposals are stored whole, in order, together with which of them the
reducer refused and why. Replaying them through the same reducer reproduces
the card exactly, without calling the model again.

### Alerts: nothing lost, nothing repeated

An alert is identified by what it is about (`Notice.key`), and that key is the
outbox's primary key: deriving the same alert twice queues it once. A worker
claims notices with `FOR UPDATE SKIP LOCKED`, checks that each is still true
of the current card, sends it, and records the delivery. A claim is a lease;
one left behind by a dead worker is taken over when it goes stale.

Sending cannot be made atomic with recording, so a worker that dies between
the two leaves a notice that will be attempted again under the same key. The
channel must treat the key as an idempotency key — that is the single
requirement placed on the outside world.

`tests/test_delivery_crash.py` checks the result two ways. Deterministically:
a worker is killed before and after every send in a batch. And bluntly: real
worker processes are killed with SIGKILL at random moments while two of them
drain 300 notices. A typical run:

    300 notices, 20 SIGKILLs, 36 notices re-claimed after a dead worker,
    7 repeated attempts absorbed by the key, 0 lost, 0 duplicated

The scheduler does not poll tickets. Each ticket row carries
`lifecycle.next_wakeup` — the next instant at which its alerts change — and
the scheduler sleeps until the earliest one.

## The pipeline: one event, all the way through

```python
from duebook.pipeline import Pipeline

pipeline = Pipeline(store, extractor)
handled = pipeline.handle(event)  # ingest -> extract -> apply -> persist -> queue alerts
pipeline.retry_held()  # tickets whose failed extraction is due for another try
```

The event is stored first, so it cannot be lost and the helpdesk can be
acknowledged whatever happens next. Events are then applied strictly in order
under a per-ticket lock: one that arrives ahead of its predecessor waits in
the log and is applied in the same pass that closes the gap; a redelivered one
is recognised and not read again.

When extraction fails the ticket is **held** at that event. No empty result
is recorded for it and nothing after it is applied; the hold is stored with
the error, the cost of the failed calls and a retry time that backs off. Other
tickets are not affected.

## The handoff brief

```python
from duebook.brief import compose

brief = compose(card, now, budget=600, names={"agent-olga": "Ольга"})
print(brief.text)
brief.dropped  # what did not fit, by section, with counts
```

A deterministic rendering of the card, in Russian, for the operator taking a
ticket over. No model writes it. It is ordered by what hurts most if missed:
promises broken or about to be, promises that changed hands or have nobody
answerable, the other unresolved promises, open contradictions, current
facts, what changed, steps that failed, open questions.

    Тикет T-1 на вт 12.01 12:00 · ответственный: Ольга [событие №7] · учтено событий: 7

    Просрочено и на подходе:
    - ПРОСРОЧЕНО на 12 ч (срок был до конца пн 11.01); клиент: прислать скриншот [№4 Анна: «Пришлю скриншот сегодня»]
    - осталось 3 ч (срок до вт 12.01 15:00); агент: перезвонить; отвечает Ольга; раньше отвечал: Иван [№5 Иван: «завтра до 15:00 перезвоню»]

    Открытые противоречия:
    - order.status: клиент: «не пришёл» [№2 Анна: «он не пришёл»] — компания: «вручён» [№3 Иван: «заказ вручён»]

    Что изменилось:
    - account.plan: было «Базовый» [№2 Анна: «Тариф Базовый»], стало «Про» [№5 Иван: «на тариф Про»]

Every line ends with the message it rests on: its number in the ticket, the
author and the quoted words. Under a token budget the brief loses whole
sections from the bottom of that order and ends by naming them — "Не
поместилось в лимит 330 токенов: что известно сейчас (3), открытые вопросы
(1)" — so a short brief is never mistaken for a complete one. Tokens are
estimated pessimistically for Russian; pass a real tokenizer's counter as
`count` when the exact figure matters.

## Status

Implemented, with tests: the domain core, the extractor, and the PostgreSQL
event store with its alert outbox, scheduler and delivery worker, the
per-ticket pipeline that ties them together, and the handoff brief. The
extractor is unit-tested against recorded responses; `scripts/smoke.py` runs a
short ticket through a real model and prints the proposals, the refusals, the
final card and the charged cost.

Next: a helpdesk adapter and the benchmark of simulated tickets with planted events
for comparison against full-context, rolling-summary and Mem0 baselines.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest -q
```

The store tests need a PostgreSQL database they may wipe, and are skipped
without one:

```bash
TEST_DATABASE_URL=postgresql://user:password@localhost:5432/duebook_test .venv/bin/pytest -q
```

```bash
.venv/bin/ruff check .
```
