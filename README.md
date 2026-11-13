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

## Deadlines with a confidence level

The same phrase is read twice, independently: by the model and by a rule
parser for Russian and English ("до пятницы", "в течение 3 рабочих дней",
"by Friday 5 pm"). Agreement gives a high-confidence deadline. Disagreement
gives a low-confidence one, which is shown on the card and never alerted on —
a false alarm teaches people to ignore the next one.

A deadline resolves to the latest plausible moment: "by Friday" is the end of
Friday.

## Status is a function of the clock

Only what events established is stored: open, claimed, fulfilled, cancelled,
superseded. "Due soon" and "breached" are computed from the current time, so no
background job can leave them stale. An alert is identified by what it is about
— commitment, kind, deadline — so delivery can be made exactly-once with a
unique key.

## Example

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from duebook import Actor, Card, MessagePosted, Party, Role, apply, notices, status_at
from duebook.proposals import MakeCommitment
from duebook.temporal import WeekdayRef

msk = ZoneInfo("Europe/Moscow")
ivan = Actor("agent-ivan", Role.AGENT)

event = MessagePosted(
    id="e1",
    ticket_id="T-1",
    seq=1,
    at=datetime(2027, 1, 11, 10, 0, tzinfo=msk),
    author=ivan,
    text="Анна, мы вернём деньги до пятницы.",
)
proposal = MakeCommitment(
    owner=Party.COMPANY,
    action="вернуть деньги",
    quote="вернём деньги до пятницы",
    due=WeekdayRef(4),
    due_quote="до пятницы",
)

card = apply(Card("T-1"), event, [proposal]).card
commitment = card.commitments[0]

saturday = datetime(2027, 1, 16, 9, 0, tzinfo=msk)
status_at(commitment, saturday)  # Status.BREACHED
notices(card, saturday)  # one BREACHED notice with a stable key
```

## Layout

| Module | Contents |
|---|---|
| `duebook.events` | The input stream: messages, assignee and status changes |
| `duebook.card` | The card: facts with validity intervals, commitments, steps, questions |
| `duebook.proposals` | What an extractor may propose |
| `duebook.reducer` | `apply` and `replay`: admission rules, supersession, contradictions, handoff |
| `duebook.temporal` | Deadline expressions, working calendar, the rule parser |
| `duebook.lifecycle` | Status and alerts as functions of the clock |

The core has no dependency on a database, a network or a model.

## Status

Implemented: the domain core above, with tests.

Next: LLM extractor with a JSON schema, PostgreSQL event store and alert
outbox, helpdesk adapter, handoff brief under a token budget, and the benchmark
of simulated tickets with planted events for comparison against full-context,
rolling-summary and Mem0 baselines.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest -q
```

```bash
.venv/bin/ruff check .
```
