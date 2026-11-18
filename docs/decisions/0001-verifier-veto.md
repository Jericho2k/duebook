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
