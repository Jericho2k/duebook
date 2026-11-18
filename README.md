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
| A close (done, cancelled, renegotiated) must name its promise twice and be about it; if not, the promise stays open and the close becomes a suggestion | A promise closed by mistake is never alerted on again; one left open still is |
| One side updating its own statement *supersedes*; two sides disagreeing is a *contradiction* | A change is history, a disagreement is an open problem |
| A promise follows the ticket on handoff and is marked *inherited*; with no assignee it is *orphaned* | Handoff is where promises are lost |
| The model never computes a date | It emits a structured expression; code resolves it against the message time, timezone and working calendar |
| The model does not get to name a fact's slot freely | One thing must have one slot, or an update looks like a second fact and a disagreement goes unnoticed |

## Closing fails safe

The extractor can attach "done" to the wrong promise: two refunds are open,
the customer writes "деньги пришли", and the model picks one. Checking WHO may
close a promise does not catch that; round 1 of the evaluation lost 5 of 32
broken promises this way. So a proposal that closes or replaces a commitment
carries two independent references — the handle (C2) and `target_quote`, a
span copied from that commitment's original wording, which the card shows the
model — and `duebook.closing` decides, deterministically, whether to admit it:

* the span must be in the target's original wording;
* the closing words must identify the target. For a brief confirmation, a
  verified quote from the preceding public turn can supply the subject, if
  the question names nothing the target does not;
* no other open promise of that side may fit the closing words as well;
* a renegotiation may add or drop detail, but not name a different thing.

Bare thanks passes none of this: "спасибо" closes nothing. A refused close is
kept on the commitment as a `Suggestion`; the promise stays open, its alerts
still fire, and the brief says "возможно, выполнено по сообщению №N —
подтвердите". `apply(..., guards=UNGUARDED)` switches the checks off, for
measuring what they do.

### A second opinion on destructive changes

`extract.VerifyingReader` asks about proposed completions, cancellations and
replacements. For a short confirmation that relies on the preceding side's
public turn, it compares the open promises of the owing side together. It can
identify a single target with a quote from each turn. The reducer checks both
quotes against their source messages, checks for a rival target, and stores
both sources on the fulfilled commitment. An ambiguous reply may leave a
suggestion for an agent to clarify.

`duebook process` enables the verifier by default. It uses the same endpoint
and API key as the extractor, with DeepSeek V4.1 Flash and low reasoning.

A verifier `no` rejects that proposed change. A `yes` still requires
evidence of the subject and the action, even with one open promise. An unsure
verdict or a failed verification leaves the deterministic checks in force.
The stored verdict and the version of the closing rule let the log replay
without calling a model. [Closing rule decision](docs/decisions/0001-verifier-veto.md).
`Guards(verifier=False)` ignores verdicts and contextual closes for measurement.

```python
from duebook.extract import ChatClient, Extractor, Verifier, VerifyingReader

reader = VerifyingReader(
    Extractor(ChatClient(api_key=key)),                   # reasoning off
    Verifier(ChatClient(api_key=key, reasoning="low")),   # thinks, only on closes
)
```

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

The extractor works against an OpenAI-compatible endpoint. It shows the card,
the event text, and the preceding public turn from the other side. Its
structured response lists only kinds of changes with entries. An empty read
is `{"updates":[]}`.

The processor reads each message individually by default. With
`DUEBOOK_EXTRACTION_BATCHING=true` it reads up to four already-waiting,
consecutive text events of one ticket together. It collects one response per
event and applies events in sequence. A response that depends on an earlier
change can request an individual reread against the updated card. A batch
request has a 10 s deadline; the processor reads individually near a
scheduled alert or after a failed batch.
The default reader is `deepseek/deepseek-v4.1-flash` with reasoning switched
off (left at "low" it spends 1,000-2,300 hidden tokens a message) and locked
to one OpenRouter host (DeepInfra, fp8, no fallbacks), so the fixed
instructions hit one prompt cache and one host answers every call. What a
model needs sent lives in a small presets table, `extract.client.PRESETS`
(hosts and reasoning per model); `ChatClient(reasoning="low")` asks the same
model to think. Every call records the host that answered, the prompt tokens
served from cache and the hidden reasoning tokens. A 429 or 5xx is retried
once, an empty or unreadable answer once, a timeout never — it may already
have been generated and billed. A `ResponseCache` keeps answers to
temperature-0 requests on disk, so re-running unchanged input costs nothing.

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
Verifier timeouts, HTTP errors and unreadable answers are logged and stored as
`unsure` verdicts. The event is applied using the deterministic checks.
Each applied event records verifier usage separately from extraction, including
the provider when reported, token counts and the reported charge.

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

### Going live on old tickets

Importing a ticket's history would queue a breach alert for every promise
broken long ago. `AlertPolicy(alerts_since=go_live)` withholds alerts about
deadlines before that moment: they are never derived and never scheduled. The
promise is still breached on the card and in the brief — only the alert is
suppressed.

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

`handle` does both halves in the caller's time. The service splits them: the
webhook only stores (`Store.ingest`), and processors apply what was stored:

```python
pipeline.process()  # one pass over tickets with new events or a retry due
pipeline.advance(ticket_id)  # move one ticket as far as its stored events allow
```

Each ticket row counts the events ingested for it, and a processor writes back
the count it saw before advancing, so the work list is a partial-index read
and an event that lands mid-pass keeps its ticket on the list. A ticket
waiting on a gap or backing off after a failure drops off the list until
something changes. Several processors can run at once: they *try* the
per-ticket lock and pass over a ticket another one holds.

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

## The benchmark

```bash
python -m duebook.bench --seed 1 --tickets 200        # self-check, no model called
python -m duebook.bench --split dev3                 # self-check a fixed split
python -m duebook.bench.experiment --split dev2 --methods event_memory --max-usd 0.2
python -m duebook.bench.experiment --split dev2 --methods event_memory --max-usd 0.02 \
    --replay results/round2/dev2-4.json   # recorded proposals, only the verifier is asked
python -m duebook.bench.report results/round2/test2.json --dev results/round2/dev2-*.json
python -m duebook.bench.review --split test2  # regenerate the human spot-check
```

`duebook.bench` builds simulated tickets the other way round: the events are
planted first and the conversation is written around them, so the labels are
true by construction. A ticket has 50-64 messages (200-230 with the `long`
profile), at least two handoffs between operators, and runs over several
days. Most of it is filler — status pings, thanks, "any news?" — as in a real
long ticket. Planted: promises kept, broken, vague, still pending at the end,
and the customer's own; a promise made early with a deadline days away; a
plan changed by the company and an address changed by the customer; disputes
over an amount (half later settled) and a product model; a fix that failed,
suggested again by the operator who took the ticket over. Generation is
seeded, and `SPLITS` fixes the seeds: `dev` (6 tickets) for tuning, `test`
(20 tickets) run once for the reported numbers, `long` (2 tickets of 200+
messages) for cost scaling — round 1. Round 2's main test is the `main`
profile: 150-200 messages, 3-4 handoffs, about two weeks, and, besides all of
the above, pairs of near-identical promises open at once (one kept, one
broken), a renegotiation and a cancellation — what makes a close easy to pin
on the wrong promise. Its splits are `dev2` (4 tickets) and `test2` (20).

The `main2` profile uses the same length and handoff ranges and adds three
cases per ticket. Use `dev3` (4 tickets, seed 4444) for tuning and `test3`
(20 tickets, seed 4343) for evaluation.

| Measure | What the method must show at the end of the ticket |
|---|---|
| `rescue_close_correct` | The twin identified by the preceding turn is fulfilled and its rival is still open. |
| `ambiguous_close_refrained` | Both twins are still open when the reply leaves their identity unclear. |
| `wrong_thing_not_closed` | The callback is still open after the customer confirms an unrelated refund. |

Each measure is the share of its cases the method gets right. Omitting an
affected promise counts as a failure. When the method supplies a closing
message number for the rescue, it must point to the customer's confirmation.
The oracle carries a planted affirmative verdict for that confirmation.

Every method answers in one format at every handoff and at the end
(`bench.result`): promises with the owing side, source message, due instant
and status; the facts it presents as current; failed steps; open disputes.
One scorer (`bench.score`) judges them all: commitment precision/recall/F1,
deadline accuracy, status accuracy, breach-alert precision/recall and false
alerts per ticket, freshness error (the share of presented facts that are
stale or contested), fact changes, disputes and failed steps, and closes:
false closes (closed while still owed), wrong-target closes (closed by the
wrong message), missed closes and the event memory's suggestions. Facts are
matched by planted value, not slot name, because methods name things
differently.

Each ticket carries the proposals a perfect extractor would make; pushed
through the real reducer they score exactly 1.0 on every measure:

    tickets: 200
    at the end of each ticket:
      commitments: precision 1.000  recall 1.000  F1 1.000  (tp 913, fp 0, fn 0)
      deadline accuracy: 1.000 (913/913)
      status accuracy: 1.000 (913/913)
      breach alerts: precision 1.000  recall 1.000  (tp 304, fp 0, fn 0)
    at every handoff and the end (697 checkpoints):
      freshness error: 0.000 (stale 0, contested 0 of 1772 facts judged)
      fact changes right: 1.000 (686/686)
      disputes: precision 1.000  recall 1.000
      failed steps: precision 1.000  recall 1.000

Tickets are rewritten by a different model than the one that reads them
(round 2: GLM Flash, reading with DeepSeek); a rewrite that loses a planted
span, turns into something other than one message, or adds a number, a
question, a negation or a promise-like verb, is discarded and the template
text kept.

The [test2 spot-check packet](docs/review/test2-spotcheck.md) samples four
archived rewrites. Its answer key follows all four conversations. The review
command uses archived text when available and generated text otherwise.

The experiment runner keeps every run inside a session budget: answers are
cached on disk, every run logs its charge to `spend.jsonl`, and a run first
projects its cost from a sample and refuses to start if it would not fit. A
method too expensive for every ticket runs on a disclosed subset. A round is
a configuration of the one runner: the event memory runs as the product ships
it, with the verifier, and the same proposals are folded again with the
verdicts ignored and with the guards off. `--replay` folds a recorded run's
proposals instead of calling the extractor, and `--frozen` scores a record's
other methods again, so frozen baselines are never run twice. Finished
tickets are journalled, so an interrupted run resumes without paying twice.

The baselines (`bench.baselines`) read with the same model as the event
memory: (a) the whole transcript at each checkpoint, (b) a summary updated
after every event, (c) Mem0 (`pip install -e ".[mem0]"`), (d) Graphiti
(`pip install -e ".[graphiti]"`). The comparison, its numbers with 95%
bootstrap intervals, and its threats to validity are in
[docs/results.md](docs/results.md) (rounds 1 to 4); the raw record is under
`results/` and `results/round2/` to `results/round4/`.

## Running the service

The service is five commands of one console script, configured only by
environment variables, so one image runs every role:

| Command | What it does | How many |
|---|---|---|
| `duebook migrate` | brings the schema up to date, then exits | once per deploy |
| `duebook serve` | the HTTP API (uvicorn) | any |
| `duebook process` | applies stored events: extract, reduce, persist | any |
| `duebook schedule` | queues alerts as deadlines come due | one |
| `duebook deliver` | sends queued alerts through the configured channel | any |

`process`, `schedule` and `deliver` accept `--once` for a single pass. On
SIGTERM or SIGINT each loop finishes the item in hand — the ticket being
advanced, the alert being sent — and exits 0; a delivery worker hands the
rest of its claimed batch straight back to the queue. A loop that loses the
database logs it and reconnects.

### With Docker

```bash
cp .env.example .env   # set the model key, secrets, alert channel
docker compose up -d --build
docker compose up -d --scale processor=3   # processors share the work safely
```

`docker-compose.yml` runs PostgreSQL 16, the migrations, the API on port
8000, a processor, a scheduler and a delivery worker.

### Without Docker

```bash
pip install -e ".[service]"
export DUEBOOK_DATABASE_URL=postgresql://duebook:duebook@localhost:5432/duebook
export DUEBOOK_OPENROUTER_API_KEY=...
duebook migrate
duebook serve &
duebook process &
duebook schedule &
duebook deliver &
```

The core library does not need the web stack: `pip install duebook[store]`
is enough for the event store, `[service]` adds FastAPI, uvicorn and the
connection pool.

### Endpoints

| Method and path | Returns |
|---|---|
| `POST /events` | 202 `{"ticket_id", "seq", "new"}` once the event is stored; 409 when a different event holds that position; 422 when the body is not a valid event |
| `GET /tickets/{id}/card` | the card in the codec's JSON, and `commitment_status`: each commitment's status now |
| `GET /tickets/{id}/card?as_of=<seq>` | the card replayed from the log up to that event, with statuses as of that event's time |
| `GET /tickets/{id}/brief?budget=<tokens>&format=text\|json` | the handoff brief; as JSON, its sections and lines, each line with its sources (event id, seq, author, side, quote) |
| `GET /tickets/{id}/status` | last applied seq, backlog, hold details if any, and verifier call and failure counts for the ticket |
| `GET /health` | database status, pending alerts, held tickets, and service-wide verifier call and failure counts |

`verifier_calls` counts proposals sent to the verifier, including calls that
fail. `verifier_failures` counts calls that end in an error. Both counts
cover applied events and count a retried call once.

`POST /events` takes one event in the format `duebook.store.codec` stores:

```json
{"type": "MessagePosted", "id": "msg-8812", "ticket_id": "T-1", "seq": 3,
 "at": "2027-01-11T10:15:00+03:00",
 "author": {"type": "Actor", "id": "agent-ivan", "role": "agent", "name": "Иван"},
 "text": "Перезвоню вам через 2 часа.", "public": true}
```

`AssigneeChanged` (`previous`, `current`) and `StatusChanged` (`previous`,
`current`) are the other kinds. `seq` is the ticket-local order and the
idempotency key; events may arrive in any order and more than once. The
endpoint only stores — it never waits for the model — so a helpdesk is
answered at once.

When `DUEBOOK_WEBHOOK_SECRET` is set the raw body must be signed:
`X-Duebook-Signature: sha256=<hex HMAC-SHA256 of the body>`. When
`DUEBOOK_API_KEY` is set the read endpoints need `Authorization: Bearer
<key>`; `/health` is open.

Alerts go either to stdout as JSON lines (`log`) or to a URL (`webhook`): a
POST of the notice as JSON with `Idempotency-Key: <notice key>`, signed with
the same scheme when `DUEBOOK_ALERT_SECRET` is set. Any answer but 2xx is
retried with backoff, and the receiver must treat the key as an idempotency
key.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `DUEBOOK_DATABASE_URL` | — | PostgreSQL connection string (required) |
| `DUEBOOK_OPENROUTER_API_KEY` | — | model key; required by `process` |
| `DUEBOOK_OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | any OpenAI-compatible endpoint |
| `DUEBOOK_OPENROUTER_MODEL` | `deepseek/deepseek-v4.1-flash` | the model that reads messages |
| `DUEBOOK_VERIFIER_ENABLED` | `true` | set to `false` to skip model verification |
| `DUEBOOK_VERIFIER_MODEL` | `deepseek/deepseek-v4.1-flash` | the model that verifies proposed closes |
| `DUEBOOK_VERIFIER_REASONING` | `low` | verifier reasoning, `low` or `off` |
| `DUEBOOK_WEBHOOK_SECRET` | empty: unsigned | HMAC secret for `POST /events` |
| `DUEBOOK_API_KEY` | empty: open | bearer key for the read endpoints |
| `DUEBOOK_ALERT_CHANNEL` | `log` | `log` or `webhook` |
| `DUEBOOK_ALERT_URL` | — | where `webhook` alerts are POSTed |
| `DUEBOOK_ALERT_SECRET` | empty: unsigned | HMAC secret for outgoing alerts |
| `DUEBOOK_TIMEZONE` | `Europe/Moscow` | the working calendar's zone; keep it fixed, cards are replayed under it |
| `DUEBOOK_ALERTS_SINCE` | none | ISO 8601 time with offset; no alerts about earlier deadlines |
| `DUEBOOK_WORKER_NAME` | role@host:pid | this process's name in outbox claims |
| `DUEBOOK_PROCESS_IDLE_SECONDS` | 1 | processor sleep when there is no work |
| `DUEBOOK_PROCESS_BATCH` | 20 | tickets per processor pass |
| `DUEBOOK_EXTRACTION_BATCHING` | `false` | `true` reads queued messages of a ticket together |
| `DUEBOOK_SCHEDULE_MAX_IDLE_SECONDS` | 30 | longest scheduler sleep |
| `DUEBOOK_DELIVER_IDLE_SECONDS` | 5 | delivery worker sleep when the queue is empty |
| `DUEBOOK_DELIVER_BATCH` | 10 | alerts claimed per pass |
| `DUEBOOK_HOST`, `DUEBOOK_PORT` | `0.0.0.0`, 8000 | where `serve` listens |
| `DUEBOOK_POOL_MIN`, `DUEBOOK_POOL_MAX` | 1, 10 | the API's connection pool |

## Status

Implemented, with tests: the domain core, the extractor, and the PostgreSQL
event store with its alert outbox, scheduler and delivery worker, the
per-ticket pipeline that ties them together, the handoff brief, slot
normalisation, the close guards (`duebook.closing`, with an adversarial
suite in `tests/test_guards.py`) and the verifier of destructive changes
(`extract.verifier`), the benchmark with its three baselines and
the experiment runner, and the service around them: the HTTP API, the processor, scheduler
and delivery loops, log and webhook alert channels, and an end-to-end test
from webhook to delivered alert. `scripts/smoke.py` runs a short ticket
through a real model and prints the proposals, the refusals, the final card
and the charged cost.

Measured against a real model, in three rounds (see docs/results.md): round 1
with GLM 5.3 Flash on 50-64 message tickets; round 2 with DeepSeek V4.1 Flash
on 150-200 message tickets, where the event memory made no false close on the
20 test tickets; round 3, on dev only, compared a reasoning extractor and the
verifier with round 2's configuration and kept round 2's: no configuration
made a false close, the verifier agreed with the guards on all 119 closes it
was asked about but changed no outcome, and reasoning cost 2.3 times as much
and read three deadlines wrong. Round 4, the final comparison on a fresh
seed (`test3`, 20 tickets with three hard close cases each), ran once: the
event memory read every deadline right, showed no stale fact and every open
dispute, where every baseline fell short on each, at $0.013 a ticket. It lost every
rescue case (the reply that settles a promise names nothing, and the
extractor sees one message), and in 4 of 20 tickets closed a callback on an
unrelated "the money arrived": the verifier said no, and the guards let it
through. The test tickets and the baselines' answers are frozen.

Each service command has been run against a local PostgreSQL with uvicorn and
a stand-in model endpoint; the Dockerfile and `docker-compose.yml` have been
checked with `docker compose config` but not yet built or started.

Round 5 adds the negative verdict veto, contextual confirmation, compact
replies, and batching for queued messages. The evaluation is in
[docs/results.md](docs/results.md). A helpdesk adapter and evaluation on
independently labeled real tickets are next.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest -q
```

The store, API and service tests need a PostgreSQL database they may wipe,
and are skipped without one:

```bash
TEST_DATABASE_URL=postgresql://user:password@localhost:5432/duebook_test .venv/bin/pytest -q
```

```bash
.venv/bin/ruff check .
```
