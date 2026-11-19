# Round 10 model comparison

Duebook ran the same 12 fresh tickets with DeepSeek V4.1 Flash on DeepInfra
and GLM 5.3 Flash on Relace. Both runs completed all 2,091 messages and
produced no false closes across 54 checkpoints each. GLM got 261 of 267
labeled promises right at ticket end and raised five false breach alerts.
DeepSeek got all 267 right and raised zero false breach alerts.

## Measured solutions

The two `dev-portability` rows are paired on identical tickets. The four
baselines ran on `test3` with DeepSeek V4.1 Flash in Round 4. Their scores
are frozen alongside the Round 10 DeepSeek result on that split. Compare
methods within their split. The table also gives the original Round 10
`test5` result for reference. [Round 10 results](results.md#round-10-the-final-method) ·
[Frozen baseline scores](../results/round10/test3-compare.json)

| Solution | Split | Tickets | Promises right at end | Commitment F1 | False closes / ticket | False breach alerts / ticket | Freshness error | Dispute recall | USD / ticket, handoffs only | USD / ticket, live |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Duebook R10, DeepSeek V4.1 Flash | `dev-portability` | 12 | 267/267 | 1.000 | 0.00 | 0.00 | 0.000 | 1.000 | $0.0154 | $0.0154 m |
| Duebook R10, GLM 5.3 Flash | `dev-portability` | 12 | 261/267 | 0.996 | 0.00 | 0.42 | 0.000 | 1.000 | $0.0202 | $0.0202 m |
| Duebook R10, DeepSeek V4.1 Flash | `test5` | 20 | 461/463 | 0.999 | 0.15 | 0.00 | 0.000 | 1.000 | $0.0170 | $0.0170 m |
| Duebook R10, DeepSeek V4.1 Flash | `test3` | 20 | 367/367 | 0.995 | 0.00 | 0.00 | 0.000 | 1.000 | $0.0154 | $0.0154 m |
| Full context | `test3` | 20 | 348/367 | 0.997 | 0.30 | 0.45 | 0.291 | 0.560 | $0.0043 | $0.0651 m+p |
| Rolling summary | `test3` | 4 | 43/71 | 0.797 | 0.50 | 0.75 | 0.333 | 0.667 | $0.0956 | $0.210 p |
| Mem0 2.2.1 | `test3` | 20 | 341/367 | 0.990 | 0.65 | 0.40 | 0.310 | 0.604 | $0.0530 | $0.304 p |
| Graphiti 0.31.2 (Zep's engine) | `test3` | 10 | 147/180 | 0.942 | 1.90 | 0.40 | 0.278 | 0.192 | $0.1602 | $0.325 p |

False closes, freshness error, and dispute recall use all checkpoints.
Commitment F1 and false breach alerts use ticket end. Full context reads the
transcript at each checkpoint, and rolling summary updates a bounded summary
after each event. Graphiti and rolling summary ran on the first 10 and 4
tickets respectively. One Graphiti ticket stopped early, and one rolling-summary
ticket completed. "Handoffs only" is the cost of answering at each handoff
and the end; "live" is the cost of an answer current after every message, as
Duebook's card is (m: measured; p: projected; m+p: full context measured on
three `test3` tickets, $0.0714 a ticket, and projected on the other 17).
Duebook reads every message in both regimes
([Round 10c](results.md#round-10c-a-fair-comparison--live-versus-checkpoint-only)). The broader survey compares additional memory systems by
storage and handling of time and contradictions. [Round 4 run details](results.md#round-4-final-comparison-on-test3) ·
[Competitor survey](competitors.md#summary)

## What changed with the model

- Both models got every matched deadline right, caught all 92 true breach
  alerts at ticket end, and handled all 12 identifier corrections. DeepSeek
  got 12 of 12 context-dependent confirmations right. GLM got 10 of 12.
- Of GLM's six promise errors, the scorer attributed one to a proposal
  dropped by the reducer and five to later handling. The verifier returned
  one invalid verdict, which Duebook recorded as `unsure`. Both runs finished
  with zero unread messages.
- With GLM's stored proposals, the unguarded fold produced nine false closes
  at ticket end. The normal fold produced zero. The replay checker rebuilt
  all 108 saved checkpoints from the two models at $0.
- The paired GLM-minus-DeepSeek difference in F1 was −0.0037
  (95% interval −0.0092 to 0.0000). Status accuracy among matched promises
  was 100% for DeepSeek and 98.1% for GLM (difference −1.88 percentage
  points, 95% interval −3.14 to −0.74). Intervals resample paired tickets.

## Run and replay

The run used commit `75bb219` and closing rule version 5. The fixed
`dev-portability` split uses `main5`, seed 9091, and the generator's original
synthetic wording. Both evaluated models only read the tickets during the
comparison. The archived Round 10 splits used GLM 5.3 Flash for text rewrites.

Both models ran at temperature 0 on locked OpenRouter routes with fallbacks
disabled. DeepSeek used the usual DeepInfra fp8 preset, with reasoning off
for initial extraction and low for escalation and verification. Relace
requires reasoning for GLM and rejects strict JSON schema requests. The
benchmark client used low reasoning and appended the existing response
schema to the prompt as text. The rest of the extractor, verifier, reducer,
and scorer ran at the pinned revision. The records list 2,091 extraction
responses and 139 verifier responses from DeepInfra, and 2,091 extraction
responses and 132 verifier responses from Relace.

Relace rate limiting interrupted the first attempt. The paced rerun reused
successful cached replies. Total benchmark spend was $0.4264, including
the interrupted attempt. The pair tests portability across the two
model/provider configurations. The different reasoning and schema settings
also contribute to observed quality and cost, and the synthetic wording
limits generalization to real support tickets. [Spend log](../results/round10/model-comparison/spend.jsonl)

Run `python scripts/replay_check.py` from the repository root to check both
readers' saved snapshots and the paired scores with model calls disabled.
[DeepSeek record](../results/round10/model-comparison/deepseek.json) ·
[GLM record](../results/round10/model-comparison/glm.json) ·
[Paired scores](../results/round10/model-comparison/comparison.json)
