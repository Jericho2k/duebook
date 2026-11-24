# Promises are reconciled, not only read in passing

Status **proposed**, 2026-11-24.

## Context

duebook has two jobs: find promises, and track what happens to each one.
The first works, on real data too. The second is done in passing: at each
message the reader must see how it relates to every open promise, and word
rules (`closing.overlap`, `restates`, `names_other`) decide. Each way of
talking those rules did not foresee became a new decision (0001, 0003–0006).

The Round 11 baseline (`test6-implicit`, 20 tickets) shows the limit. No
false close and no missed breach, but none of the 40 promises kept
implicitly ended closed, and 245 alerts were false:

| Cause | Alerts |
|---|---:|
| A restatement became a second promise | 162 |
| The close was refused because the promise's own copies fit as well | 66 |
| The close was aimed at a look-alike | 9 |
| No close was proposed | 8 |

On the two Mendeley tickets, "no close proposed" was the largest cause (10
of 21 errors). Finding a commitment and detecting that it was kept are
separate problems (White, Hassan Awadallah, Sim, "Task Completion
Detection", SIGIR 2019).

## Options

1. Keep adding word rules, one per failure.
2. Ask the per-message reader to track everything better. It already has to
   hold every open promise at every message, which is what it misses.
3. Add a reconciliation step: when a promise's state matters, ask about that
   one promise with its whole history, and decide through the existing
   checks.

We take 3. This replaces the proposed 0007 (restatements) and 0008
(deadline check).

## Rule

The reader still proposes. Before duebook acts on a promise's state, or
adds a promise that may already exist, it reconciles that promise.

| When | Question | Possible answers |
|---|---|---|
| A new promise shares a naming word with an open one of the same side | Same promise, same promise with a new date, or a new one? | Join it as a repeat; renegotiate (`supersedes`); admit it as new |
| The scheduler is 1 hour before a promise's due-soon time, and 15 minutes before its deadline | Was it done? | Kept, quoting the message; partly kept (it stays open, the progress is noted); not kept |
| A handoff, before the brief | Was each open promise of the company done? | Same as above |

- A check is an event that carries the promise and the messages it was
  shown (ids and text). Its answer is stored as ordinary proposals, so the
  card rebuilds without a model (AGENTS.md rule 1). Calls go through an
  `extract/client.py` preset.
- Every answer passes the checks a close or renegotiation already passes:
  - the quote is verbatim in the cited message;
  - the target span identifies the promise;
  - the verifier did not say no (decision 0001);
  - the words are about this promise;
  - no other open promise fits them as well.
  A join must also drop nothing the promise names.
- A kept promise is closed, or claimed when the proof is the debtor's own
  message (decision 0006), exactly as if the reader had seen it at that
  message.
- An answer that fails a check changes nothing. It leaves the existing
  suggestion («возможно, выполнено по сообщению №N»), and the alert, which
  then fires as before, carries that message.
- A check that has not finished in time never holds an alert back.
- The word rules stay as the safety net, and 0003's exact restatement is
  still joined without a call.

## Coverage

| What happens to a promise | Before | With this rule |
|---|---|---|
| Made | ✓ | ✓ |
| Repeated | ✗ | Reconciled |
| New date | ✓ | ✓, and checked by reconciliation |
| Kept, confirmed by the other side | ✓ | ✓ |
| Kept implicitly (a link, a file, "deployed") | ✗ | Reconciled |
| Kept partly | untested | Stays open, progress noted |
| Cancelled or withdrawn | ✓ | ✓ |
| Owner changes (handoff) | ✓ | ✓, reconciled before the brief |
| Kept outside the conversation | cannot be seen | Alerts, as it must |

## Consequences

- One mechanism instead of a word rule per failure. A new way of talking
  needs a better answer to the same question, not a new rule.
- Cost rises: about 30–50 more calls per ticket, each with more context.
  The ceiling is $0.05 a ticket (today $0.017, full-context methods
  $0.065–0.071). Reasoning effort is chosen on the development set within
  that ceiling.
- Readiness is decided by measurement, not by an operator's confirmation:
  - `test5` must not regress (461/463 promises, 0 false closes, 0 false
    alerts, 0 missed breaches);
  - on a fresh `test7`, generated only after the rule is frozen and covering
    every row of the table above: 0 false closes, 0 promises lost to a join,
    0 missed breaches, and a false alert for at most 1 kept promise in 20;
  - the Mendeley pair is replayed as a case study.
  `test6-implicit` is the development set from now on.
- Closing-rule version 7. Events recorded earlier replay under their own
  version. The method frozen at Round 10 changes; the report presents this
  as Round 11.
