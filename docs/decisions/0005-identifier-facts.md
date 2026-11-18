# An identifier slot holds a set of values

Status **Accepted by the owner** in chat on 2026-11-18, as relayed by the
Round 9 task brief.

## Context

The card held one current value per slot. When the agent named a second
order ("Оформим возврат по заказу 538916") while the customer's order was on
the card, the two values met in `order.number` and the reducer opened a
dispute between the two sides. On Round 8's `test4` this made 20 of the 46
false disputes, and it hid 6 order numbers from the current facts. It is not
only a benchmark artifact: a ticket can concern several orders, accounts,
contracts or invoices, and each of them is a fact at once.

## Options

1. Keep one value per slot. Every second object reads as a disagreement.
2. Ask the model to file each object in its own slot (`order.number_2`).
   Slot names would drift, and a real correction would no longer meet the
   value it corrects.
3. Let identifier slots hold a set, and dispute only an explicit correction.

We take 3.

## Rule

- The identifier slots are listed in the slot vocabulary
  (`slots.IDENTIFIERS`): `order.number`, `account.number`,
  `contract.number`, `invoice.number`, `tracking.number`.
- A new value in an identifier slot, from either side, is added to the
  current values. It neither supersedes nor disputes them.
- A value is corrected only when the message negates it: "не 538916, а
  538961" (a negation, then at most two words, then the value). A correction
  of the other side's value is a dispute; of one's own, a change. A system of
  record or an explicit change replaces the values it corrects.
- The other side repeating a disputed value settles that dispute only.
- The brief and the benchmark hide a disputed value, not its whole slot, from
  the current facts.

## Consequences

- A second order no longer opens a dispute, and the customer's order stays
  among the current facts.
- A correction worded without a negation ("правильный номер 538961") is
  added as one more value, not disputed. The card shows both numbers, so the
  error is visible, but no dispute is raised.
- Events recorded under the earlier rule keep it on replay: the store
  records rule version 5 for new events (migration 0009); versions 1 to 4
  rebuild with one value per slot.
