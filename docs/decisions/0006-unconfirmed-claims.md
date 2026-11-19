# A claim that is never confirmed must not go silent

Status **Accepted by the owner** in chat on 2026-11-19, as relayed by the
T14 task brief.

## Context

When the side that owes a promise says it is done, the reducer records a
*claim*: the promise stays on the card as claimed until the other side
confirms it. A claimed promise raised no deadline alert, so a false claim
silenced the promise, and only a protest from the other side brought it
back.

In Round 10's `test5` (M0006, seq 54) the writer turned "По накладной вам
отправлен VC-858" into "Накладная уже у вас — по ней отправлен VC-858". The
model read it as the company's claim that "Пришлём вам накладную до конца
недели" was done. The promise was still owed, went silent at three
checkpoints, and its breach alert never fired.

## Options

1. Keep a claim silent. A false claim is a silent loss, the worst failure.
2. Treat a claim as still open and alert on its breach. A true claim, which
   the customer often never confirms, would read as a broken promise.
3. Keep the claim, and raise one soft notice when the other side has not
   confirmed it by the deadline: a person checks it.

We take 3.

## Rule

- A claimed promise that the other side has not confirmed by its deadline
  raises one `claim_unconfirmed` notice: «отмечено как выполненное, но
  клиент не подтвердил — проверьте».
- `lifecycle` derives it from the card and the clock, keyed by the promise
  and the notice's time, and the outbox delivers it like every alert. Its
  time is the deadline, or the claim if that came later.
- The deadline must be one `lifecycle` alerts on: trusted, inside
  `alerts_since`, and owed by the company unless the policy includes the
  customer. A promise with no deadline gets no such notice.
- It does not reopen the promise or count as a breach: the promise stays
  claimed. A confirmation by the other side, or a cancellation, ends the
  claim, and with it the notice; the delivery worker retires one that is
  still queued.
- The brief lists such a claim among the urgent promises, in those words,
  with the message that claimed it.

## A confirmation aimed off the card

The same rule version keeps a second kind of evidence. In `test5` M0015
seq 143, "Бонусы вижу, спасибо!" confirmed "Начислим вам компенсацию
бонусами", but the model aimed the close at the pending offer "начислим вам
бонусы взамен". The reducer refused it as not a commitment and kept nothing,
so no person was asked.

- When a close is refused because its target is not an open commitment (an
  offer, an unknown handle, a settled promise), the reducer tries its words
  against every open commitment of the other side, with the checks of a
  close except the target's own span and the verifier's verdict, which are
  about the wrong target.
- If exactly one commitment passes, the usual suggestion is recorded on it
  («возможно, выполнено по сообщению №N — подтвердите»). It never closes it.
  If none or several pass, nothing is recorded.

## Consequences

- A false claim is no longer silent: past the deadline a person is asked to
  look. A true claim the customer never confirms costs one notice.
- A misaimed confirmation can now leave a suggestion; a person dismisses a
  wrong one, as with any suggestion.
- Events recorded under earlier rules keep a silent claim on replay. A
  claim records the rule it was made under (`Commitment.claim_checked`), and
  the store records rule version 6 for new events (migration 0010).
