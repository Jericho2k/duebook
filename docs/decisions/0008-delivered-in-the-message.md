# A promise delivered in the message itself is kept

Status **Accepted by the owner** in chat on 2026-11-24.

## Context

When the side that owes a promise says it is done, the reducer records a
claim, and decision 0006 asks a person to check any claim the other side has
not confirmed by the deadline. That protects against a false "done", such as
`test5` M0006: "Накладная уже у вас" was read as the invoice being sent when
it was not.

But often the debtor's message is not a report of the work. It *is* the
work: "Ссылка на встречу: https://…", "Счёт во вложении: invoice-52294.pdf",
the tracking number that was promised. Customers rarely answer "получил". In
the Round 11 development run (`test6-implicit`, decision 0007), 40 of the 45
remaining false notices were 0006 claim reviews on promises kept this way.
Without them, 5 of 134 kept promises raised a false notice.

## Options

1. Keep 0006 as it is. Every delivered link or file costs a person a check.
2. Treat any debtor "done" as kept. M0006 shows how that loses a promise.
3. Close only when the message itself contains what was promised; keep
   every report of work done elsewhere a claim.

We take 3.

## Rule

- A close proposed on the debtor's own message may say `delivered`: the
  message contains the promised thing itself. It must quote that thing (the
  link, the file name, the number or the information asked for).
- The reducer closes the promise, not claims it, only when:
  - the quote is verbatim in the message;
  - every check of a close passes (decision 0001's verifier veto included);
  - the verifier, asked "does this message itself contain what was
    promised?", says yes.
  Otherwise it is a claim, as before.
- A report of work done elsewhere ("установили", "отправили посылку",
  "деньги вернули", "накладная уже у вас") is never `delivered`. It stays a
  claim under 0006.
- The same applies to a close found by reconciliation (decision 0007).

## Consequences

- A delivered link, file or answer no longer raises a check notice.
- Risk: a close on a wrong delivery, such as the test-environment link
  closing the production promise. The target and ambiguity checks and the
  verifier guard it. Measured as false closes, which must stay 0, and the
  M0006 case must stay a claim.
- Closing-rule version 8. Events recorded earlier replay under their own
  version.
