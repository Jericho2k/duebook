# Conditional offers

Status **Accepted by the owner** in chat on 2026-11-18 (option 3), as relayed
by the Round 9 task brief. Proposed in Round 8.

## Context

"Если посылка не придёт до пятницы, вернём деньги" is neither a promise nor
nothing. Nothing is owed yet; if the condition comes true, the company owes a
refund, and nobody is reminded unless the record keeps the offer.

The card had commitments, which `lifecycle` alerts on from their deadline,
and facts, which the brief shows and nothing alerts on. A condition fits
neither: its trigger is an event ("the parcel has not arrived"), not a
moment, and the deadline in the sentence ("до пятницы") belongs to the
condition, not to the refund.

Round 8 filed such offers as an `offer.conditional` fact until this was
decided. On `test4` the model also filed a plain promise there ("Начислим
вам компенсацию бонусами"), which then never alerted, and in three tickets
a later plain promise replaced the offer as the slot's current value.

## Options

1. **Ignore it.** If the condition comes true the offer is lost silently.
2. **Record it as a plain commitment.** A breach alert fires on the
   condition's date although nothing may be owed, and the promise cannot be
   honestly closed.
3. **A conditional item** with its own life: pending, due, activated, lapsed.

We take 3.

## Rule

- The extractor reports an offer under `conditionals`: the whole offer
  (`quote`), its condition copied from it (`condition`), the owner, the
  action, and the condition's deadline in the usual structured form, which
  `temporal` resolves. The reducer admits it as a `Conditional` on the card
  (`MakeConditional`), refused like a promise when its words are not in the
  message, when it binds the other side, or when it restates a pending offer.
- A pending offer owes nothing and raises no alert.
- When the condition's deadline passes with nothing recorded, `lifecycle`
  derives one `condition_due` notice, keyed by the offer and its deadline,
  sent through the outbox to the assignee. No migration: the outbox already
  takes an alert about an item that is not a commitment.
- `condition_outcomes` reports that the condition held or failed
  (`SettleConditional`). Both must name the offer twice, by handle and by a
  span of its wording, and their words must fit it and no other pending offer
  as well: the deterministic checks of a close.
- **Activation** (it held) admits an ordinary commitment: its wording is the
  offer's, `activated_by` is the message that said the condition held, and
  its deadline is one the offer gave its action ("вернём деньги в течение 3
  дней"), read by the rule parser outside the condition and counted from
  activation. From then on it is a promise like any other.
- **Lapse** (it failed) ends the offer, and so must meet stricter evidence,
  since a wrong lapse is a silent loss. Besides the checks of a close, it is
  refused when it comes from the side that owes the offer ("the debtor cannot
  release itself", as with a cancellation), and when its words agree with
  the condition in polarity ("так и не пришла" against "если не придёт" says
  the condition holds). A refused activation or lapse stays on the offer as
  a suggestion, and the offer stays pending.
- The brief lists pending offers in a section "Обещано при условии", between
  the open promises and the open contradictions, with their sources; an
  activated commitment cites both messages. The API's card carries the offers
  with their sources, and the brief's JSON carries each line's sources.
- The `offer.conditional` slot and its instruction are removed.

## Consequences

- The answer format gains two lists. Every format change so far (Round 5)
  cost recall elsewhere, so Round 9 checks a control sample of ordinary
  messages before trusting it.
- The verifier is not asked about activations and lapses; the deterministic
  checks above decide them.
- The offer's own deadline for its action comes from the rule parser only,
  so it has medium confidence; the model reads one deadline per offer, the
  condition's, which is the confusion this record warned about.
- Events recorded under earlier rules hold no conditional proposal. The store
  records rule version 5 for new events (migration 0009, with decision 0005).
