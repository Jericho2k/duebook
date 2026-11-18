# A promise is a duplicate only when it restates an open one

Status **Accepted by the owner** in chat on 2026-11-18, as relayed by the
Round 8 task brief.

## Context

The reducer refused a new commitment as a duplicate when an open commitment
of the same owner had the same action after case and space folding. It did
not look at the words the promise was made in, at its deadline or at what it
was about. "Восстановим доступ к рабочему пространству Альфа до пятницы" and
"…Бета до понедельника" both came out as "восстановить доступ": the second
was refused, never reached the card and never alerted.

The two errors are not alike. A restatement kept by mistake is one more
visible promise and at worst one extra alert, which a person sees and
dismisses. A real promise refused as a duplicate is silent and permanent.

## Options

1. Keep the same-action rule. Short actions keep dropping promises about
   different objects.
2. Never deduplicate. Every restatement becomes a second promise.
3. Deduplicate only a restatement: compare what the two wordings name and
   their deadlines, and keep both when in doubt.

We take 3.

## Rule

A new commitment is a duplicate of an open one only when all hold:

- same owner, and the open one is not the commitment it supersedes;
- its wording (quote and action) names nothing the open one's wording does
  not, and the open one's names nothing it does not. This is the comparison
  `closing.names_other` makes for a renegotiation, applied both ways
  (`closing.restates`). Wordings that name nothing at all are not joined;
- the deadlines are equal, or one of them is absent.

Otherwise the commitment is admitted as a separate one.

## Consequences

- Promises with the same action about different orders, workspaces or
  refunds are kept apart. Promise identity now depends on the wording
  naming what is affected, so the extraction instructions ask for that.
- A restatement in different words, or with a different deadline and no
  `supersedes`, is kept as a second promise. If the first was renegotiated,
  it stays open and may raise an alert a person has to dismiss.
- Replaying every saved run's proposals (Rounds 2 and 4 to 7, `test2`,
  `test3` and the dev splits) found one real promise the old rule dropped,
  and none the new rule drops (`results/round8/identity-replay.json`).
- Events recorded under the earlier rule keep it on replay: the store
  records rule version 4 for new events (migration 0008); versions 1 to 3
  rebuild with the same-action rule.
