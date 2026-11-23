# Look back for the proof before a deadline alert

Status **proposed**, 2026-11-23.

## Context

The model reads a ticket one message at a time. It often fails to connect
an implicit fulfilment to the promise it fulfils: a bare meeting link, an
attached invoice, "deployed". The promise stays open, and the clock raises
a false due-soon warning and a false breach.

On the two Mendeley tickets this was the main cause: 10 of 21 errors had
no close proposed at all, and 4 more had the close aimed at another
promise. On `test6-implicit` none of the 40 implicit fulfilments ended
closed or claimed (most of those cases are covered by decision 0007).

The closing guards are not the cause: they refused a correct close once on
Mendeley. They must stay: false closes were 0 on `test3`, `test5`,
`test6-implicit` and the Mendeley run.

Microsoft Research treats "is this task already done" as a separate problem
from "is this a commitment" (White, Hassan Awadallah, Sim, "Task Completion
Detection", SIGIR 2019). Their shipped reminders asked the user to confirm.

## Options

1. Teach the per-message reader to notice fulfilment better (a prompt
   change). It has to remember every open promise at every message, which
   is what it fails at now.
2. Before alerting, ask one focused question about one promise: here is the
   promise, here is everything said since; was it done, and which words
   show it?
3. Soften every alert on a promise whose owing side posted afterwards (no
   model). Cheap, but it also softens real breaches.

We take 2, in two steps.

## Rule

- When an alertable promise comes within an hour of its due-soon time, the
  scheduler appends a deadline-check event for it. The event carries the
  promise and the public messages since it (ids and text), so that it can
  be replayed without the store. A second check is appended 15 minutes
  before the deadline if the promise is still open. At most two per
  promise.
- The processor asks the reader through an `extract/client.py` preset: did
  any of these messages do what was promised? It answers with a close
  proposal citing the message and a verbatim quote from it, or with
  nothing.
- The reducer applies the usual checks of a close: the quote is in the
  cited message, the target span identifies it, the verifier's verdict, the
  relevance check, and no rival fitting as well.
- **Step 1 (this decision):** a check that passes every test does not
  close the promise. It is recorded as a verified suggestion. While one is
  pending, `lifecycle` raises «вероятно выполнено (сообщение №N) —
  подтвердите» instead of «скоро срок» or «просрочено». A check that fails
  any test leaves an ordinary suggestion, and the normal alert fires.
- **Step 2 (a later decision):** a verified check closes or claims the
  promise as the cited message would have (decision 0006). Only after step
  1 measures zero wrong verified suggestions.

## Consequences

- A false «просрочено» becomes a «подтвердите» with the proof attached. The
  card does not change in step 1, so no promise can be closed by mistake.
- Risk: a real breach softened by wrong evidence (the twin's delivery). The
  guards catch most of it. Measured on the broken twins in the implicit
  sets: real breaches softened must be 0.
- Cost: one or two extra calls per promise with a deadline, about 10% more
  per ticket. Promises kept off the conversation (a phone call) still alert:
  there is nothing to find.
- New: an event kind, a notice kind and a closing-rule version. Saved runs
  replay unchanged. The method frozen at Round 10 changes; the report
  presents this as Round 11, measured on a fresh held-out set.
