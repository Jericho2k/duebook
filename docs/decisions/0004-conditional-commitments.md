# Conditional offers

Status **Proposed** (Round 8). The owner decides.

## Context

"Если посылка не придёт до пятницы, вернём деньги" is neither a promise nor
nothing. Nothing is owed yet; if the condition comes true, the company owes a
refund, and nobody is reminded unless the record keeps the offer. The
extraction instructions did not cover it, so the model's reading was
unpredictable: sometimes a plain commitment, sometimes nothing.

The card has commitments, which `lifecycle` alerts on from their deadline,
and facts, which the brief shows and nothing alerts on. A condition fits
neither: its trigger is an event ("the parcel has not arrived"), not a
moment, and the deadline in the sentence ("до пятницы") belongs to the
condition, not to the refund.

## Options

1. **Ignore it.** Nothing is stored. Simplest, but if the condition comes
   true the offer is lost silently: the worst failure by the owner's order of
   priorities.
2. **Record it as a plain commitment.** The card shows "вернуть деньги" with
   the condition's date as its deadline. On Friday a breach alert fires
   although nothing may be owed (the parcel arrived), and a close needs a
   confirmation of a refund that should never happen. False alerts and a
   promise that cannot be honestly closed.
3. **A new conditional item.** A `ConditionalCommitment` with the offer's
   wording, the condition (its own quote and, when it has one, a deadline
   resolved by `temporal`) and the action it would trigger. Its life:
   - *pending* while the condition is undecided; no alert;
   - when the condition's deadline passes with nothing recorded, `lifecycle`
     raises one `condition_due` notice (keyed by the item, as `Notice.key`
     does today) asking the assignee to check whether the condition holds;
   - a message that says the condition holds ("посылка так и не пришла")
     *activates* it: the reducer admits an ordinary commitment, sourced from
     both messages, with its own deadline if the offer gave one; from then on
     it is a promise like any other;
   - a message that says the condition failed ("посылка пришла") *lapses* it,
     under the same evidence rules as a close (`closing`), since a wrong lapse
     is a silent loss.

## Consequences of option 3

- **Card model:** a new item type and its codec form; a proposal kind
  (`MakeConditional`) and two decisions (`ActivateConditional`,
  `LapseConditional`); a migration only if the outbox needs a new kind of row
  (it does not: it keys on the item id as for an unread message).
- **`lifecycle`:** one more notice kind, derived from the condition's
  deadline; activation hands over to the existing commitment statuses, so
  breach logic is unchanged.
- **Extraction:** one more list in the answer format and instructions on
  telling a condition from a deadline. This is the biggest risk: every change
  of the answer format so far (Round 5) cost recall elsewhere, so it needs a
  dev run before any test.
- **Benchmark:** Round 8's `main3` profile plants conditional offers; gold
  would gain the activation or lapse message.
- **Brief:** a section "Обещано при условии" between open promises and
  contradictions.

## Until the owner decides

The extraction instructions report a conditional offer as a fact in the slot
`offer.conditional`, with the whole offer as its value, and never as a
commitment. It is not alerted on, but the card and the handoff brief show it
with its source, so the next agent sees it. That is option 1's cost without
its silence. Its limits: a second offer in the same ticket supersedes the
first as the current value (the first stays in the fact history), and no
alert fires when the condition comes true.

## Recommendation

Option 3, built after a development run shows the extractor can tell a
condition's date from a promise's deadline without losing recall elsewhere.
Option 2 should not be used: it trades a silent loss for false alerts and
promises that cannot be closed honestly.
