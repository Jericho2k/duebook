# A restated promise joins the promise it repeats

Status **proposed**, 2026-11-23.

## Context

Support agents repeat promises: "Напомню: ссылка будет в чате до 22
января", "Срок прежний", "will get back to you asap" said four times. Under
decision 0003 a repeat is joined to the open promise only when its wording
names exactly the same things. A real repeat almost always adds a word
("чат", "напомню", "срок"), so it becomes a second promise.

In `test6-implicit` (Round 11 baseline) 81 of 103 restatements became new
promises. They caused 162 of the 245 false alerts directly. They also
caused most of the other 66: a close of the original was refused as
`ambiguous_target` because its own copies fit the closing words equally
well (SIM-8484-I0001: one delivery, four rows, every close refused). On the
two Mendeley tickets duplicates caused 6 of 21 errors.

Decision 0003 chose this side on purpose: joining a new promise to an old
one by mistake loses it silently. That reason stands. What it did not have
was a way to tell a repeat from a new promise other than the words alone.

## Options

1. Loosen the word comparison (allow added words). "Пришлю счёт и акт"
   would join "пришлю счёт", and the act would be lost. Rejected.
2. Let the model link a repeat to the promise it repeats, and admit the
   link only through the checks a renegotiation already passes, including
   the verifier's veto (decision 0001). Anything that fails stays a
   separate promise, as today.
3. Leave it. Every repeat stays a row that alerts.

We take 2.

## Rule

- A `MakeCommitment` may carry `restates`: the id of an open commitment it
  repeats with nothing new promised. The extraction instructions say so,
  and say that a different deadline is a renegotiation (`supersedes`).
- The reducer joins it, creating no new row, only when all hold:
  - same owner; the target is open;
  - the deadline is the target's, or none is stated;
  - the checks of a renegotiation pass: the target span identifies the
    target, the verifier did not say no, the wording does not name
    something other than the target, and no other open commitment fits it
    as well;
  - the wording drops nothing the target names.
- A joined repeat is recorded on the commitment as a further source, shown
  on the card with its message («повторено в сообщении №N»). Alerts follow
  the original's deadline.
- If any check fails, the promise is admitted as a separate one, and the
  target gets a suggestion «возможно, повтор обещания из сообщения №N».
- 0003's exact-restatement rule stays as it is.

## Consequences

- Fewer rows, and fewer refused closes, because a close no longer competes
  with copies of its own target.
- A wrong join could hide a new promise. Guarded by the verifier, the
  "drops nothing" check and the fallback. Measured as "gold promises lost
  to a join", which must be 0 on `test5`, `test3` and the new sets.
- Old proposals have no `restates`, so saved runs replay unchanged. The
  store records closing-rule version 7 for new events.
- Measuring needs live reading: `test6-implicit` for development, then a
  fresh `test7-implicit` (new seed, some bare deliveries such as a link with
  no words) generated only after the rule is frozen. Also `test5` for
  regressions and the Mendeley pair. Estimated about $1 in total.
