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

## Status

Implemented: the domain core and the extractor, with tests. The extractor is
tested against recorded responses.

Next: PostgreSQL event store and alert outbox, helpdesk adapter, handoff brief
under a token budget, and the benchmark of simulated tickets with planted
events for comparison against full-context, rolling-summary and Mem0 baselines.

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
