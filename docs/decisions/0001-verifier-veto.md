# Honor negative verification on proposed closes

Status **Accepted by the owner**

Round 4 found four unrelated confirmations that closed still-owed callbacks.
The verifier rejected each close, but the only-open-promise fallback admitted it.
An affirmative verdict also let a vague reply close the wrong commitment.

We considered keeping the verifier advisory, removing it, and honoring its
negative verdicts. The reducer now rejects the targeted change on `no` and
requires independent evidence that identifies the commitment. `yes`, `unsure`,
and an unavailable verifier leave evidence checks in force. A rejected close
keeps the promise open and leaves a suggestion.

New events use the tighter rule. The store records the rule version so existing
cards can still be rebuilt from the log, without changing alert history.

## Amendment: a contextual close must not name another thing

Status **Accepted by the owner** (Round 6; confirmed in chat on 2026-11-18).

In Round 5 a customer confirmed "возмещение стоимости монтажа", asked about in
the preceding turn. The installation refund had never reached the card, and
the close went to its twin, "Возместим стоимость сборки". The context passed
because the twins share every word but one.

Options: leave the rule, which keeps this false close; require the context to
match the target word for word, which blocks a question with a remark beside
it; or refuse when the question names something the target does not. We take
the third. A close that relies on the preceding turn is refused when a content
word of the asking sentences in the quoted question is absent from the
target's wording. Asking verbs ("проверьте") and remarks beside the question
("По другой позиции ещё уточняем") do not count. The promise stays open with a
suggestion saying that the confirmation seems to refer to something not on
the card. Replaying Round 5's records, it removes the one false close and
blocks none of the 21 true contextual closes on `test3` and `dev3`.

Events recorded under the earlier rule keep it on replay: the store records
rule version 3 for new events, and version 2 rebuilds without this check.
