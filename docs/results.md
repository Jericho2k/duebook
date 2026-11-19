# Results: event memory against the baselines

Ten rounds, run from 2026-11-17 to 19. Round 10 runs the final method on a
new, unseen split (`test5`) and on `test3` beside the frozen baselines.
Rounds 5 to 7 reuse the frozen Round 4 comparison. Round 4 contains the
original run on a fresh test split. Round 8 runs our method on a new, unseen
split (`test4`). Round 9 is not a run: it replays Round 8's record and reads
297 chosen messages again. The earlier rounds follow in reverse order.

## Round 10: the final method

Two full runs of the final method, as `main` ships it plus this round's
prompt fix: once on `test5`, a new split nobody had read, and once on
`test3`, beside the frozen baselines. No baseline was run.

- **On `test5` the final method lost no gold promise to the model or the
  reducer.** 461 of 463 were right at the end. It made no false alert or
  false dispute, recorded all 20 conditional offers and each one's end state
  (5 activated, 8 lapsed, 7 pending), raised the 12 condition-due alerts and
  no other, and handled all 20 corrections as decision 0005 says. Its one
  false close comes from a claim that the benchmark's writer added to a
  message (below).
- **On `test3` it is right on every gold promise (367 of 367).** It makes no
  false close at any checkpoint, no false alert and no false dispute, and it
  gets all 60 hard close cases. Every baseline makes more false closes and
  false alerts and misses more disputes. Round 7 was already level on most
  measures. Full context is level on commitment F1 and costs a quarter as
  much.
- **Cost:** $0.0170 a ticket on `test5` and $0.0154 on `test3`, about $0.09
  per 1,000 messages. The session spent **$0.7281 of the $0.75 cap**.

### What this version is

- **Reader:** `deepseek/deepseek-v4.1-flash` with reasoning off, temperature
  0, locked to DeepInfra fp8 (all 7,001 extraction calls of the two runs were
  served there). It reads one message at a time against the card, plus the
  other side's preceding public turn. An answer is capped at 800 tokens; an
  unreadable answer is read once more with reasoning "low".
- **Verifier:** the same model with reasoning "low", asked about every
  proposal that would close, cancel or replace an open promise.
- **The deterministic layer, closing rule version 5:**
  - [0001](decisions/0001-verifier-veto.md): a verifier "no" vetoes a close,
    and a close resting on the preceding turn may not name another thing.
  - [0002](decisions/0002-unreadable-message.md): a message still unreadable
    on the third attempt is recorded as unread and alerted; it does not stall
    the ticket.
  - [0003](decisions/0003-promise-identity.md): a promise is a duplicate only
    when it restates an open one.
  - [0004](decisions/0004-conditional-commitments.md): a conditional offer
    is its own item: it can be pending, due, activated or lapsed.
  - [0005](decisions/0005-identifier-facts.md): identifier slots hold a set,
    and only "не X, а Y" corrects a value.
  - The model never computes a date, and slot names are normalised against a
    controlled vocabulary.
- **This round's fix** (prompt, one rule): "value" holds only what its slot
  is about, and a value is never repeated inside another fact.

### Part A: the open bug, and corrections in the benchmark

**The bug.** On `test4`, the model filed "Мне пришёл блендер модели BL-949"
under `delivery.status` beside `device.model: BL-949` (M0005 seq 21, M0009
seq 128). When the company contested the model, the code stayed current in
the delivery fact: 7 checkpoint presentations, freshness error 0.027.
`delivery.status` is itself a canonical slot, so no alias in `slots.py` can
map it. The fix is one instruction (above). It was checked live against
Round 9's records: the 18 device-model statements in `test4` and a control
sample of 100 other messages (80 where Round 9 or the label had a proposal,
20 where neither did, none read in Round 9; `random.Random(10)`).
[Selection](../results/round10/reread-test4.json) ·
[record](../results/round10/test4-reread.json)

- `test4` freshness error is now **0.000** (from 0.027). All 464 promises
  are still right. There is still no false close, false alert or false
  dispute, and the offers and hard close cases stay at 1.000.
- **No promise or fact the label holds was lost** in the 118 messages. 16
  answers changed:
  - Lost: the 2 offending delivery facts; 4 `refund.status` facts that
    restated a promise ("Возместим стоимость упаковки…"); and one
    `delivery.status: приехал`, which no label holds.
  - The rest are 5 question spans worded differently, a shorter
    cancellation quote, a reworded action, one duplicate close proposal
    dropped, and one filler line no longer read as a question.
  - With 0 regressions in the 98 messages where the label or Round 9 had a
    proposal, the rate could still be up to about 3% (95%, rule of three).
- `replay_check` rebuilds this record at $0 from Round 8's proposals plus
  the reads of Rounds 9 and 10, a later read over an earlier one.

**`main5`.** It is `main4` (an offer whose condition is later said to hold,
said to fail, or never answered) plus a correction beat for decision 0005.
One side gives an invoice number ("Оплачивала по счёту 954559" or "Выставили
вам счёт № 954559"). Later the same side or the other side writes "номер
счёта не 954559, а 954595", which swaps two digits. A correction by the same
side must replace the value. A correction by the other side must open a
dispute.
- The scorer reports each correction's outcome at the end (replaced,
  disputed, both current, new value missing) and each offer's end state.
- Every earlier split is byte-identical, and the labels of `dev5`, `test4`
  and `dev6` are now pinned too.
- The oracle scores 1.000 on the new splits. `dev7` (4 tickets, seed 7221)
  and `test5` (20, seed 7373) are the first seeds from 7171 and 7373 whose
  labels hold each condition outcome and each correction (either side, either
  side's value) at least once in `dev7` and three times in `test5`. Seed 7373
  already did.
- `test5` is rewritten like `test3` and `test4`. `dev7` stays in template
  words, to save the rewrite's cost.
- **`dev7` check:** first the planted proposals, at $0: every measure
  1.000. Then its 4 offers, 3 outcomes, 4 invoice numbers and 4 corrections
  were read live ($0.0024). All 15 were read right: 2 activations, 1 lapse,
  2 corrections replaced, 2 disputed, no false dispute. No full `dev7` run.
  [Record](../results/round10/dev7.json)

### `test5`: fresh and unseen

20 `main5` tickets, 3,520 messages, 87 checkpoints, 463 gold promises. They
were generated, rewritten once by GLM 5.3 Flash (746 rewrites discarded) and
run once. Nobody read them before the run. Intervals are 95% bootstrap over
tickets. [Record](../results/round10/test5.json)

| Measure | `test5` |
|---|---:|
| Gold promises right at the end | **461 of 463** |
| (a) Missed by the model / (b) dropped by the reducer / (c) mishandled later | **0 / 0 / 2** |
| False closes per ticket, end / across checkpoints | 0.05 [0.00, 0.15] / 0.15 [0.00, 0.45] (one promise) |
| False alerts per ticket | **0.00** |
| Breach-alert precision / recall | 1.000 / 0.994 (159 of 160) |
| False disputes per ticket / dispute recall | **0.00** / 1.000 (106 of 106 judged) |
| Failed-step precision / recall | 1.000 / 1.000 |
| Fact recall / freshness error | 1.000 (300 of 300) / **0.000** |
| Fact changes right | 139 of 139 |
| Context-dependent / ambiguous / unrelated close | 20/20, 20/20, 20/20 |
| Commitment precision / recall / F1 | 0.998 / 1.000 / 0.999 |
| Deadline accuracy / status accuracy | 1.000 (463 of 463) / 0.996 |
| Suggestions per ticket (all on promises still owed) | 1.90 |
| Unreadable messages | 0 |
| Cost per ticket / per 1,000 messages | $0.0170 [0.0164, 0.0177] / $0.097 |

Every beat at the end of the ticket:

| Beat | Cases | Result |
|---|---:|---|
| Delegated, with a date / without | 17 / 23 | all right |
| Two promises with one action, different objects | 40 | all right |
| Prediction / status report | 20 / 20 | no promise made |
| Conditional offer | 20 | 20 recorded, none as a promise |
| ↳ condition held: activated | 5 | 5 right |
| ↳ condition failed: lapsed | 8 | 8 right |
| ↳ never answered: still pending | 7 | 7 right |
| Offer state, every checkpoint | 63 | 63 right |
| Condition-due alerts (right, false, missed) | 12 | 12, 0, 0 |
| Correction of one's own number | 12 | 12 replaced, no dispute |
| Correction of the other side's number | 8 | 8 disputed |

**What went wrong on `test5`:**

- **The false close (M0006, seq 54).** The company promised "Пришлём вам
  накладную до конца недели". The writer turned the planted "По накладной вам
  отправлен VC-858" into "Накладная уже у вас — по ней отправлен VC-858". It
  added a statement that the waybill is already with the customer, and the
  rewrite filter did not catch it.
  - The model read this as the company saying its promise was done. The
    reducer recorded it as the debtor's *claim*: the promise stays on the
    card as claimed, but no breach alert fires.
  - By the text this reading is defensible. By the label it is a false
    close at 3 checkpoints and the one missed breach alert.
  - A claim by the side that owes the promise silences the alert. If it is
    false, only the other side's protest brings the promise back.
- **The missed close (M0015, seq 143).** "Бонусы вижу, спасибо!" confirmed
  "Начислим вам компенсацию бонусами". The model pointed the close at the
  pending offer "начислим вам бонусы взамен", and the reducer refused it as
  not a commitment. The promise stays open, so this fails safe. No suggestion
  was left for a person.
- **The one spurious promise** is also the writer's: it turned "Новой
  информации пока нет" into "…Как только что-то появится, мы сразу вас
  проинформируем", a vague promise that no label holds.
- **Suggestions, 1.90 per ticket.** These are the 38 closes the guards
  refused, every one on a promise still owed. Each stays on its promise as
  "возможно, выполнено", and a person has to dismiss it.

**Cost.** Extraction $0.3118 (3,520 calls, 89.6% of prompt tokens from the
cache, 97.5 output tokens a call), verifier $0.0292 (246 calls). That is 7%
above Round 8's `test4` per ticket ($0.0159). The prompt is longer
(conditional offers, this round's rule) and `main5` plants about 3 more
messages a ticket.

### `test3`: beside the frozen baselines

One live run. The baselines are the frozen Round 4 runs in
`results/round4/test3.json`: full context and Mem0 on all 20 tickets,
Graphiti on the first 10 and the rolling summary on the first 4. Rounds 4–7
are our earlier versions, rescored from their records by today's scorer.
Paired differences are taken over the tickets both methods ran.
[Record](../results/round10/test3.json) ·
[comparison with intervals](../results/round10/test3-compare.json)

**`test3` is not unseen.** It informed Rounds 5 to 7, which were checked on
it four times. Rounds 8–10 were developed on other tickets (`dev5`, `dev6`,
`dev7` and, for the fix, `test4`), but a method tuned on `test3` once
carries that tuning. Read these as follow-up numbers on a known set; `test5`
is the unbiased figure.

| Measure | **Round 10** | Round 4 | Round 5 | Round 6 | Round 7 | Full context | Mem0 | Graphiti (10) | Rolling summary (4) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Gold promises right at the end | **367/367** | 338/367 | 337/367 | 351/367 | 364/367 | 348/367 | 341/367 | 147/180 | 43/71 |
| (a) missed by the model / (b) dropped by the reducer / (c) mishandled later | **0 / 0 / 0** | 2 / 0 / 27 | 14 / 1 / 15 | 12 / 0 / 4 | 2 / 0 / 1 | 0 not on the answer / 19 | 6 not on the answer / 20 | 19 not on the answer / 14 | 24 not on the answer / 4 |
| False closes per ticket, end | **0.00** | 0.20 | 0.05 | 0.00 | 0.00 | 0.10 | 0.15 | 0.80 | 0.25 |
| False closes per ticket, across checkpoints | **0.00** | 0.90 | 0.15 | 0.00 | 0.00 | 0.30 | 0.65 | 1.90 | 0.50 |
| False alerts per ticket | **0.00** | 1.10 | 0.45 | 0.20 | 0.05 | 0.45 | 0.40 | 0.40 | 0.75 |
| False disputes per ticket | **0.00** | 0.30 | 0.40 | 2.55 | 0.00 | 0.00 | 0.10 | 0.10 | 2.00 |
| Dispute recall | **1.000** | 1.000 | 0.879 | 0.978 | 1.000 | 0.560 | 0.604 | 0.192 | 0.667 |
| Failed-step precision | **1.000** | 1.000 | 0.776 | 0.777 | 1.000 | 0.792 | 0.771 | 0.771 | 0.963 |
| Fact recall | **1.000** | 1.000 | 0.972 | 0.984 | 1.000 | 1.000 | 1.000 | 0.956 | 0.815 |
| Freshness error | **0.000** | 0.000 | 0.064 | 0.015 | 0.000 | 0.290 | 0.310 | 0.278 | 0.333 |
| Context-dependent close recovered | **1.000** | 0.000 | 0.850 | 0.900 | 0.950 | 0.850 | 0.900 | 0.600 | 0.500 |
| Ambiguous close refrained | **1.000** | 1.000 | 0.950 | 1.000 | 1.000 | 0.950 | 0.850 | 0.600 | 0.500 |
| Unrelated confirmation handled | **1.000** | 0.800 | 0.950 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Commitment F1 | **0.995** | 0.989 | 0.975 | 0.970 | 0.991 | 0.997 | 0.990 | 0.942 | 0.797 |
| Deadline accuracy | **1.000** | 1.000 | 1.000 | 1.000 | 1.000 | 0.935 | 0.909 | 0.932 | 0.915 |
| Status accuracy | **1.000** | 0.926 | 0.957 | 0.989 | 0.997 | 0.948 | 0.945 | 0.913 | 0.915 |
| Breach-alert precision | **1.000** | 0.850 | 0.932 | 0.969 | 0.992 | 0.934 | 0.941 | 0.933 | 0.864 |
| Breach-alert recall | **1.000** | 0.969 | 0.961 | 0.977 | 1.000 | 0.992 | 0.985 | 0.862 | 0.760 |
| Cost per ticket | **$0.0154** | $0.0134 | $0.0110 | $0.0140 | $0.0140 | $0.0043 | $0.0530 | $0.1602 | $0.0956 |
| Cost per 1,000 messages | **$0.088** | $0.077 | $0.064 | $0.081 | $0.081 | $0.025 | $0.304 | $0.908 | $0.532 |

Round 10's 95% intervals where they are not a point: commitment F1
0.989–0.999, cost per ticket $0.0148–0.0159. Every other Round 10 interval
is a single point. Paired differences, other method minus Round 10:

| Other minus Round 10 | Round 7 | Round 4 | Full context | Mem0 | Graphiti (10) | Rolling summary (4) |
|---|---:|---:|---:|---:|---:|---:|
| False closes across checkpoints | +0.00 [+0.00, +0.00] | +0.90 [+0.20, +1.80] | +0.30 [+0.05, +0.65] | +0.65 [+0.10, +1.35] | +1.90 [+0.80, +3.00] | +0.50 [+0.00, +1.50] |
| False alerts per ticket | +0.05 [+0.00, +0.15] | +1.10 [+1.00, +1.25] | +0.45 [+0.15, +0.80] | +0.40 [+0.15, +0.65] | +0.40 [+0.00, +0.80] | +0.75 [+0.25, +1.00] |
| Dispute recall | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | -0.440 [-0.530, -0.349] | -0.396 [-0.551, -0.262] | -0.808 [-0.960, -0.612] | -0.333 [-0.462, -0.143] |
| Failed-step precision | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | -0.208 [-0.266, -0.144] | -0.229 [-0.288, -0.169] | -0.229 [-0.325, -0.113] | -0.037 [-0.120, +0.000] |
| Freshness error | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | +0.290 [+0.239, +0.334] | +0.310 [+0.236, +0.382] | +0.278 [+0.225, +0.324] | +0.333 [+0.160, +0.444] |
| Status accuracy | -0.003 [-0.008, +0.000] | -0.074 [-0.092, -0.060] | -0.052 [-0.073, -0.031] | -0.055 [-0.081, -0.033] | -0.087 [-0.126, -0.045] | -0.085 [-0.148, -0.062] |
| Deadline accuracy | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | -0.065 [-0.093, -0.042] | -0.091 [-0.122, -0.062] | -0.068 [-0.120, -0.031] | -0.085 [-0.208, +0.000] |
| Breach-alert precision | -0.008 [-0.024, +0.000] | -0.150 [-0.170, -0.133] | -0.066 [-0.111, -0.023] | -0.059 [-0.094, -0.025] | -0.067 [-0.133, +0.000] | -0.136 [-0.273, -0.048] |
| Commitment F1 | -0.004 [-0.008, +0.000] | -0.005 [-0.012, +0.000] | +0.003 [-0.004, +0.009] | -0.004 [-0.013, +0.003] | -0.053 [-0.171, +0.003] | -0.196 [-0.476, -0.045] |
| Cost per ticket | -0.0014 [-0.0015, -0.0011] | -0.0020 [-0.0022, -0.0018] | -0.0110 [-0.0115, -0.0105] | +0.0376 [+0.0354, +0.0397] | +0.1449 [+0.1259, +0.1575] | +0.0803 [+0.0354, +0.1064] |


- Against every baseline, Round 10 has more dispute recall, a lower
  freshness error and higher status and deadline accuracy. Every one of
  these intervals excludes zero, except the rolling summary's deadline
  accuracy on 4 tickets.
- It also has fewer false alerts. That interval excludes zero except
  against Graphiti, whose lower bound is 0.
- On commitment F1, full context is level (+0.003 [−0.004, +0.009]) and
  costs $0.011 less a ticket. Mem0 costs 3.4 times as much, Graphiti 10
  times and the rolling summary 6 times.
- Against Round 7 every difference is within its interval of zero, except
  cost: Round 10 costs $0.0014 more a ticket.
- **Four spurious promises,** all the writer's: "Как только что-то
  появится, мы сразу вас проинформируем / уведомим" added to a filler line.
  These are real promises in the text that no label holds. They are all of
  Round 10's precision loss (0.989).

**Spend.** Of $0.75: $0.0239 for the `test4` check, $0.0024 for `dev7`,
$0.0572 to rewrite `test5`, $0.3394 for `test5` and $0.3051 for `test3`:
**$0.7281**.
- Before `test5`, the runner projected $0.325 expected and $0.577 upper,
  from Round 8's `test4` rate; $0.666 was left.
- Before `test3`, $0.327 was left. The runner's upper bound ($0.467) did not
  fit, so the run was judged on its expected cost, as in Rounds 7 and 8: Round
  7's actual `test3` cost scaled by how much the per-message rate grew from
  Round 8's `test4` to `test5`, $0.295. The ledger was capped at the $0.327
  left.
[Spend log](../results/round10/spend.jsonl)

### Threats to validity

- **Synthetic tickets.** The events are planted and the text is written
  around them. Real tickets are messier, longer-tailed and not built to
  labels.
- **Label noise from the writer.** The writer added a delivery claim and
  five vague promises that no label holds (one false close and five
  spurious promises above). The rewrite filter checks numbers, questions,
  negations and a list of verb stems. It does not catch everything, and it
  does not let a rewrite add a negation, which limits how naturally an
  outcome such as a lapse can be worded.
- **One team wrote the generator and the method.** The beats are planted
  in the forms the rules recognise: a correction is always "не X, а Y", and
  an offer is always "Если …, …". A correction worded without a negation,
  or an offer worded another way, is not planted.
- **One reading model**, DeepSeek V4.1 Flash on one host, and one writer,
  GLM 5.3 Flash. Each split was run once at temperature 0, so run-to-run
  variance is not measured.
- **The baselines ran only on `test3`**, once, in Round 4, with Graphiti on
  10 tickets and the rolling summary on 4. They were not given this round's
  tickets, offers or corrections, and `test3` informed our Rounds 5–7.
- **Seeds were chosen on labels** so that each outcome and correction
  occurs (`test5`'s first candidate already qualified). No model output was
  looked at to choose them.

```bash
python -m duebook.bench.experiment --split test4 --methods event_memory \
    --replay <Round 9's spliced test4 record> --reread results/round10/reread-test4.json \
    --max-usd 0.04 --session-cap 0.75 --results results/round10 --out /tmp/test4.json
python -m duebook.bench.experiment --split dev7 --template --methods event_memory \
    --replay oracle --reread results/round10/reread-dev7.json \
    --projection-from results/round8/test4.json --max-usd 0.01 --session-cap 0.75 \
    --results results/round10 --out /tmp/dev7.json
python -m duebook.bench.experiment --split test5 --methods "" --rewrite-only \
    --max-usd 0.08 --session-cap 0.75 --results results/round10
python -m duebook.bench.experiment --split test5 --methods event_memory \
    --projection-from results/round8/test4.json --max-usd 0.60 --session-cap 0.75 \
    --results results/round10 --out results/round10/test5.json
python -m duebook.bench.experiment --split test3 --methods event_memory \
    --projection-from results/round7/test3.json --expected \
    --frozen results/round4/test3.json --compare-frozen-event-memory \
    --max-usd 0.327 --session-cap 0.75 --results results/round10 --out results/round10/test3.json
python scripts/replay_check.py   # every record, Round 10's included, at $0
```

## Round 9 — conditional offers, several orders, issue-summary noise

**Method: replay plus targeted calls, not a full run.** The live budget was
$0.10, and a full run costs $0.12-0.30 a split. So (1) Round 8's recorded
proposals were folded again under the new rules at $0; (2) 284 chosen
messages of `dev5` and `test4` were read again with the new prompt, each
against the card the reducer rebuilds from the recorded proposals before it,
and spliced into the record; every other message keeps Round 8's model
output (4,519 of their 4,803 messages had no new read); (3) on `dev6`, a new split
in template words, the 13 offer and outcome messages were read against the
card the planted proposals build. **`test4` was seen once before**, in
Round 8, so it is no longer unseen.

On the spliced `test4` our method kept all 464 gold promises, including the
one Round 8 lost silently. It made no false close, false alert or false
dispute, recorded every conditional offer, and raised each condition-due
alert the labels expect and no other. The 200-message control sample shows
no promise or fact lost. Spent: $0.0599.

### What changed

1. **Conditional offers** ([decision 0004](decisions/0004-conditional-commitments.md),
   accepted, option 3). An offer owed only if a condition comes true is a
   `Conditional` on the card: pending and silent; one `condition_due` notice
   through the outbox when its condition's deadline passes with nothing
   recorded; *activated* into an ordinary commitment sourced from the offer
   and the message that said the condition held; or *lapsed* under a close's
   evidence rules, and not by the side that owes it or in words that agree
   with the condition ("так и не пришла"). The brief lists pending offers.
   The answer format gains `conditionals` and `condition_outcomes`; the
   `offer.conditional` stopgap is gone from the prompt and the vocabulary.
2. **Identifier slots hold a set** ([decision 0005](decisions/0005-identifier-facts.md),
   accepted). `order.number`, `account.number`, `contract.number`,
   `invoice.number` and `tracking.number` keep every value named; only a
   negated value ("не 538916, а 538961") is a correction, disputed when it was
   the other side's. Rule version 5 (migration 0009); older events replay as
   they were decided.
3. **Issue-summary noise** (prompt, one sentence): "Never restate a promise,
   an offer or a status report as a fact." Of Round 8's 26 `issue.summary`
   false disputes on `test4`, 22 rested on a fact beside a promise and 4
   beside a status report.
4. **Benchmark.** `main3`'s offer is labelled as an offer, not a fact (its
   messages are unchanged and pinned). The scorer judges offers found, their
   state and the condition-due alert. `main3` plants no activation or lapse,
   so a `main4` profile adds the customer saying the condition held, failed,
   or nothing; `dev6` (8 tickets, seed 6174, the first from 6161 with each
   outcome at least twice) uses it. The runner's `--reread` reads listed
   messages live inside a replay. The oracle scores 1.000 on every split.

### Free replay ($0)

Round 8's proposals, scored by the Round 9 scorer under rule version 4 and
version 5. No proposal can create a conditional item, so offers are not
measured here. [Replay](../results/round9/replay.json)

| `test4` (`dev5`) | Rules v4 | Rules v5 (0005) |
|---|---:|---:|
| False disputes per ticket | 2.30 (1.75) | **1.30 (1.00)** |
| Freshness error | 0.029 (0.000) | 0.027 (0.000) |
| Fact recall | 0.921 (0.914) | **1.000 (0.978)** |
| Gold promises lost | 1 (0) | 1 (0) |

0005 removes all 20 `order.number` disputes on `test4` (6 on `dev5`) and
shows the customer's order again. Round 8's own record reads 0.024 freshness
and 0.914 fact recall on `test4`: the offer then counted as a fact.

### Targeted re-extraction (live, then replay)

| Messages read again | `dev5` | `test4` |
|---|---:|---:|
| Conditional offers | 8 | 20 |
| Plain promises Round 8 filed as `offer.conditional` (incl. the 4 silent-loss cases) | 2 | 6 |
| Messages where Round 8 added an `issue.summary` fact | 11 | 37 |
| Control sample (random, `random.Random(9)`) | 61 | 139 |

The control sample is 160 messages where the label or Round 8 had a
proposal and 40 where neither had, drawn from both splits together.
[`dev5`](../results/round9/dev5.json) · [`test4`](../results/round9/test4.json) ·
[selection](../results/round9/reread.json)

| Measure | Round 8, `test4` | **Round 9, `test4`** | **Round 9, `dev5`** |
|---|---:|---:|---:|
| Gold promises lost | 1 | **0** of 464 | 0 of 179 |
| False closes / false alerts per ticket | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 |
| False disputes per ticket | 2.30 | **0.00** | 0.00 |
| Dispute recall | 1.000 | 1.000 | 1.000 |
| Fact recall / freshness error | 0.914 / 0.024 | 1.000 / 0.027 | 0.978 / 0.000 |
| Conditional offers recorded at the end | 17 of 20 as a fact | **20 of 20** | 8 of 8 |
| Offer state right, all checkpoints | — | 59 / 59 | 22 / 22 |
| Condition-due alerts (right, false, missed) | — | **28, 0, 0** | 11, 0, 0 |
| Commitment precision / recall | 0.996 / 0.998 | 0.996 / 1.000 | 0.994 / 1.000 |
| Context-dependent / ambiguous / unrelated close | 20/20 each | 20/20 each | 8/8 each |

- **Offers.** All 28 offers on `dev5` and `test4` were read as offers, each
  with its condition's date ("до 24 января") as the condition's deadline, none
  as a commitment; no other message produced an offer.
- **The four silent-loss cases.** M0002 seq 23 "Начислим вам компенсацию
  бонусами." is now a commitment; it was a fact. In M0012 (172), M0015 (78)
  and M0017 (73) the plain promise is a commitment and nothing else, so the
  earlier offer stays a pending offer. So do M0001 (30) and M0019 (65).
- **Issue-summary noise.** None of the 20 messages after the first where
  Round 8 added an `issue.summary` fact has one now. The customer's opening
  "проблема с заказом" is still a fact in all 28 tickets.
- **Control sample: no regression.** No promise and no fact the label holds
  was lost in 200 messages. Lost were 6 facts that restated a promise or a
  prediction ("refund.status: Возместим стоимость монтажа…", "payment.status:
  Платёжная система обычно обновляет…"). Added: an `AnswerQuestion` beside
  four short confirmations, and once the agent's second order number, now a
  second value rather than a dispute. With 0 of 160 content-bearing messages
  regressing, the rate could still be up to about 2% (95%, rule of three).
- **Activation and lapse** (`dev6`, template words, [record](../results/round9/dev6.json)):
  8 of 8 offers recorded with the condition's deadline, 2 of 2 activations
  and 3 of 3 lapses read right, every offer state right at all 29
  checkpoint judgements, and 5 of 5 condition-due alerts with none false.

### What is wrong, and what was not checked

- **Not a full run.** 4,519 of 4,803 `dev5` and `test4` messages carry Round
  8's model output. The prompt's effect on them is estimated from the
  control sample only.
- **Activation and lapse were read on 5 messages**, written by templates, in
  `dev6`. `main3` plants none, so `test4` and `dev5` measure offers and
  condition-due alerts only. A lapse worded with a negation ("пришла, но
  повторная не нужна") would be refused as agreeing with the condition: the
  offer stays pending with a suggestion, which is safe but untested on real
  phrasing.
- **Freshness error 0.027 on `test4` is unchanged:** the same 7 presentations
  of a contested device-model code filed under `delivery.status`. Nothing in
  this round addresses it.
- 0005's correction rule ("не X, а Y") is not planted by the benchmark; only
  unit tests exercise it.
- **Cost per message is not measured.** The 282 paid reads averaged $0.00017
  a call, about twice Round 8's average; the chosen messages are not typical,
  so this says little about a full run.
- The splice first dropped one promise and mishandled two on `dev5`: a
  re-read message put its promise at another place among its proposals, and
  a later recorded close named the old place. The runner now follows the
  item; the numbers above are after the fix.

**Spend: $0.0599 of the $0.10 cap.** $0.0005 for a 2-message smoke test,
$0.0144 for `dev5`, $0.0013 to read again the `dev5` messages whose card
changed with the splice fix, $0.0346 for `test4`, and $0.0090 for `dev6`, of
which about $0.0068 went to the verifier on the planted closes (a runner
bug, fixed). Every later run came from the response cache at $0.
[Spend log](../results/round9/spend.jsonl)

```bash
python -m duebook.bench.experiment --split test4 --methods event_memory \
    --replay results/round8/test4.json --reread results/round9/reread.json \
    --max-usd 0.06 --session-cap 0.10 --results results/round9 --out /tmp/test4.json
python -m duebook.bench.experiment --split dev6 --template --methods event_memory \
    --replay oracle --projection-from results/round8/dev5.json \
    --reread results/round9/reread-dev6.json --max-usd 0.01 --results results/round9 \
    --out /tmp/dev6.json
```

## Round 8 — promise identity, what counts as a promise, and an unseen test

**`test4` is the first unseen test of this version.** Its 20 tickets
(seed 5353, profile `main3`) were generated, rewritten once and run once, and
nobody read them before the run. Nothing was tuned on them. The other figures
in this document come from `test3`, which our method had already been run on
four times.

On `test4` our method kept 463 of 464 gold promises. It made no false close
at any checkpoint and no false alert. It read every delegated undertaking and
every pair of same-action promises right, and it put no promise on any
prediction or status report. The one promise lost was a silent loss that this
round's prompt change caused (below). False disputes rose to 2.30 per ticket,
against 0.00 in Round 7. It cost $0.0159 a ticket.

### What changed

1. **Promise identity** ([decision 0003](decisions/0003-promise-identity.md),
   accepted). A new commitment is refused as a duplicate only when it restates
   an open one: same owner, wording that names nothing the other's does not
   (in either direction), and equal deadlines or one absent. Until now the same
   action was enough. Rule version 4; events recorded under versions 1 to 3
   replay with the old rule.
   **Replay at $0.** Every saved run's proposals were folded under both rules:
   Rounds 4 to 7 on `test3`, Round 2's `test2` and `dev2`, `dev3` and `dev4`.
   Under each run's own closing rules, the old duplicate rule refused nothing
   and dropped **no** real promise. Under today's closing rules it dropped
   **one** (Round 4, M0017 seq 130: "Отправим вам замену через неделю", a
   renegotiation proposed without `supersedes` while the first promise was
   still open). The new rule keeps it and refuses nothing the old rule
   admitted. The benchmark's actions had always named their objects, so it
   never exercised this rule; `main3` now does.
   [Replay](../results/round8/identity-replay.json)
2. **What counts as a promise** (prompt only). A commitment is the speaker's
   side becoming accountable for something happening, whoever does the work.
   Work by a courier, the finance department or engineers is a company
   commitment. A prediction about a third party and a status report are not
   commitments. The `action` must name what is affected. The instructions are
   still first and fixed, and 88% of prompt tokens were served from cache.
3. **Conditional offers** ([decision 0004](decisions/0004-conditional-commitments.md),
   proposed): designed, not built. Until the owner decides, the prompt files a
   conditional offer as an `offer.conditional` fact, never as a commitment.
   This keeps the offer visible on the card and in the brief instead of
   dropping it silently. `test4` shows what this costs (below).
4. **Benchmark.** The `main3` profile adds these beats to `main2`: two
   delegated undertakings (with or without a date), two promises with one
   action on different objects (settled separately), a prediction, a status
   report and a conditional offer. Every earlier split is byte-identical and
   now pinned by hash. On `dev5`, `test4` and 200 seeded `main3` tickets the
   oracle scores 1.000. The scorer puts every gold promise in one stage:
   (a) missed by the model, (b) dropped by the reducer, or (c) mishandled
   later (closed or alerted against its label).

### Development on `dev5`

`dev5` (8 tickets, seed 5151) was run once. All 179 gold promises were right
and there was no false close or false alert. False disputes were 1.75 per
ticket. One prompt fix was considered and not made, because it would cost a
full second `dev5` run ($0.125: a prompt change empties the response cache).
After that, `test4` would have fitted the cap only on its expected cost.
Some of the `dev5` analysis below was finished after the `test4` run had
started, so `test4` carries what it found.
[dev5](../results/round8/dev5.json)

### Round 8 on `test4`, beside Round 7 on `test3`

These are **different ticket sets**: `test4` uses the `main3` profile, and
`test3` uses `main2`, which our method had seen four times. **The baselines
were not run on `test4`.** Their `test3` numbers stay in Round 7's table.
[Record](../results/round8/test4.json)

| Measure | **Round 8, `test4`** (unseen) | Round 7, `test3` (seen 4×) |
|---|---:|---:|
| Gold promises | 464 | 367 |
| Missing: (a) missed by the model | **1** | 2 |
| Missing: (b) dropped by the reducer | **0** | 0 |
| (c) Mishandled later | **0** | 1 |
| False closes per ticket, end / across checkpoints | **0.00 / 0.00** | 0.00 / 0.00 |
| False alerts per ticket | **0.00** | 0.05 |
| Breach-alert precision / recall | **1.000 / 1.000** | 0.992 / 1.000 |
| False disputes per ticket | **2.30** | 0.00 |
| Dispute recall | **1.000** | 1.000 |
| Failed-step precision / recall | **1.000 / 1.000** | 1.000 / 1.000 |
| Fact recall / freshness error | **0.914 / 0.024** | 1.000 / 0.000 |
| Context-dependent / ambiguous / unrelated close | **20/20, 20/20, 20/20** | 19/20, 20/20, 20/20 |
| Commitment precision / recall / F1 | **0.996 / 0.998 / 0.997** | 0.986 / 0.995 / 0.991 |
| Status accuracy / deadline accuracy | **1.000 / 1.000** | 0.997 / 1.000 |
| Cost per ticket | **$0.0159** | $0.0140 |

The stages for Round 7 come from rescoring its record.

The new beats on `test4` (end of ticket):

| Beat | Cases | Result |
|---|---:|---|
| Delegated, with a date | 16 | 16 right |
| Delegated, without a date | 24 | 24 right |
| Two promises with one action, different objects | 40 | 40 right |
| Prediction about a third party | 20 | 20 without a promise |
| Status report | 20 | 20 without a promise |
| Conditional offer | 20 | 20 without a promise; 17 shown as a fact, 3 not |

### What is wrong

- **The only lost promise is a silent loss that this round's prompt caused.**
  In M0002, "Начислим вам компенсацию бонусами." was filed as an
  `offer.conditional` fact and not as a commitment. It never alerts. The same
  confusion explains the 3 conditional offers not shown: in M0012, M0015 and
  M0017 a later plain promise was also filed under `offer.conditional` and
  replaced the offer as its current value. On `dev5` this happened once,
  where the promise was kept as a commitment as well. The interim "offer as a
  fact" rule needs a tighter wording, or decision 0004's conditional item, and
  a dev run before it can be trusted.
- **False disputes, 2.30 per ticket** (46 across checkpoints; `dev5` 1.75).
  - 26 are `issue.summary` facts the model added beside a commitment, mostly
    for "restore access" promises (12 times on `test4`). This never happened
    in Round 7's `test3` or `dev4`, so this round's prompt or the new
    access-promise wording probably causes it.
  - 20 are `order.number` disputes, a **benchmark artifact**: in the
    same-action beat the agent names a second order number ("Оформим возврат
    по заказу 538916"), and the model files it against the customer's order.
    The same disputes hide 6 order numbers from the current facts, which is
    most of the fall in fact recall.
- **Freshness error 0.024.** These are 7 checkpoint presentations of a
  contested device-model code that the model filed under `delivery.status`.
- **Cost** is 14% above Round 7 per ticket ($0.0159 against $0.0140), with
  the longer instructions and about 8 more planted messages a ticket.

**Projection and spend.** The `test4` projection from `dev5` was $0.324
expected, $0.374 at the dearest dev rate, and $0.562 at the runner's 1.5×
upper bound. The upper bound did not fit the $0.497 left, so, as in Round 7,
the run was judged on the expected figure and capped at $0.49 by its
ledger. It cost $0.3159. The session spent **$0.5185 of the $0.70 cap**:
$0.0230 rewriting `dev5`, $0.0549 rewriting `test4`, $0.1247 for `dev5` and
$0.3159 for `test4`. No baseline was run. [Spend log](../results/round8/spend.jsonl)

```bash
python -m duebook.bench.experiment --split dev5 --methods "" --rewrite-only \
    --max-usd 0.05 --session-cap 0.70 --results results/round8
python -m duebook.bench.experiment --split test4 --methods "" --rewrite-only \
    --max-usd 0.10 --session-cap 0.70 --results results/round8
python -m duebook.bench.experiment --split dev5 --methods event_memory \
    --max-usd 0.16 --session-cap 0.70 --results results/round8 \
    --projection-from results/round7/dev4.json --expected
python -m duebook.bench.experiment --split test4 --methods event_memory \
    --max-usd 0.49 --session-cap 0.70 --results results/round8 \
    --projection-from results/round8/dev5.json --expected
```

## Round 7 — Round 4's answer format back, no stalled ticket, no batching

Round 6's three open problems came from Round 5's compact answer format.
With Round 4's format restored, the ticket that stalled reads on, false
disputes fall from 2.55 to 0.00 per ticket, and failed-step precision
returns to 1.000. On `test3` Round 7 misses the same two gold promises as
Round 4 and makes no false close at any checkpoint. It raises 0.05 false
alerts per ticket and recovers 19 of 20 context-dependent closes, at $0.0140
a ticket. This is the **fourth look at `test3`** for our method (Rounds 4 to
7), so these are follow-up numbers on a known set, not a fresh test.

### What changed

1. **Answer format.** The extractor again answers with one list per kind of
   change, as in Round 4. `context_quote`, the verifier veto, the preceding
   turn and the amended contextual-close rule stay. The compact format is
   deleted. Read alone against Round 6's card, "Письма придут по отдельности.
   Напишите, пожалуйста, какое получите первым." now answers in 95 tokens
   with a question in both stalled tickets ($0.0005).
   [Stall check](../results/round7/stall-check.json)
2. **Fail fast, then escalate.** An extraction answer is capped at 800
   tokens. An answer that is cut off, empty or not JSON is not asked again
   at temperature 0. It is read once more with reasoning "low", under a
   4,000-token ceiling. On the two stall messages that read is also right and
   spends 2,006–2,746 hidden tokens ($0.0026). Timeouts are not retried.
3. **An unreadable message no longer stalls its ticket**
   ([decision 0002](decisions/0002-unreadable-message.md)). After the third
   failed attempt, the event is applied with an explicit unreadable record.
   The card and brief show "Сообщение №N не удалось прочитать — проверьте его
   вручную" with its source. The assignee gets an `unreadable` alert, and the
   ticket goes on. Timeouts and outages still only hold the ticket.
4. **Batching is removed**, with its schema, settings, timeout and tests.

In every run of this round, all 5,511 messages were answered on the first
read: no escalation and no unread item occurred. Steps 2 and 3 are exercised
by tests and the stall check only.

### Development

`dev3` uses template text, as in Round 5. `dev4` is new: seed 4747, eight
`main2` tickets, rewritten once by the benchmark's writer (276 of 1,348
rewrites discarded). Neither shows a regression against Round 4 on missing
promises, false disputes or failed-step precision.
[dev3](../results/round7/dev3.json) · [dev4](../results/round7/dev4.json)

| Measure | `dev3` (4) | `dev4` (8) |
|---|---:|---:|
| Gold promises missing from the card | 1 of 79 | 1 of 154 |
| Tickets stalled / unreadable items | 0 / 0 | 0 / 0 |
| False closes per ticket, end / across checkpoints | 0.00 / 0.00 | 0.00 / 0.00 |
| False alerts per ticket | 0.00 | 0.00 |
| False disputes per ticket | 0.00 | 0.00 |
| Dispute recall | 1.000 | 1.000 |
| Failed-step precision | 1.000 | 1.000 |
| Fact recall / freshness error | 1.000 / 0.000 | 1.000 / 0.027 |
| Context-dependent / ambiguous / unrelated close | 4/4, 4/4, 4/4 | 8/8, 8/8, 8/8 |
| Commitment F1 | 0.994 | 0.990 |
| Cost per ticket | $0.0128 | $0.0139 |

### Round 7 on `test3`

One live run of our method, scored with the frozen baselines from
`results/round4/test3.json`. Round 4 is that record's event memory. Rounds 5
and 6 are rescored by the current code from their frozen records. Missing
promises are counted by matching the final card with the gold promises (this
count gives Round 5 15 rather than the 14 reported earlier).
[Record](../results/round7/test3.json) · [Rounds compared](../results/round7/compare-rounds.json)

| Measure | Round 4 | Round 5 | Round 6 | **Round 7** | Full context | Mem0 | Graphiti | Rolling summary |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Gold promises missing from the card | 2 | 15 | 12 | **2** | 0 | 6 | 19 | 24 |
| Tickets stalled | 0 | 0 | 2 | **0** | 0 | 0 | 1 | 3 |
| Unreadable items | — | — | — | **0** | — | — | — | — |
| False closes per ticket | 0.20 | 0.05 | 0.00 | **0.00** | 0.10 | 0.15 | 0.80 | 0.25 |
| False closes across checkpoints per ticket | 0.90 | 0.15 | 0.00 | **0.00** | 0.30 | 0.65 | 1.90 | 0.50 |
| False alerts per ticket | 1.10 | 0.45 | 0.20 | **0.05** | 0.45 | 0.40 | 0.40 | 0.75 |
| False disputes per ticket | 0.30 | 0.40 | 2.55 | **0.00** | 0.00 | 0.10 | 0.10 | 2.00 |
| Dispute recall | 1.000 | 0.879 | 0.978 | **1.000** | 0.560 | 0.604 | 0.192 | 0.667 |
| Failed-step precision | 1.000 | 0.776 | 0.777 | **1.000** | 0.792 | 0.771 | 0.771 | 0.963 |
| Fact recall | 1.000 | 0.972 | 0.984 | **1.000** | 1.000 | 1.000 | 0.956 | 0.815 |
| Freshness error | 0.000 | 0.064 | 0.016 | **0.000** | 0.291 | 0.310 | 0.278 | 0.333 |
| Context-dependent close recovered | 0.00 | 0.85 | 0.90 | **0.95** | 0.85 | 0.90 | 0.60 | 0.50 |
| Ambiguous close refrained | 1.00 | 0.95 | 1.00 | **1.00** | 0.95 | 0.85 | 0.60 | 0.50 |
| Unrelated confirmation handled | 0.80 | 0.95 | 1.00 | **1.00** | 1.00 | 1.00 | 1.00 | 1.00 |
| Commitment F1 | 0.989 | 0.975 | 0.970 | **0.991** | 0.997 | 0.990 | 0.942 | 0.797 |
| Status accuracy | 0.926 | 0.957 | 0.989 | **0.997** | 0.948 | 0.945 | 0.913 | 0.915 |
| Breach-alert precision / recall | 0.850 / 0.969 | 0.932 / 0.961 | 0.969 / 0.977 | **0.992 / 1.000** | 0.934 / 0.992 | 0.941 / 0.984 | 0.933 / 0.862 | 0.864 / 0.760 |
| Cost per ticket | $0.01337 | $0.01102 | $0.01401 | **$0.01399** | $0.00434 | $0.05299 | $0.16024 | $0.09563 |

Graphiti covers 10 tickets and the rolling summary four. Paired over tickets
with Round 4: 1.05 fewer false alerts per ticket [1.00, 1.15],
status accuracy +0.071 [+0.059, +0.086], breach-alert precision +0.142
[+0.131, +0.156], and $0.00066 [0.00046, 0.00084] more per ticket. Commitment
F1 is level (+0.001 [0.000, 0.004]). Against full context: 0.40 fewer false
alerts [0.15, 0.70], 0.30 fewer false closes across checkpoints [0.05,
0.65], status accuracy +0.049 [+0.030, +0.068]. Commitment F1 is 0.007
lower [0.000, 0.014], and Round 7 costs 3.2 times as much.

What is still wrong:

- **Two promises are missed**, the same two Round 4 missed (M0003 seq 114,
  M0010 seq 143).
- **One context-dependent close is missed** (M0000, seq 134). It is refused as
  not about its target and kept as a suggestion. The promise stays open, which
  causes the run's one false alert. It fails safe.
- **Cost** is level with Round 6 and 4.6% above Round 4. Extraction answers
  average 80.6 output tokens (Round 4: 79.0), 88.3% of prompt tokens come
  from the cache, and the verifier costs $0.0012 a ticket.

**Projection.** From `dev4`, the run projected $0.288 expected and $0.487 at
the runner's upper bound. The upper bound is 1.5 times the dearest dev
ticket's rate per message. With $0.413 left, the upper bound did not fit.
The dearest rate itself did ($0.325), so the run was judged on the expected
figure (`--expected`) and capped at $0.41 by its ledger. It cost $0.2798.

**Spend.** The session charged **$0.4668** of the $0.60 cap. That was $0.0031
for the stall check and escalated read, $0.0231 to rewrite `dev4`, $0.0509
for `dev3`, and $0.1099 for `dev4`, including a $0.0024 projection sample
for a first attempt refused by its own $0.15 cap. The `test3` run cost
$0.2798. [Spend log](../results/round7/spend.jsonl)

```bash
python -m duebook.bench.experiment --split dev4 --methods "" --rewrite-only \
    --max-usd 0.05 --session-cap 0.60 --results results/round7
python -m duebook.bench.experiment --split dev3 --template --methods event_memory \
    --max-usd 0.10 --session-cap 0.60 --results results/round7 \
    --frozen results/round5/dev3.json --compare-frozen-event-memory
python -m duebook.bench.experiment --split dev4 --methods event_memory \
    --max-usd 0.18 --session-cap 0.60 --results results/round7
python -m duebook.bench.experiment --split test3 --methods event_memory \
    --max-usd 0.41 --session-cap 0.60 --results results/round7 \
    --frozen results/round4/test3.json --compare-frozen-event-memory \
    --projection-from results/round7/dev4.json --expected
```

## Round 6 — batching off, and a tighter contextual close

Round 5's batching caused its missing promises. Read alone, 12 of the 14
promises absent from its card are made. With batching off, Round 6 is missing
one gold promise among the messages it read, the same one Round 4 missed. It
made no false close at any checkpoint, raised 0.20 false alerts per ticket,
and kept 18 of 20 context-dependent closes. Two problems are new to this run,
and neither comes from batching. Two tickets stopped on a message the model
answers with looping, truncated JSON. Free-text "facts" now open false disputes
(2.55 per ticket). This is the third look at `test3` for our method (Rounds 4,
5 and 6), so these are follow-up numbers on a known set, not a fresh test.

### What caused the missing promises

Round 5's batch groups were rebuilt from its frozen proposals and recorded
batch sizes. Its live code fell back for only the first message of a
timed-out batch, and the rebuild matches every recorded size. All 14 missing
promises had been read in a batch that succeeded, and 5 of them came first in
their batch. Their messages and 38 neighbours were read again one at a time:
the same prompt, preceding turn, compact output and card the batch saw
(52 calls).
[Record](../results/round6/batch-diagnosis.json)

- **Promises: ruled in.** 12 of 14 are made when read alone. The other two
  (M0003 seq 114, M0010 seq 143) are the two Round 4 also missed.
  Neighbours: no promise in Round 5's batches, one when read alone.
- **Second hypothesis, failed steps: ruled in.** Of 13 failed fixes lost by
  the end of a ticket, 12 had the suggestion and its "не помогло" in one
  batch. 12 of 29 such pairs were lost, against 1 of 25 pairs split across
  batches. In 7 of the 12, the batch named a step handle its card did not
  have. Read alone against the card that holds the step, all 12 report the
  failure (12 calls).
- **Second hypothesis, disputes: ruled out.** None of the 4 lost disputes had
  both values in one batch, so the earlier value was already on the card.
  Read alone, all 4 counter-claims land in the right slot (4 calls). Batching
  lost them, but not by hiding the earlier value.

The calls cost $0.0040. Batching is now off by default in the service and the
benchmark (`DUEBOOK_EXTRACTION_BATCHING`, `--batch`).

**Safe batching was not built.** Timestamps in `test3` are minute-granular,
and only 4.0% of consecutive message pairs arrive within 60 s (1.8% in the
same minute). A two-message window would batch about 2% of reads. A
10-ticket development run would hold roughly 70 batched pairs, too few to
show that recall matches. A deadline-phrase guard would also have left 5 of
the 14 lost promises exposed: they had no deadline ("Вышлем вам смету").
Batching stays off.

### The contextual-close rule, replayed

A close that relies on the preceding turn is now refused when the asking
sentences of the quoted question name something the target's wording does
not ([amendment to decision 0001](decisions/0001-verifier-veto.md), recorded
as proposed for the owner's confirmation). Round 5's frozen proposals and
verdicts were folded again at $0. On `test3`, false closes dropped from 0.05 to
0.00 per ticket at the end and from 0.15 to 0.00 across checkpoints. M0012 is
refused as `context_names_other`. All 17 true contextual closes are kept, as
are all 4 on `dev3`. Status accuracy went from 0.957 to 0.960, and
breach-alert recall from 0.961 to 0.969.
[Replay](../results/round6/rule-replay.json)

### Round 6 on `test3`

One live run, with batching off, the amended rule, and the verifier on. All
else is as in Round 5, scored against the frozen baselines. Round 5's
and Round 4's event memories are rescored with the same code and paired over
the same tickets.
[Record](../results/round6/test3.json) · [Rounds compared](../results/round6/compare-rounds.json)

| Measure | Round 4 | Round 5 | Round 6 | Full context | Mem0 | Graphiti | Rolling summary |
|---|---:|---:|---:|---:|---:|---:|---:|
| Gold promises missing from the card | 2 | 14 | **1** + 11 unread | — | — | — | — |
| False closes per ticket | 0.20 | 0.05 | **0.00** | 0.10 | 0.15 | 0.80 | 0.25 |
| False closes across checkpoints per ticket | 0.90 | 0.15 | **0.00** | 0.30 | 0.65 | 1.90 | 0.50 |
| Wrong-target closes per ticket | 0.05 | 0.00 | **0.00** | 0.30 | 0.20 | 0.10 | 0.00 |
| False alerts per ticket | 1.10 | 0.45 | **0.20** | 0.45 | 0.40 | 0.40 | 0.75 |
| Context-dependent close recovered | 0.00 | 0.85 | **0.90** | 0.85 | 0.90 | 0.60 | 0.50 |
| Ambiguous close refrained | 1.00 | 0.95 | **1.00** | 0.95 | 0.85 | 0.60 | 0.50 |
| Unrelated confirmation handled | 0.80 | 0.95 | **1.00** | 1.00 | 1.00 | 1.00 | 1.00 |
| Dispute recall | 1.000 | 0.879 | 0.978 | 0.560 | 0.604 | 0.192 | 0.667 |
| False disputes per ticket | 0.30 | 0.40 | **2.55** | 0.00 | 0.10 | 0.10 | 2.00 |
| Failed-step precision | 1.000 | 0.776 | 0.777 | 0.792 | 0.771 | 0.771 | 0.963 |
| Freshness error | 0.000 | 0.064 | 0.016 | 0.291 | 0.310 | 0.278 | 0.333 |
| Commitment F1 | 0.989 | 0.975 | 0.970 | 0.997 | 0.990 | 0.942 | 0.797 |
| Status accuracy | 0.926 | 0.957 | **0.989** | 0.948 | 0.945 | 0.913 | 0.915 |
| Breach-alert precision / recall | 0.850 / 0.969 | 0.932 / 0.961 | 0.969 / 0.977 | 0.934 / 0.992 | 0.941 / 0.984 | 0.933 / 0.862 | 0.864 / 0.760 |
| Cost per ticket | $0.01337 | $0.01102 | $0.01401 | $0.00434 | $0.05299 | $0.16024 | $0.09563 |
| Cost per incoming message | $0.0000769 | $0.0000635 | $0.0000807 | $0.0000249 | $0.000304 | $0.000911 | $0.000530 |

Graphiti covers 10 tickets and the rolling summary four. Against Round 5,
paired over tickets, Round 6 has fewer false alerts (−0.25 [−0.55, 0.00]),
better failed-step recall (+0.232 [+0.112, +0.364]) and status accuracy
(+0.031 [+0.014, +0.049]), more false disputes (+2.15 [+1.1, +3.2]), and costs
$0.0030 [0.0020, 0.0039] more per ticket. The dispute recall gain, +0.099,
has an interval that includes zero.

What is still wrong:

- **Two tickets stopped** (M0000 at event 133, M0003 at event 97). On "Письма
  придут по отдельности. Напишите, пожалуйста, какое получите первым." the
  model loops until the 4,000-token output limit, so its JSON is cut off. It
  did so on both attempts in both tickets and again in one reproduction
  ($0.0017 a try). At
  temperature 0, a service would hold such a ticket and pay for every retry.
  The 11 promises after the stops were never read. On the 18 finished
  tickets, commitment recall is 0.997 (Round 4 0.997, Round 5 0.958), and
  precision is 0.971 (0.982 and 0.994).
- **False disputes.** 11 of the 13 open at ticket end pit an `issue.summary`
  complaint against a status report ("Ваша заявка сейчас рассматривается").
  Round 5's compact prompt does this when it reads each message alone. Its
  batches happened to skip that filler, and Round 4's prompt did not do it.
- **Failed-step precision stays at 0.777.** A failure report is also
  recorded as a new failed step beside the listed one. All 12 single reads in
  the diagnosis did this. Round 5's prompt causes it, not batching.

Both prompt problems need a prompt change and a live development run. They
are left for the next round.

**Spend.** The session charged **$0.3032** of the $0.60 cap: $0.0040 for the
diagnosis, $0.2773 for the test run, and $0.0017 to reproduce the truncation.
A further $0.0203 went to a mistaken start of rewriting `dev3` during the
free replay, stopped by its $0.01 run cap. Round 5's `dev3` uses template text
(`--template`). [Spend log](../results/round6/spend.jsonl)

```bash
# the test run (live, once)
python -m duebook.bench.experiment --split test3 --methods event_memory \
    --max-usd 0.40 --session-cap 0.60 --results results/round6 \
    --frozen results/round4/test3.json --compare-frozen-event-memory
# the rule replay ($0)
python -m duebook.bench.experiment --split test3 --methods event_memory \
    --max-usd 0.01 --results /tmp/duebook-round6-replay \
    --replay results/round5/test3.json --frozen results/round4/test3.json \
    --compare-frozen-event-memory
```

## Round 5 — contextual closes and queued extraction

Round 5 cut false alerts from **1.10 to 0.45 per ticket** and recovered
**17 of 20** context-dependent closes. It cost **$0.01102 per ticket**, 17.5%
less than Round 4. The combined change falls short of a quality release gate.
Commitment recall dropped from 0.995 to 0.959, and dispute recall fell from
1.000 to 0.879. The experiment changed the context, response format, and
batching together, so it cannot isolate the cause of the extraction losses.

### Reducer replay before live work

The recorded Round 4 proposals and verifier verdicts were folded under the
[new closing rule](decisions/0001-verifier-veto.md) at $0. This was a post-hoc
replay on `test3` with the original extraction. It reduced false closes at
ticket end from four to zero and wrong-target closes from one to zero. The
unrelated-refund case improved from 16/20 to 20/20. At ticket end, breach
alerts went from 125 correct, 22 false, and four missed to 128 correct, 23
false, and one missed. Context-dependent closes remained at 0/20. Status
accuracy went from 0.926 to 0.937. Commitment F1 went from 0.989 to 0.988.
[Replay scores](../results/round5/reducer-replay.json)

### Live comparison

One run on the four original `dev3` tickets recovered all four contextual
closes, kept all four ambiguous and unrelated cases open, and made zero false
closes. One live run of the selected configuration processed all 20 archived
`test3` tickets, with no failed tickets. Both used DeepSeek V4.1 Flash on
DeepInfra. The verifier used low reasoning. The extractor used reasoning off,
compact responses, and groups of up to four consecutive messages. The run
treated adjacent archived messages as already queued, which models a backlog.
The frequency of groups in a live helpdesk depends on arrivals. `test3` had
already informed the design through Round 4, so these are follow-up results on
a known synthetic set. All baselines were read from their frozen records.
[Development record](../results/round5/dev3.json) · [Test record](../results/round5/test3.json)

| Measure | Round 4 | Rule replay | Round 5 | Full context | Mem0 | Graphiti | Rolling summary |
|---|---:|---:|---:|---:|---:|---:|---:|
| False closes per ticket | 0.20 | 0.00 | **0.05** | 0.10 | 0.15 | 0.80 | 0.25 |
| False closes across checkpoints per ticket | 0.90 | 0.00 | **0.15** | 0.30 | 0.65 | 1.90 | 0.50 |
| Wrong-target closes per ticket | 0.05 | 0.00 | **0.00** | 0.30 | 0.20 | 0.10 | 0.00 |
| False alerts per ticket | 1.10 | 1.15 | **0.45** | 0.45 | 0.40 | 0.40 | 0.75 |
| Context-dependent close recovered | 0.00 | 0.00 | **0.85** | 0.85 | 0.90 | 0.60 | 0.50 |
| Ambiguous close refrained | 1.00 | 1.00 | 0.95 | 0.95 | 0.85 | 0.60 | 0.50 |
| Unrelated confirmation handled | 0.80 | 1.00 | 0.95 | 1.00 | 1.00 | 1.00 | 1.00 |
| Breach-alert precision | 0.850 | 0.848 | **0.932** | 0.934 | 0.941 | 0.933 | 0.864 |
| Breach-alert recall | 0.969 | 0.992 | 0.961 | 0.992 | 0.984 | 0.862 | 0.760 |
| Status accuracy | 0.926 | 0.937 | **0.957** | 0.948 | 0.945 | 0.913 | 0.915 |
| Commitment F1 | 0.989 | 0.988 | 0.975 | 0.997 | 0.990 | 0.942 | 0.797 |
| Deadline accuracy | 1.000 | 1.000 | 1.000 | 0.935 | 0.909 | 0.932 | 0.915 |
| Freshness error | 0.000 | 0.000 | 0.064 | 0.291 | 0.310 | 0.278 | 0.333 |
| Dispute recall | 1.000 | 1.000 | 0.879 | 0.560 | 0.604 | 0.192 | 0.667 |
| Failed-step precision | 1.000 | 1.000 | 0.776 | 0.792 | 0.771 | 0.771 | 0.963 |
| Recorded model cost per ticket | $0.01337 | $0.01337 | $0.01102 | $0.00434 | $0.05299 | $0.16024 | $0.09563 |

Graphiti covers 10 tickets and rolling summary covers four. The replay cost
column carries the original calls' cost for comparison. The replay itself
charged $0. The record
contains ticket-level paired intervals and counts. There were 124 correct,
nine false, and five missed breach alerts at ticket end. Without verifier
verdicts, replaying the same Round 5 proposals recovered 0/20 contextual
closes. One contextual `yes` closed the assembly refund when the installation
refund was the intended target and had never been recorded (M0012). The
unrelated-confirmation case also failed on M0012 because the callback itself
was missing. The ambiguous-case failure on M0009 was a missing promise.
Both labels require every affected promise to be present.

### Cost and latency during active tickets

| Measure | Round 4 Duebook | Round 5 Duebook |
|---|---:|---:|
| Model cost per ticket | $0.01337 | $0.01102 |
| Model cost per incoming message | $0.00007679 | $0.00006332 |
| Model calls per ticket, including verifier | 182.65 | 63.00 |
| Provider prompt tokens served from cache | 88.7% | 72.6% |
| Extraction output tokens per extraction call | 79.0 | 211.3 |
| Extraction output tokens per incoming message | 79.0 | 66.2 |
| All output tokens per model call | 85.8 | 214.7 |
| Model seconds per incoming message | 1.462 | 0.808 |

Output per call grows because a batch describes several messages. Output
per incoming message dropped 16.1%. The run attempted 884 batches, of which
835 had four messages, 25 had three, and 24 had two. It scheduled 186
individual rereads within those groups. Batching adds zero deliberate wait
for arrivals. The first event in a group waited a median of 1.93 s (p95
3.61 s) for the batch request. Recorded individual extraction calls took a
median of 1.38 s (p95 2.39 s). The observed first-event differences were
0.55 s at the median and 1.23 s at p95 between these unpaired samples.
Seven measured batch requests exceeded 10 s under HTTP phase timeouts. The
production path now enforces a total wall-clock deadline and falls back for
the whole group. That change was tested with a stalled transport stub.
A related check against the whole preceding question preserved all 88 live
checkpoints in a free replay. The test split made one live pass, so its
latency measurements are from before the transport hardening.

| Comparison | Calls on 20 tickets | Cost per ticket | Cost per incoming message |
|---|---:|---:|---:|
| Full context at handoffs and end, measured | 88 | $0.00434 | $0.00002493 |
| Full context after every message, projected at the observed cache share | 3,481 | $0.15710 | $0.00090259 |
| Full context after every message, projected with all input cached | 3,481 | $0.07478 | $0.00042962 |
| Duebook maintaining the card, measured in Round 5 | 1,260 | $0.01102 | $0.00006332 |

The full-context projection uses the original 416,339 prompt and 86,064
output tokens at 88 checkpoints. For each ticket, scale its observed prompt
tokens by the ratio of total rendered prompt characters at all message
prefixes to those at its checkpoints. Hold its mean output length per answer
fixed. At the recorded DeepInfra prices, 14.02M prompt and 3.42M output tokens
cost $3.14 at the observed 13.5% cache share and $1.50 with all input cached.
These are estimates for re-answering the same full card after every message.
Round 5 still uses the stored card for reads and the scheduler for alerts,
both with zero model calls. [Projection inputs](../results/round5/continuous-cost.json)

The development projection was $0.0524. The test projection from the four
development tickets was $0.2260 expected and $0.4046 at the upper estimate,
without sampling any test messages. OpenRouter charged **$0.26386** in total
($0.04390 development and $0.21997 test), within the $0.60 cap. [Spend log](../results/round5/spend.jsonl)

To refold only the saved Round 5 proposals and rescore the frozen comparison
at zero model cost:

```bash
python -m duebook.bench.experiment --split test3 --methods event_memory \
    --max-usd 0.01 --results /tmp/duebook-round5-replay \
    --replay results/round5/test3.json --frozen results/round4/test3.json \
    --compare-frozen-event-memory
```

## Round 4: final comparison on `test3`

Run once, on 2026-11-18, on a fresh seed that nobody had read before. The
test tickets and the baselines' answers are now frozen; later work re-runs only
the event memory (see [the end of this section](#what-is-frozen-and-how-to-re-run-only-our-method)).
Record: `results/round4/test3.json`; every charge: `results/round4/spend.jsonl`.

### Short version

- **Where the event memory wins, clearly.** Deadline accuracy 1.000 (365/365)
  against 0.909–0.935 for every baseline; freshness error 0.000 against
  0.28–0.33; dispute recall 1.000 against 0.19–0.67; failed-step precision
  1.000 against 0.77–0.79 for full context, Mem0 and Graphiti. Against those
  three every one of these paired intervals excludes zero (against the
  rolling summary's 4 tickets, freshness and disputes do). Its answer at a
  handoff costs no model call.
- **Where it loses, plainly.**
  - **The rescue case: 0 of 20.** "Ага, вижу." after "Проверьте, поступило ли
    возмещение стоимости монтажа", with two near-identical promises open.
    Full context gets 0.85, Mem0 0.90, Graphiti 0.60. On all 20 the extractor
    proposed nothing: it reads the card and the one new message, and that
    message names nothing. The kept promise stays open, so the event memory
    raises 20 false alerts (1.10 a ticket against 0.40–0.45) and misses 20
    closes (missed-close rate 0.139 against 0.076).
  - **The wrong-thing trap: 0.80** (16 of 20); every baseline 1.00. In 4
    tickets "Кстати, деньги за прошлый заказ пришли." closed the callback
    "Я свяжусь с вами по телефону", which was still owed. These are all 4 of
    its false closes at the end of a ticket (0.20 a ticket). Full context is
    lower (0.10) and Mem0 about level (0.15); neither difference excludes
    zero.
  - **Status accuracy** 0.926 against 0.948 for full context (paired
    difference +0.022 [0.000, 0.047]). Commitment F1 0.989 against 0.997 for
    full context (+0.008 [0.000, 0.017]).
- **The verifier added nothing useful on test3.** It changed one outcome,
  and that one was wrong. It said "no" to all 4 false closes above, but a "no"
  is not a veto, and the deterministic rule that let them through does not
  consult it. Details [below](#what-the-verifier-changed).
- **The guards earned their place.** On the same proposals with the guards
  off: 1.20 false closes a ticket instead of 0.20 and 0.50 wrong-target
  closes instead of 0.05.
- **Graphiti, 10 tickets.** The dispute finding holds on the larger sample:
  dispute recall 0.19; of the 11 planted disputes still open when its graph
  last held them, 8 had one side's value closed by the other's (7 the
  customer's by the company's). It also makes the most false closes (0.80 a
  ticket) and costs 12 times as much as the event memory.
- **Cost.** Event memory $0.0134 a ticket, verifier included ($0.00095);
  full context $0.0043; Mem0 $0.053; rolling summary $0.096; Graphiti $0.160.
  The session charged **$3.47 of the $4.50 approved**.

### What was run

| | |
|---|---|
| Split | `test3`: seed 4343, 20 `main2` tickets, 150–200 messages, 3–4 handoffs, 3 hard close cases each (60 in all) |
| Text | rewritten once by `~z-ai/glm-flash-latest` (GLM 5.3 Flash), never by the reader: 3,481 messages, 748 rewrites discarded for their template words; `results/round4/tickets/test3.json` |
| Reader | `deepseek/deepseek-v4.1-flash`, reasoning off, locked to DeepInfra (`PRESETS`), for every method: all 3,653 event-memory calls were served by DeepInfra |
| **event memory** | as the product ships it: extractor reasoning off, guards, verifier (same model, reasoning low) on every proposal that would close, cancel or replace an open promise. **All 20 tickets** |
| event memory without the verifier | the same recorded proposals folded again with the verdicts ignored (`event_memory_no_verifier`); no extra call |
| event memory without guards | the same proposals, guards off (`event_memory_unguarded`); no extra call |
| full context | **all 20** |
| Mem0 | **all 20** (`mem0ai` 2.2.1) |
| Graphiti | **the first 10** (`graphiti-core` 0.31.2), the owner's choice for cost |
| rolling summary | **the first 4** (see the cut and restore below) |

Paired differences are over the tickets both methods ran on. Every interval
is a 95% bootstrap over tickets (2,000 resamples).

**Cost control.** The first projection from a 20-event sample gave $4.50
(upper) for the run, $4.66 with what had been spent, over the cap; by the
brief, the rolling summary was cut from 4 tickets to 2. The run's own
projection, a minute later, gave $3.97: Mem0's sample cost less the second
time. Once the first pass's real cost was known ($3.15 logged for the session
by then, $1.35 left), the rolling summary went back to the brief's 4
tickets.

**Interruptions, and the rule applied to them.** Every finished (method,
ticket) was journalled, so nothing paid was paid twice: a resume reads every
answer it already has from the response cache.

- The first pass ran every method at once. Three event-memory tickets and one
  rolling-summary ticket stopped on a connection reset, which is not retried
  (the answer may have been billed). Graphiti stopped on M0005 at event 6: one
  of its own calls came back as 25,000 characters of unreadable JSON, three
  times. The container then restarted at the very end of the pass, before the
  run had logged its charge; the rolling summary's M0000 was cut by it.
- The rule: **every ticket that stopped was resumed in the next pass, from
  the journal and the cache; the third pass was the last, and what it gave is
  final.** In the second pass the event memory's three finished, and so did
  the rolling summary's M0000; its M0001 stopped again (below), and M0002 and
  M0003, added back, stopped on 429s. In the third pass Graphiti's M0005
  failed again, identically, at no cost: the bad answer was cached. The
  rolling summary stopped again on all three: two on an answer cut off
  mid-JSON (M0001 at event 123, twice; M0003 at 141): the summary had
  outgrown one answer. The third, M0002, stopped on a proxy timeout at
  event 58.
- A ticket that stopped is scored on what the method held, as the pipeline
  would hold a ticket. Graphiti's 10-ticket numbers therefore carry one ticket
  read to event 6; its 9 complete tickets are given below. The rolling summary
  completed one ticket of four; its numbers are reported but say little.
- The record is the third pass's output. Its `frozen_from` names the second
  pass's record, a working file not kept; every run of it is in this record.
- The first pass's charge was taken from the OpenRouter key's usage counter
  once it settled ($2.9923). The counter and the log agree to $0.003 for
  everything since the rewrite.

### Step 1: the sanity check before spending

Three tickets (M0003, M0007, M0009) were read end to end against their gold
labels, and then every rewritten planted message of all 20 (400 of 880).

- **One labelling bug, fixed.** The company's "По накладной вам отправлен
  BL-967" protected only the code. In 3 of 20 tickets the writer kept the code
  and changed what it is ("Номер вашей накладной — BL-967"). The gold "device
  model disputed, still open" then contradicted the text. Every other planted
  fact already keeps the word that says what its value is ("списано 4100
  рублей", "на тариф Премиум"). The model dispute now does too, on both sides.
  The 15 stored rewrites that dropped the word, these 3 and 12 harmless ones,
  went back to their template words. Re-check: the planted proposals, folded
  through the reducer on the final test3 text, score 1.000 on every measure
  and on all 60 hard cases. The self-check is 1.000 on `dev3`, `test3` and 200
  seeded tickets. Round 2's splits changed in those spans only, and its
  test record re-scores identically. **Round 2's test2 text has the same
  fault in 2 tickets** (M0005, M0015): its dispute numbers carry it.
- **Not a bug, but a convention the labels follow.** `temporal` reads "до
  пятницы" said on a Friday as that same Friday. 5 of 367 promises (1.4%) have
  such a deadline. A reader may well mean the next Friday. The event memory
  resolves it exactly as the labels do, so this favours it slightly on
  deadline accuracy.
- **Style, noted.** The writer turned "Проверил" into "Перепроверила" for male
  agents in the settling message. It added detail now and then ("замена уже
  подготовлена"). One first statement of an address reads as a change
  ("Мне нужно поменять адрес доставки"). The hard-case messages are verbatim
  by design.

### Results (end of each ticket unless marked)

Value [95% interval]. Graphiti: 10 tickets; rolling summary: 4 (1 complete).

**Promises and alerts**

| metric | event_memory | event_memory_no_verifier | event_memory_unguarded | full_context | mem0 | graphiti | rolling_summary |
|---|---|---|---|---|---|---|---|
| commitment_f1 | 0.989 [0.982, 0.995] | 0.988 [0.980, 0.995] | 0.989 [0.982, 0.995] | 0.997 [0.993, 1.000] | 0.990 [0.982, 0.997] | 0.942 [0.826, 0.994] | 0.797 [0.524, 0.955] |
| commitment_precision | 0.984 [0.973, 0.995] | 0.984 [0.973, 0.994] | 0.984 [0.973, 0.995] | 0.995 [0.987, 1.000] | 0.997 [0.992, 1.000] | 0.994 [0.980, 1.000] | 1.000 [1.000, 1.000] |
| commitment_recall | 0.995 [0.986, 1.000] | 0.992 [0.981, 1.000] | 0.995 [0.986, 1.000] | 1.000 [1.000, 1.000] | 0.984 [0.967, 0.997] | 0.894 [0.706, 0.994] | 0.662 [0.355, 0.914] |
| deadline_accuracy | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.935 [0.907, 0.958] | 0.909 [0.878, 0.938] | 0.932 [0.880, 0.969] | 0.915 [0.792, 1.000] |
| status_accuracy | 0.926 [0.908, 0.940] | 0.926 [0.907, 0.940] | 0.890 [0.869, 0.910] | 0.948 [0.927, 0.969] | 0.945 [0.919, 0.967] | 0.913 [0.874, 0.955] | 0.915 [0.852, 0.938] |
| breach_alert_recall | 0.969 [0.938, 0.992] | 0.961 [0.921, 0.992] | 0.853 [0.824, 0.882] | 0.992 [0.976, 1.000] | 0.984 [0.961, 1.000] | 0.862 [0.667, 0.985] | 0.760 [0.348, 1.000] |
| breach_alert_precision | 0.850 [0.830, 0.867] | 0.844 [0.818, 0.863] | 0.902 [0.871, 0.935] | 0.934 [0.889, 0.977] | 0.941 [0.906, 0.975] | 0.933 [0.867, 1.000] | 0.864 [0.727, 0.952] |
| false_alerts_per_ticket | 1.100 [1.000, 1.250] | 1.150 [1.000, 1.300] | 0.600 [0.400, 0.800] | 0.450 [0.150, 0.800] | 0.400 [0.150, 0.650] | 0.400 [0.000, 0.800] | 0.750 [0.250, 1.000] |

**Closes**

| metric | event_memory | event_memory_no_verifier | event_memory_unguarded | full_context | mem0 | graphiti | rolling_summary |
|---|---|---|---|---|---|---|---|
| false_closes_per_ticket | 0.200 [0.050, 0.400] | 0.200 [0.050, 0.400] | 1.200 [0.950, 1.500] | 0.100 [0.000, 0.250] | 0.150 [0.000, 0.350] | 0.800 [0.300, 1.300] | 0.250 [0.000, 0.750] |
| false_closes_any_checkpoint | 0.900 [0.200, 1.800] | 0.900 [0.200, 1.800] | 3.200 [2.350, 4.100] | 0.300 [0.050, 0.650] | 0.650 [0.100, 1.350] | 1.900 [0.800, 3.000] | 0.500 [0.000, 1.500] |
| wrong_target_closes_per_ticket | 0.050 [0.000, 0.150] | 0.000 [0.000, 0.000] | 0.500 [0.250, 0.700] | 0.300 [0.100, 0.500] | 0.200 [0.050, 0.400] | 0.100 [0.000, 0.300] | 0.000 [0.000, 0.000] |
| missed_close_rate | 0.139 [0.123, 0.161] | 0.146 [0.126, 0.172] | 0.076 [0.047, 0.103] | 0.076 [0.037, 0.122] | 0.076 [0.039, 0.120] | 0.075 [0.016, 0.140] | 0.150 [0.048, 0.308] |
| close_precision | 0.964 [0.927, 0.993] | 0.971 [0.941, 0.993] | 0.800 [0.769, 0.829] | 0.946 [0.914, 0.979] | 0.953 [0.917, 0.986] | 0.871 [0.812, 0.934] | 0.944 [0.857, 1.000] |
| suggestions_per_ticket | 1.600 [1.200, 2.050] | 1.650 [1.250, 2.050] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |

`false_closes_any_checkpoint` counts over every handoff and the end, per
ticket. Counted at the end of the tickets: the event memory closed 135 of 158
rightly, 4 falsely, 1 on the wrong message, missed 22, and held 32 back as
suggestions (1 right). Full context: 140, 2, 6, 12. Mem0: 142, 3, 4, 12.

**The hard cases (PR #9)**

| metric | event_memory | event_memory_no_verifier | event_memory_unguarded | full_context | mem0 | graphiti | rolling_summary |
|---|---|---|---|---|---|---|---|
| rescue_close_correct | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.850 [0.700, 1.000] | 0.900 [0.750, 1.000] | 0.600 [0.300, 0.900] | 0.500 [0.000, 1.000] |
| ambiguous_close_refrained | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.850 [0.650, 1.000] | 0.950 [0.850, 1.000] | 0.850 [0.650, 1.000] | 0.600 [0.300, 0.900] | 0.500 [0.000, 1.000] |
| wrong_thing_not_closed | 0.800 [0.600, 0.950] | 0.800 [0.600, 0.950] | 0.400 [0.200, 0.600] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |

**Facts, disputes, failed steps (every handoff and the end)**

| metric | event_memory | event_memory_no_verifier | event_memory_unguarded | full_context | mem0 | graphiti | rolling_summary |
|---|---|---|---|---|---|---|---|
| freshness_error | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.291 [0.239, 0.334] | 0.310 [0.236, 0.382] | 0.278 [0.225, 0.324] | 0.333 [0.160, 0.444] |
| freshness_error_at_handoffs | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.266 [0.210, 0.314] | 0.301 [0.223, 0.381] | 0.272 [0.216, 0.321] | 0.320 [0.111, 0.446] |
| fact_recall | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.956 [0.867, 1.000] | 0.815 [0.656, 1.000] |
| fact_change_accuracy | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.963 [0.860, 1.000] | 0.833 [0.667, 1.000] |
| dispute_recall | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.560 [0.470, 0.651] | 0.604 [0.449, 0.738] | 0.192 [0.040, 0.388] | 0.667 [0.538, 0.857] |
| dispute_precision | 0.938 [0.836, 1.000] | 0.938 [0.836, 1.000] | 0.938 [0.836, 1.000] | 1.000 [1.000, 1.000] | 0.965 [0.881, 1.000] | 0.909 [0.625, 1.000] | 0.636 [0.429, 1.000] |
| failed_step_recall | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.881 [0.625, 1.000] | 0.839 [0.595, 1.000] |
| failed_step_precision | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.792 [0.734, 0.856] | 0.771 [0.712, 0.831] | 0.771 [0.675, 0.887] | 0.963 [0.880, 1.000] |
| unanswered_checkpoints | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.104 [0.000, 0.319] | 0.368 [0.100, 0.684] |

**Cost and latency**

| metric | event_memory | event_memory_no_verifier | event_memory_unguarded | full_context | mem0 | graphiti | rolling_summary |
|---|---|---|---|---|---|---|---|
| usd_per_ticket | 0.013 [0.013, 0.014] | 0.012 [0.012, 0.013] | 0.013 [0.013, 0.014] | 0.00434 [0.00410, 0.00457] | 0.053 [0.050, 0.056] | 0.160 [0.141, 0.173] | 0.096 [0.051, 0.122] |
| calls_per_ticket | 182.7 [176.4, 188.7] | 174.1 [167.8, 180.2] | 182.7 [176.4, 188.7] | 4.400 [4.200, 4.600] | 497.1 [476.4, 516.5] | 1,250 [955.8, 1,426] | 131.2 [83.0, 173.2] |
| prompt_tokens_per_ticket | 345,851 [331,218, 360,663] | 341,849 [327,028, 356,762] | 345,851 [331,218, 360,663] | 20,817 [19,492, 22,112] | 1,662,565 [1,599,033, 1,723,329] | 1,135,235 [868,914, 1,293,798] | 251,994 [138,159, 321,354] |
| prompt_cache_share | 0.887 [0.883, 0.890] | 0.888 [0.884, 0.891] | 0.887 [0.883, 0.890] | 0.136 [0.132, 0.141] | 0.827 [0.824, 0.831] | 0.143 [0.133, 0.162] | 0.752 [0.665, 0.814] |
| ingest_seconds_per_message | 1.462 [1.336, 1.540] | 1.335 [1.220, 1.404] | 1.462 [1.336, 1.540] | 0.000 [0.000, 0.000] | 2.073 [2.012, 2.140] | 4.038 [3.231, 4.494] | 3.810 [0.895, 7.389] |
| answer_seconds_per_checkpoint | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 5.925 [5.710, 6.188] | 6.622 [6.362, 6.926] | 4.997 [3.733, 5.812] | 3.590 [1.025, 6.464] |
| model_seconds_per_ticket | 253.5 [232.3, 269.2] | 231.4 [211.8, 245.9] | 253.5 [232.3, 269.2] | 26.0 [24.7, 27.3] | 391.0 [369.3, 413.3] | 735.8 [580.6, 834.0] | 676.0 [165.9, 1,292] |

`prompt_cache_share` is the share of prompt tokens the provider served from
its prompt cache. The event memory's verifier made 172 calls, 8.6 a ticket,
for $0.019 in all: 1,741 reasoning tokens a ticket and 77% of its prompt
cached. Full context and Graphiti send prompts that change every time, so
little is cached. Model seconds include queueing: up to 12 tickets ran at
once against one host. Graphiti's are wall time; its calls run concurrently.

**Paired differences** (method − event memory; * where the interval
excludes zero)

| metric (method − event_memory) | event_memory_no_verifier | event_memory_unguarded | full_context | mem0 | graphiti | rolling_summary |
|---|---|---|---|---|---|---|
| commitment_f1 | -0.001 [-0.004, +0.000] | +0.000 [+0.000, +0.000] | +0.008 [+0.000, +0.017] * | +0.001 [-0.010, +0.011] | -0.050 [-0.168, +0.005] | -0.189 [-0.476, -0.030] * |
| deadline_accuracy | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | -0.065 [-0.093, -0.042] * | -0.091 [-0.122, -0.062] * | -0.068 [-0.120, -0.031] * | -0.085 [-0.208, +0.000] |
| status_accuracy | -0.000 [-0.001, +0.000] | -0.036 [-0.056, -0.019] * | +0.022 [+0.000, +0.047] * | +0.019 [-0.012, +0.048] | -0.009 [-0.046, +0.033] | +0.015 [-0.087, +0.087] |
| breach_alert_recall | -0.008 [-0.024, +0.000] | -0.116 [-0.145, -0.085] * | +0.023 [-0.008, +0.057] | +0.016 [-0.015, +0.047] | -0.108 [-0.313, +0.033] | -0.160 [-0.609, +0.130] |
| false_alerts_per_ticket | +0.050 [+0.000, +0.150] | -0.500 [-0.700, -0.300] * | -0.650 [-0.950, -0.300] * | -0.700 [-0.950, -0.400] * | -0.800 [-1.200, -0.300] * | -0.500 [-1.500, +0.000] |
| false_closes_per_ticket | +0.000 [+0.000, +0.000] | +1.000 [+0.750, +1.300] * | -0.100 [-0.350, +0.150] | -0.050 [-0.250, +0.200] | +0.600 [+0.100, +1.100] * | -0.250 [-0.750, +0.000] |
| false_closes_any_checkpoint | +0.000 [+0.000, +0.000] | +2.300 [+1.650, +3.050] * | -0.600 [-1.600, +0.250] | -0.250 [-1.150, +0.650] | +0.900 [-0.900, +2.500] | -2.000 [-4.000, +0.000] |
| wrong_target_closes_per_ticket | -0.050 [-0.150, +0.000] | +0.450 [+0.250, +0.700] * | +0.250 [+0.000, +0.450] | +0.150 [-0.050, +0.350] | +0.100 [+0.000, +0.300] | +0.000 [+0.000, +0.000] |
| missed_close_rate | +0.006 [+0.000, +0.020] | -0.063 [-0.093, -0.036] * | -0.063 [-0.103, -0.019] * | -0.063 [-0.108, -0.018] * | -0.085 [-0.152, -0.017] * | -0.017 [-0.232, +0.200] |
| rescue_close_correct | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | +0.850 [+0.700, +1.000] * | +0.900 [+0.750, +1.000] * | +0.600 [+0.300, +0.900] * | +0.500 [+0.000, +1.000] |
| ambiguous_close_refrained | +0.000 [+0.000, +0.000] | -0.150 [-0.350, +0.000] | -0.050 [-0.150, +0.000] | -0.150 [-0.350, +0.000] | -0.400 [-0.700, -0.100] * | -0.500 [-1.000, +0.000] |
| wrong_thing_not_closed | +0.000 [+0.000, +0.000] | -0.400 [-0.600, -0.200] * | +0.200 [+0.050, +0.400] * | +0.200 [+0.050, +0.400] * | +0.200 [+0.000, +0.500] | +0.500 [+0.000, +1.000] |
| freshness_error | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | +0.291 [+0.239, +0.334] * | +0.310 [+0.236, +0.382] * | +0.278 [+0.225, +0.324] * | +0.333 [+0.160, +0.444] * |
| dispute_recall | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | -0.440 [-0.530, -0.349] * | -0.396 [-0.551, -0.262] * | -0.808 [-0.960, -0.612] * | -0.333 [-0.462, -0.143] * |
| failed_step_precision | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | -0.208 [-0.266, -0.144] * | -0.229 [-0.288, -0.169] * | -0.229 [-0.325, -0.113] * | -0.037 [-0.120, +0.000] |
| usd_per_ticket | -0.001 [-0.001, -0.001] * | +0.000 [+0.000, +0.000] | -0.009 [-0.010, -0.009] * | +0.040 [+0.037, +0.042] * | +0.147 [+0.128, +0.159] * | +0.082 [+0.037, +0.108] * |

### Why the event memory loses the rescue and the wrong-thing cases

**Rescue.** On all 20 replies ("Ага, вижу.", "Получила, спасибо.", "Да, на
месте.") the extractor proposed nothing. What says which twin is meant is the
agent's previous message, and the extractor is shown the card and the new
message only, so it cannot know. Nothing was proposed, so neither the guards
nor the verifier were involved. Every method that sees the transcript, or a
memory written from it, gets most of these right. The failure is cheap in
one sense: both twins stay open, and the card is wrong in the safe direction,
an unneeded alert rather than a silent close. 20 of the event memory's 22
false alerts are these twins. It is still a loss, and it is the method's
design, not a bad model call.

**Wrong thing.** The extractor pinned "деньги за прошлый заказ пришли" on the
only open promise of the company, the callback, in 4 of 20 tickets. The close
guard has a rule for exactly that situation. A plain confirmation of a deed
may close a side's only open promise even when it shares no word with it. That
rule admitted all 4. The verifier said **no** to all 4, but the reducer weighs
a "no" as no evidence rather than as evidence against.

### What the verifier changed

| verdict | the message closed the target | the target was still owed | it was closed by another message |
|---|---|---|---|
| yes (147) | 136 | 8 | 3 |
| no (9) | 0 | 8 | 1 |
| unsure (16) | 0 | 10 | 6 |

- **It rescued one close, and the close was wrong.** In M0017 the ambiguous
  reply "Одно уже получила, спасибо." was pinned by the extractor on an
  unrelated open promise, "Отправим вам замену". The verifier said yes, with
  "Одно уже получила" as evidence, which is more than thanks. The yes stood in
  for the lexical check and closed a promise that was still owed. That is the
  event memory's one wrong-target close (0.05 against 0.00 without the
  verifier). It also let a later renegotiation of that promise onto the card,
  so the same ticket gained one promise and one breach alert. Nothing else
  differs between `event_memory` and `event_memory_no_verifier` at any
  checkpoint.
- **A "no" that vetoed** would have blocked 4 admitted closes, all 4 false:
  exactly the wrong-thing closes above. It would have blocked no true close.
  Round 3 found nothing for a veto to block on dev; test3 does.
- Its yes is not trustworthy alone: 11 of 147 fell on a promise that was not
  closed. The hard checks held 10 of those; the eleventh is the rescue above.
  No "no" and no "unsure" fell on a true close. No evidence quote was missing
  from its message, and no call failed.

So on these tickets the verifier as wired adds about 8% to the event
memory's cost and makes the result slightly worse. Wired as a veto, it would have removed every end-of-ticket
false close. That is a change to the reducer's rules (`closing`), not made
here. It can be measured on the frozen proposals and verdicts without a model
call; see below.

### The guards, on the same proposals

| | false closes | wrong target | missed closes | suggestions | breach alerts (right, false, missed) | status accuracy |
|---|---|---|---|---|---|---|
| guards on (`event_memory`) | 4 | 1 | 22 of 158 | 32 | 125, 22, 4 | 0.926 |
| guards off | 24 | 10 | 12 of 158 | 0 | 110, 12, 19 | 0.890 |

The guards refused 32 closes: 21 false closes, 10 closes by the wrong
message, and 1 true close (a dropped word in the copied wording, as in
round 2). Unlike round 2's test split, the extractor did propose wrong closes
here, about 1.5 a ticket, and the guards are what stopped them.

### Graphiti on 10 tickets

What its graph held at the end of each ticket, per planted dispute: whether
each side's value was still a valid fact (`report.graphiti_disputes`).

| dispute | customer's value closed, company's current | company's closed, customer's current | both current | both absent (M0005, stopped at event 6) |
|---|---|---|---|---|
| still open at the end (13) | 7 | 1 | 3 | 2 |
| settled during the ticket (4) | 1 | 1 | 2 | — |

On round 3b's two dev tickets every open dispute became a supersession. On
ten test tickets, 8 of the 11 its graph held did; the other 3 kept both
values, and its reader still reported few of them as disputes (dispute recall
0.19). The mechanism is the one round 3b described: a contradiction has one
resolution in Graphiti, the newer fact wins, and its facts do not say who
asserted them.

On its 9 complete tickets (M0005 left out; paired with the event memory on
the same 9): commitment F1 0.988 (−0.003 [−0.013, 0.006]), deadline accuracy
0.931 (−0.069 [−0.122, −0.032]), false closes 0.89 a ticket (+0.67 [0.11,
1.22]), dispute recall 0.24 (−0.76 [−0.94, −0.55]), freshness error 0.286
(+0.286), rescue 0.67, ambiguous refrained 0.67, wrong thing 1.00. Its
commitment account is as good as ours when it completes. It loses on
disputes, deadlines and false closes.

### Spent

| what | USD |
|---|---|
| Step 0 check (round 3's B and C through the single runner) | 0.0033 |
| rewriting test3 | 0.0644 |
| the first rewrite, cut by a connection reset (logged nothing; entered at the full rewrite's cost, an upper bound) | 0.0644 |
| projection sample | 0.0251 |
| first pass (from the key's usage counter) | 2.9923 |
| second and third passes (resumes) | 0.3188 |
| **session** | **3.4684** |

By method, as the record counts it (cached calls at what they cost when
made): Graphiti $1.602, Mem0 $1.060, rolling summary $0.383, event memory
$0.267 (verifier $0.019), full context $0.087.

### Threats to validity (round 4)

- **Simulated data.** Tickets are generated from templates and reworded by a
  model. Real tickets are messier, and real disputes, rescues and wrong-thing
  confirmations occur at unknown rates. Here each is planted once per ticket.
- **The same team wrote the generator and the method.** The planted cases
  were designed with the method's guards in mind, and the deadline phrases are
  the ones `temporal` parses. The 1.000 deadline accuracy partly reflects
  that, including 5 same-weekday deadlines that follow `temporal`'s convention.
- **One reading model.** Every method read with DeepSeek V4.1 Flash on one
  host. A stronger model might help the baselines more than the event memory
  (whose reading is narrow), or less.
- **The sanity check was done by a model, not a person.** Three tickets were
  read in full, and every rewritten planted message of the 20 was checked. It
  found one bug and fixed it; a person may find others. Round 2's test2 has
  the same model-dispute fault in 2 tickets, uncorrected.
- **Subsets and failures.** Graphiti ran on 10 tickets, one of which stopped
  at event 6; the rolling summary ran on 4, of which it completed 1. Their
  intervals are wide, and their numbers carry those failures. A failure that
  stops a ticket is the method's, as it would be in production. Network
  failures were resumed once for every method alike.
- **The verifier's 172 verdicts** come from one model judging closes the same
  model proposed.
- **The rewrite fix was made after reading test3**, before any method read
  it. It moved 15 messages back to their template words.

### What is frozen and how to re-run only our method

Frozen for the rest of the coursework: the `test3` tickets (seed 4343 and
`results/round4/tickets/test3.json`), the gold labels as generated now, and
every baseline's answers at every checkpoint in `results/round4/test3.json`
(full context, Mem0, Graphiti on 10 tickets, rolling summary on 4). Also
frozen: the event memory's proposals for every message, with every verifier
verdict, in the same record.

```bash
# Score everything again (a corrected or added measure): no model call.
python -m duebook.bench.experiment --split test3 --methods "" --max-usd 0 \
    --frozen results/round4/test3.json --results /tmp/r --out /tmp/r/test3.json

# A new reducer, guards or verifier rule, on the frozen proposals and verdicts:
# no model call; the baselines come from the record and are scored again.
python -m duebook.bench.experiment --split test3 --methods event_memory --max-usd 0.01 \
    --replay results/round4/test3.json --frozen results/round4/test3.json \
    --results results/round5 --out results/round5/test3.json

# A new extractor or prompt: only the event memory reads again (~$0.27 uncached).
python -m duebook.bench.experiment --split test3 --methods event_memory --max-usd 0.4 \
    --frozen results/round4/test3.json --results results/round5 --out results/round5/test3.json
```

Each command was checked on the final record. The first two reproduce every
value of round 4's summary exactly, at $0.00.


## Round 3b (dev only): further memory baselines

Mem0 was the only existing memory system compared. `docs/competitors.md`
surveys the rest and picks **Graphiti** (`graphiti-core` 0.31.2, Zep's
open-source engine): it is the one system whose facts carry validity
intervals that are closed, not deleted, when contradicted — what RQ2
measures. No second system was cheap enough to add (Hindsight is the next
candidate; see the survey). **Only dev2 was run.** Raw record:
`results/round3b/`.

### What was run

| | |
|---|---|
| Split | `dev2`, the text round 2 rewrote (`results/round2/tickets/dev2.json`) |
| Graphiti | the **first 2 tickets** (M0000, M0001; 175 and 183 events), as the budget allowed: projected at $0.59 upper for 2 tickets, $1.07 for 4 |
| Event memory | all 4 tickets, round 2's configuration (C), as the reference; the differences below are on Graphiti's 2 tickets |
| Reader | `deepseek/deepseek-v4.1-flash`, reasoning off, locked to DeepInfra (`PRESETS`), for every call Graphiti makes and for the checkpoint answer |
| Embeddings | `openai/text-embedding-3-small` through OpenRouter, as for Mem0 |
| Graph database | FalkorDB Lite (`falkordblite` 0.11.0), embedded, one per ticket; no Docker |
| Feeding | every message and reassignment is an episode, with the event's own time as `reference_time`; the episode text is the same numbered, time-stamped line the other baselines see |
| Checkpoint | the ticket's whole graph — every fact with its source messages and validity window, every entity summary — and the shared question; no search |
| Charged | $0.375: $0.0224 for the projection sample, $0.3500 for the run, $0.0027 for a 4-event smoke test before it; a replay from the response cache cost $0 and reproduced every answer |

`results/round3b/dev2.json` is the run. The run as made did not keep the
text Graphiti's graph showed the model; a replay from the response cache,
identical in every answer and score, did. Of that replay only Graphiti's
graph at the end of each ticket is kept, in the last checkpoint of its runs
(`listing`): it holds every fact with its validity window, closed ones
included, which is what the dispute finding below rests on.

### Dev comparison (Graphiti's 2 tickets)

At the end of each ticket unless marked; intervals are 95% bootstrap over 2
tickets, so they are narrow only because they cannot be wide.

| | **event memory** (C) | **Graphiti** | difference |
|---|---|---|---|
| commitment F1 | 1.000 | 1.000 | 0 |
| deadline accuracy | 1.000 (28/28) | 0.929 (26/28) | −0.071 |
| status accuracy / breach-alert recall | 1.000 / 1.000 | 1.000 / 1.000 | 0 |
| **false closes** (end / any checkpoint) | 0 / 0 | 0 / 0 | 0 |
| missed closes (all checkpoints) | 0 | 2 of 36 (at handoffs) | |
| **freshness error** (all checkpoints) | **0.000** | **0.389** (14 contested of 36) | +0.389 |
| fact recall / fact changes right | 1.000 / 1.000 | 1.000 / 1.000 | 0 |
| **dispute recall** (all checkpoints) | **1.000** | **0.071** (1 of 14) | −0.929 |
| failed-step precision (all checkpoints) | 0.833 (15 of 18) | 0.682 (15 of 22) | −0.152 |
| model calls per ticket | 174.5 | 1,392.5 | ×8.0 |
| prompt tokens per ticket | 313k | 1,238k | ×4.0 |
| prompt tokens served from cache | 89.7% | 13.4% | |
| **USD per ticket** | **0.0110** | **0.165** | ×15 |
| model seconds per ticket | 238 | 797 (wall time; Graphiti's calls run concurrently) | ×3.3 |

Event memory on all 4 dev2 tickets: F1 0.991, deadlines 1.000, 0 false
closes, freshness error 0.000, dispute recall 1.000, $0.011 a ticket — the
same as round 3's C.

**What happened.** Graphiti found every promise and closed none falsely; on
these two tickets its commitment account is as good as ours. It loses on
exactly what RQ2 is about. Each planted dispute — the customer says one
value, the company another — became a supersession:

    - (from #16) Анна сообщила, что с её карты списали 9700 рублей.
      [valid from Tue 2027-01-12 12:44; no longer true from Tue 2027-01-12 14:53]
    - (from #17) По данным Ивана, со счёта Анны списано 6300 рублей.
      [valid from Tue 2027-01-12 14:53]

Graphiti's model judged the company's figure to contradict the customer's
and closed the customer's interval, so the reader, shown the graph, reports
6300 as the current amount and no dispute. The same happened to the product
model (VC-519 against VC-722). That is a design consequence, not a bad
call: Graphiti has one way to resolve a contradiction — the newer fact wins
— and no notion of who said what. The two deadlines it got wrong were
relative times a model resolved itself: "через 3 часа" was kept at the time
it was said, and "до конца недели", said on a Saturday, became that Sunday
where the working calendar gives the end of the next working week (Friday
the 29th).

It also costs 15 times as much as the event memory and 3 times Mem0's
round-2 test cost ($0.052): about 8 calls per message, chat and embedding
together (extract entities, resolve them, extract facts, resolve and
invalidate facts, summarise entities, embed names and facts), with prompts that change on every call, so the
provider's prompt cache serves 13%.

### Projected cost on `test3`

20 `main`-profile tickets, not generated or run. Per ticket: $0.165 measured
on dev2; the upper bound adds 30% for a fresh seed's longer tickets and
retries, as round 3's projection does.

| what | expected, 20 tickets | upper |
|---|---|---|
| **Graphiti, all 20 tickets** | **$3.31** | **$4.30** |
| Graphiti on the first 4 (round 2's subset shape) | $0.66 | $0.86 |

Added to round 3's projection: every method on all 20 tickets about $7.7
(upper $9.9); with rolling summary, Mem0 and Graphiti on the first 4, about
$1.9 (upper $2.5). The cost projection in the runner now uses a growth factor
of 1.2 for Graphiti (measured 1.1).

### Threats to validity (round 3b)

- **Two tickets.** Graphiti ran on half of dev2. The intervals mean little;
  the dispute result rests on its mechanism (shown above, on every planted
  dispute but one), not on the count.
- **Our reading of Graphiti.** The checkpoint shows the whole graph, the
  way Zep's context block shows facts with date ranges and entity
  summaries; Graphiti's search was not used, which can only help its recall.
  Zep Cloud adds its own retrieval and was not run.
- **One model.** Graphiti is documented to work best with models that honour
  structured output; DeepSeek V4.1 Flash on DeepInfra does, and no call
  failed, but a stronger model might judge contradictions differently. It
  cannot tell a dispute from a change without being told who said what,
  which its fact model does not carry.
- **Dev only, no tuning.** The instructions given to Graphiti
  (`custom_extraction_instructions`) were written once, before the run, and
  not changed after seeing results.

### Reproducing round 3b

```bash
pip install -e ".[dev,mem0,graphiti]"     # Python 3.12+: FalkorDB Lite is embedded, no server
python -m duebook.bench.experiment --split dev2 --methods event_memory,graphiti \
    --subset graphiti=2 --results results/round3b --out results/round3b/dev2.json \
    --session-cap 1.0 --max-usd 0.95
```

`falkordblite` ships its own `redis-server` and FalkorDB module and starts
one per ticket in a temporary directory; nothing else needs installing.
Answers and embeddings are cached under `.cache/responses/`, so the run
replays without a model call on the machine that made it. Round 3b's event
memory had no verifier; `event_memory` now has it, and round 3b's reference is
reported beside it as `event_memory_no_verifier`. The tickets are read from
`results/round2/tickets/`, where the split's round keeps them.

## Round 3 (dev only): verifier and reasoning

Round 2 left one question open: its guards prevented 5 false closes on dev
and none on test, where the model made no wrong close for them to catch. And
they cost something. A lexical check cannot tell "Спасибо, всё получила" from
bare thanks when two promises are open. Round 3 asks whether either of two
ways of spending more on the risky step pays off: a stronger *extractor*
(reasoning on), or a *verifier* that is asked only about proposals that would
close, cancel or replace a promise. It also locks the reader to one provider.
**Only the dev split was used.** No test split was run. Raw records:
`results/round3/`.

### Short version

- **Chosen: C, round 2's configuration** (extractor reasoning off, the
  deterministic guards, no verifier). On dev2 all three configurations made
  **0 false closes** and 0 wrong-target closes. C and B tie on every accuracy
  measure (commitment F1 0.981, deadlines 1.000). A is worse (F1 0.972,
  deadlines 0.943). C is the cheapest, at $0.011 a ticket. By the rule
  (fewest false closes, then F1, then cost), C wins. B costs 7% more and
  bought nothing measurable on these tickets.
- **The verifier is accurate but had nothing to do.** It made 31 calls on dev2
  (7.75 per ticket, $0.0008 per ticket) and said yes to all 31, every one a
  true close. Over the proposals of round 2's earlier dev runs, which still
  had the twin trap, it made 119 calls. It said yes 114 times, all true, and
  no 5 times, all false. Those 5 are exactly the 5 false closes round 2's
  guards refused, so two independent mechanisms agree case by case. It
  rescued no close: none of these tickets has a true close that the lexical
  check refuses and the hard checks allow. The case it exists for ("всё
  получила" with a second promise open) does not occur in dev2, and round 2's
  one blocked true close on test failed the *hard* span check, which no
  verdict may override.
- **Reasoning on the extractor costs more and reads worse.** Reasoning low
  spent 196 hidden tokens a message and 2.3× C's cost ($0.025 a ticket). It
  added a spurious promise. Three times it turned "до среды" (by Wednesday)
  into Saturday. The rule parser disagreed, so those deadlines were marked low
  confidence and raised no alert, but they are wrong on the card. It also
  proposed one false close, which the guards refused.
- **Spent:** $0.107 of the $1.00 allowed for the session.
- **Projected final run** on a fresh seed (`test3`, 20 main-profile tickets,
  not run): event memory (C) about $0.23 (upper $0.30). The full set of
  baselines on all 20 tickets is about $4.5 (upper $5.6). With round 2's
  subsets it is about $1.2 (upper $1.6). See
  [the projection](#projected-cost-of-the-final-run).

### What changed

1. **The reader is locked to one host.** DeepSeek V4.1 Flash is sent with
   `provider: {"order": ["deepinfra/fp8"], "allow_fallbacks": false,
   "require_parameters": true}`. Round 2 allowed fallbacks; all its calls
   happened to be served by DeepInfra. Per-model settings live in a small
   presets table, `extract.client.PRESETS` (model → hosts, reasoning). The
   GLM writer is locked to `gmicloud/fp8, tenstorrent/fp8, novita/fp8`.
   Reasoning is named: off = `{"enabled": false}`, low = `{"effort": "low",
   "exclude": true}`. The extractor and the verifier choose their reasoning
   separately.
2. **Every call records** the host that answered, the prompt tokens served
   from cache and the hidden reasoning tokens. **Retries:** one after a 429
   or 5xx, one after an empty or unreadable answer, none after a timeout or a
   connection broken mid-response, because it may already have been
   generated and billed.
3. **A verifier of destructive changes** (`extract.Verifier`,
   `VerifyingReader`). It runs between extraction and the reducer, only on a
   proposal that would resolve, cancel or replace an open commitment. It sees
   that commitment (owner, action, original words, deadline) and the new
   message with its author, and answers yes, no or unsure, with
   `evidence_quote` copied from the message. The verdict travels on the
   proposal and is stored with it, so replay needs no model and the reducer
   stays pure. The reducer layers it:
   - **Hard, never overridden:** who may close, the span of the target's
     wording (`target_quote`), ambiguity (the closing words *and the
     evidence* fit another open promise of that side as well), and a
     renegotiation that names a different thing.
   - **Soft:** lexical relevance. It is passed by shared words, or by a yes
     whose evidence is in the message word for word and is more than
     thanks, acknowledgement or waiting.
   - **Anything else** (no, unsure, a quote not in the message, a verifier
     that failed) leaves the close to the deterministic checks, and a refused
     close stays a suggestion.

   Two choices go beyond the brief, both in the fail-safe direction. The
   renegotiation check ("names another thing") is treated as hard, because
   it is evidence against a close, not a lack of evidence for one. And a yes
   that quotes only thanks or "жду" never counts, so bare thanks still closes
   nothing, whatever the verifier says.
4. **Fact recall** joins the scorer: of the planted values current at a
   checkpoint, the share a method presents.

### What was run

| | |
|---|---|
| Split | `dev2` (seed 4141, 4 tickets of 168–186 messages), the text round 2 rewrote |
| A | extractor `deepseek/deepseek-v4.1-flash`, reasoning low (`exclude`), guards on, no verifier. New calls: 703 extraction calls, locked to DeepInfra |
| B | round 2's frozen dev proposals (`results/round2/dev2-4.json`, reasoning off) replayed through today's reducer; the verifier (`deepseek/deepseek-v4.1-flash`, reasoning low, locked to DeepInfra) asked as the fold reaches each destructive proposal |
| C | the same proposals replayed, no verifier: round 2's configuration |
| Record check | all 31 recorded destructive proposals carry `target_quote`, so nothing was re-extracted for B |
| Verifier stress | B's verifier also run over the proposals of round 2's dev runs 1–3 (`results/round3/stress-dev2-*.json`), made with earlier instructions that still fell for the twin trap |
| Response cache | every temperature-0 answer kept on disk; re-runs reuse it |
| Charged | $0.107 in total by `results/round3/spend.jsonl`: a $0.0004 probe, $0.0022 for A's projection sample, $0.0032 for B's verifier, $0.098 for A, $0.0032 for the stress runs |

A's ticket M0001 stopped at event 39 on its first pass. The connection was
reset while the answer was being read, and under the new policy that is not
retried. In production the pipeline would hold the ticket and retry later, so
the run was resumed: the 38 answered events came from the response cache and
only the rest was charged. The first pass is kept as
`results/round3/dev2-attempt1.json`.

### Dev comparison (4 tickets)

At the end of each ticket. Intervals are 95% bootstrap over tickets, so with
4 tickets they are wide.

| | **C** off + guards | **B** off + guards + verifier | **A** low + guards |
|---|---|---|---|
| **false closes** | **0** | **0** | **0** |
| false closes at any checkpoint | 0 | 0 | 0 |
| wrong-target closes | 0 | 0 | 0 |
| missed closes | 0 of 31 | 0 of 31 | 0 of 30 |
| suggestions (right / other) | 0 / 0 | 0 / 0 | 0 / 1 (a false close the guards refused) |
| **commitment F1** | **0.981** [0.962, 1.000] | **0.981** [0.962, 1.000] | 0.972 [0.963, 0.991] |
| commitment precision / recall | 0.981 / 0.981 | 0.981 / 0.981 | 0.964 / 0.981 |
| deadline accuracy | 1.000 (53/53) | 1.000 (53/53) | 0.943 (50/53) |
| status accuracy | 1.000 | 1.000 | 1.000 |
| breach-alert recall / false alerts | 0.933 / 0 | 0.933 / 0 | 0.933 / 0 |
| fact recall (all checkpoints) | 1.000 (47/47) | 1.000 | 1.000 |
| freshness error / dispute recall | 0.000 / 1.000 | 0.000 / 1.000 | 0.000 / 1.000 |
| verifier calls per ticket | — | 7.75 | — |
| model calls per ticket | 175.8 | 183.5 | 175.8 |
| reasoning tokens per ticket | 0 | 1,299 (verifier) | 34,529 |
| **USD per ticket** | **0.0109** | 0.0117 | 0.0246 |
| USD per message | 0.000062 | 0.000066 | 0.000140 |
| prompt cache hit rate (tokens served from cache) | 89.9% | 89.7% (verifier alone 68.1%) | 91.0% |
| hosts that answered | DeepInfra (all) | DeepInfra (all) | DeepInfra (all) |

C's and B's extraction cost is what round 2 was charged for those calls. B
adds $0.0032 for 31 verifier calls ($0.0001 each, about 170 reasoning
tokens a call).

**The choice.** The rule ranks fewest false closes first, then the highest
commitment F1, then the lowest cost. All three tie at 0 false closes. On F1,
C and B tie at 0.981 and A is lower. C is cheaper than B and no worse on
false closes or F1, so **C is chosen**. The evidence against A is its F1, its
three wrong weekdays, and its cost. The evidence against B is cost alone: it
was right every time but changed no outcome.

### What the verifier did

| run of proposals | calls | yes (true / false) | no (true / false) | unsure | rescued a close | a veto would have blocked |
|---|---|---|---|---|---|---|
| dev2 run 4 (frozen; = B) | 31 | 31 / 0 | 0 / 0 | 0 | 0 | 0 |
| dev2 run 1 (stress) | 26 | 24 / 0 | 0 / 2 | 0 | 0 | 0 |
| dev2 run 2 (stress) | 29 | 29 / 0 | 0 / 0 | 0 | 0 | 0 |
| dev2 run 3 (stress) | 33 | 30 / 0 | 0 / 3 | 0 | 0 | 0 |
| **all** | **119** | **114 / 0** | **0 / 5** | 0 | 0 | 0 |

"True" means the message is the one that closed the target. "False" means the
target was still owed. Every "no" fell on a false close that the guards also
refused, for example "Я перезвоню вам насчёт рассрочки до конца недели"
proposed as a new date for "...насчёт оплаты". No quote was missing from the
message, and no call failed. "A veto would have blocked" counts the closes
the words admitted on which the verifier said no. There were none, so a
"no"-as-veto rule would have changed nothing here either.

So on this evidence the verifier and the lexical guards agree everywhere they
overlap. What it would add is the soft rescue, and these tickets give it
nothing to rescue. Whether it earns its 7% depends on how often real tickets
confirm a deed in words that share nothing with the promise while another
promise is open. The simulated tickets plant almost no such confirmations,
and real tickets are needed to answer it.

### Cache-friendly prompt order

The extractor's prompt already has the order the owner's practice asks for.
The fixed instructions come first, as the system message. Then comes the
card, with each list in the order it was written (oldest first, never
re-sorted). Last comes the per-turn part: the message, its author and its
time. Measured on the dev tickets, 89.9% of prompt tokens were served from
DeepInfra's cache (91.0% for A). About 180 tokens a call are not cached:
roughly the new message and the end of the card that changed. The one
per-turn value inside the card is each open promise's status (open, due
soon, breached), which changes a few times per promise. It was left in
place: moving it would change the extractor's input, which would invalidate
the replay of round 2's proposals that B and C rest on, for at most a few
percent of cache. The verifier's prompt is fixed instructions first, then
the commitment and the message; 68% of its tokens were cached.

### Projected cost of the final run

This projection used the original `main` profile. The current `test3` split
uses `main2`, which keeps the same length range and adds three close cases
per ticket.

On a fresh seed, `test3` (seed 4343, 20 `main`-profile tickets), defined in
`SPLITS` but **not generated, read or run**. Per-ticket costs are round 2's
test2 measurements on the same profile, model and host. The upper bound adds
about 25–30% for a fresh seed's longer tickets and for retries. **The owner
approves this budget before anyone runs it.**

| what | basis (USD per ticket) | expected, 20 tickets | upper |
|---|---|---|---|
| rewriting the tickets (GLM writer, now locked to 3 hosts) | test2: $0.094 for 20 | $0.09 | $0.15 |
| **event memory, configuration C** | test2: 0.0115; dev2: 0.0109 | **$0.23** | **$0.30** |
| + B as a replay of C's proposals (verifier only) | dev2: 0.0008 | $0.02 | $0.03 |
| full context (asked at checkpoints) | test2: 0.0040 | $0.08 | $0.10 |
| Mem0 | test2: 0.052 | $1.03 | $1.30 |
| rolling summary | test2: 0.147, extrapolated from 3 tickets cut at 90–100% of their events | $2.94 | $3.70 |
| projection samples (20 events per method, mostly reused) | — | $0.01 | $0.02 |
| **every method on all 20 tickets** | | **≈ $4.4** | **≈ $5.6** |
| round 2's shape: rolling summary and Mem0 on the first 4 | | ≈ $1.2 | ≈ $1.6 |

### Reproducing round 3

Round 3 was run by its own module, since folded into the experiment runner:
`event_memory` is now B's configuration (reasoning off, guards, verifier),
`event_memory_no_verifier` is C (the same proposals, verdicts ignored), and
`event_memory_reasoning` is A.

```bash
# Every number above, scored again from the saved record: no model call.
python -m duebook.bench.experiment --split dev2 --methods "" --max-usd 0 \
    --frozen results/round3/dev2.json --results /tmp/r3 --out /tmp/r3/dev2.json
# B and C from round 2's frozen proposals: 31 verifier calls ($0.003).
python -m duebook.bench.experiment --split dev2 --methods event_memory --max-usd 0.02 \
    --replay results/round2/dev2-4.json --results /tmp/r3 --out /tmp/r3/bc.json
# The stress runs: the same with --replay results/round2/dev2-{1,2,3}.json.
# A, read again ($0.10 uncached).
python -m duebook.bench.experiment --split dev2 --methods event_memory_reasoning \
    --max-usd 0.15 --results /tmp/r3 --out /tmp/r3/a.json
```

The first command reproduces every value of round 3's summary exactly; the
second gave B $0.0117 and C $0.0109 a ticket and 31 "yes" verdicts, all true,
when it was checked on 2026-11-17.

### Threats to validity (round 3)

- **Four tickets.** The intervals are wide. A's F1 interval overlaps C's, so
  "A reads worse" rests on the point estimates and on the three wrong
  weekdays, which are a concrete failure.
- **B and C are replays.** Their extraction is round 2's recorded output,
  which was tuned on these same dev tickets. A was read fresh with
  instructions tuned for reasoning off. That favours C and B over A.
- **B's replay approximation.** In a live B run, a close the verifier admits
  would change the card the extractor sees next. Here the verifier admitted
  nothing the guards did not, so B's cards are identical to C's and the
  approximation did not matter.
- **The verifier was tested on the authors' own traps.** Its 5 correct "no"
  verdicts fell on the twin trap that round 2 found on dev. The adversarial
  suite checks what the reducer does with each kind of verdict, not how
  often a real model gives it.
- **One model, one host.** Both the extractor and the verifier are DeepSeek
  V4.1 Flash on DeepInfra. "A cheap extractor plus a stronger verifier" was
  tested only as "the same model, thinking a little"; a different verifier
  model was not tried.


## Round 2: long tickets, DeepSeek V4.1 Flash, and closes that fail safe

Round 1 found the method's worst flaw: the extractor sometimes attached
"done", "cancelled" or "replaced" to the wrong promise and the reducer
admitted it, silently retiring promises that were then broken. Round 2 adds
close guards to the reducer, reads with DeepSeek V4.1 Flash instead of GLM 5.3
Flash, and makes long tickets (150–200 messages) the main test. Raw records:
`results/round2/`.

### Short version

On the 20 test tickets (150–191 messages, 3–4 handoffs, 10–19 days each):

- **No false close.** The event memory closed no promise that was still owed:
  0 at the end of any ticket, and 0 at any of the 87 handoff and end
  checkpoints, out of 156 closes it should have made (it made 153; one more
  was held back as a suggestion, correctly). Round 1's event memory, re-scored
  with the same measure, made 6 false closes at the end of its 20 shorter
  tickets (15 across checkpoints). Breach-alert recall is 1.000 (round 1:
  0.812).
- **But the guards did not cause that on the test split.** Folding the same
  recorded proposals again with the guards off also gives 0 false closes:
  DeepSeek with the revised instructions made none of the wrong closes the
  guards exist to stop. On test the guards refused one close, a true one (the
  model dropped a word when copying the promise's wording), which cost one
  false alert; the promise stayed open with a correct "possibly done" note.
  On the dev split, where the instructions were still being tuned, the final
  guards stopped 5 false closes and blocked no true one. The adversarial
  suite (`tests/test_guards.py`) shows the cases they exist for.
- **Promises and deadlines.** Commitment F1 0.993 against 0.998 for full
  context (indistinguishable); every deadline right (287/287) against 0.885
  for full context and 0.914 for Mem0 (paired intervals exclude zero).
- **Current context.** Freshness error 0.083 against 0.291 for full context
  and 0.318 for Mem0; dispute recall 0.928 against 0.506 and 0.619. On long
  tickets the baselines' context degrades much more than the card's.
- **Cost.** $0.012 per ticket ($0.00007 per message) for the event memory,
  $0.004 for full context asked only at checkpoints, $0.052 for Mem0 and
  $0.14 for the rolling summary. Rolling summary and Mem0 ran on the first 4
  tickets only, for budget; 3 of the rolling summary's 4 tickets ran into the
  spending cap in their last 15% of events and could not answer their final
  checkpoint.

The whole round — probes, the writer trial, rewriting, four dev runs and the
test run — was charged $1.40 of the $1.50 allowed.

### What changed since round 1

Three things changed at once, and the comparison with round 1 has to keep
them apart:

1. **Closing fails safe** (`duebook.closing`, `reducer.Guards`). Round 1's
   worst flaw was the reducer admitting "done", "cancelled" or "replaced"
   attached to the wrong promise: it checked *who* may close a promise, never
   *whether the message was about it*. Now a close names its target twice —
   the handle and `target_quote`, a span copied from the target's original
   wording, shown on the card — and is admitted only if the two agree, the
   closing words share content with the target (or plainly confirm a deed
   when the target is the only open promise of its side), no other open
   promise of that side fits as well, and a renegotiation names the same
   thing as the promise it replaces. A refused close stays on the promise as
   a suggestion; the promise stays open and alerting. Bare thanks closes
   nothing, in the extractor's instructions and in the baselines' question.
2. **The reader is DeepSeek V4.1 Flash** with reasoning off and its routing
   pinned to one host, for every method, instead of GLM 5.3 Flash.
3. **The main test is long tickets** (the `main` profile: 150–200 messages,
   3–4 handoffs, about two weeks, mostly filler) that also plant what makes a
   close easy to misplace: near-identical promises open at once, one kept and
   one broken; a renegotiation; a cancellation. Round 1's tickets planted
   none of these.

Because the guards live in the reducer, their effect can be measured exactly:
the event memory's recorded proposals are folded again with the guards off
(`event_memory_unguarded`) — same model output, same tickets, no model call.
The model and the ticket length cannot be separated as cleanly; see
[Comparison with round 1](#comparison-with-round-1).

### What was run

| | |
|---|---|
| Extracting model, every method | `deepseek/deepseek-v4.1-flash` via OpenRouter, reasoning off, routing pinned to DeepInfra's fp8 host (`order: ["deepinfra/fp8"]`, fallbacks allowed), temperature 0; all 3,446 event-memory calls were served by DeepInfra (the baselines' hosts were not recorded) |
| Writer (rewrites ticket text) | `~z-ai/glm-flash-latest` (served as `z-ai/glm-5.3-flash`), reasoning off, temperature 0.8 |
| Mem0 | `mem0ai==2.2.1`, LLM as above with the same reasoning and routing, embedder `openai/text-embedding-3-small`, local Qdrant per ticket |
| Test split | `test2`: seed 4242 (new), 20 tickets, 150–191 messages (mean 172.3), 13 with 3 handoffs and 7 with 4, 10–19 days, 287 planted promises (123 kept, 91 broken, 40 still open, 20 renegotiated, 13 cancelled); run once |
| Dev split | `dev2`: seed 4141, 4 tickets of 168–186 messages; four runs of the event memory only |
| Subsets | event memory and full context on all 20 test tickets; rolling summary and Mem0 on the first 4, for budget (projected $0.34 and $0.22 for those 4) |
| Checkpoints | every handoff and the end of the ticket |
| Intervals | 95% percentile bootstrap over tickets (2,000 resamples); differences paired on the same resamples, over the tickets both methods ran on |
| Response cache | answers to temperature-0 requests kept on disk, keyed by the whole request; nothing was re-run on test |
| Total charged | $1.399 by the run logs (`results/round2/spend.jsonl`), $1.390 by the OpenRouter key's counter; cap $1.50 |

**Tickets.** The `main` profile keeps everything round 1 planted — kept,
broken, vague, pending and customer promises, a long-deadline promise made
early, plan and address changes, amount and model disputes, a failed fix
repeated after a handoff — and adds the three things that make a close easy
to misplace: one or two pairs of near-identical promises made within three
hours of each other ("we'll refund the delivery" / "we'll refund the case";
"I'll call about the payment" / "...about the instalments"; "we'll send the
warranty card" / "...the contract"), one kept and one broken, with the
confirmation of one arriving while the other is still owed; a promise
renegotiated to a later date before it fell due; and, in about half the
tickets, a promise the customer calls off. About 80% of messages are filler.

**What changed in the methods' instructions.** The extractor is shown each
open promise with the words it was made in, asked for `target_quote` on every
close, for the whole promise clause as a commitment's quote, and told that
bare thanks confirms nothing and that a similar-sounding promise about
something else is new, not a renegotiation. The baselines' shared question got
the same bare-thanks rule ("a confirmation closes only the promise it names")
and now reports the message that closed each promise (`closed_seq`), so the
wrong-target measure applies to every method.

**New measures.** For every method, at every checkpoint, over the promises it
found: a *false close* (it holds the promise as done, claimed, cancelled or
replaced while the label says it is still owed — still open, or broken), a
*wrong-target close* (closed, and so is the label, but by a different
message), a *missed close* (still open although the label is closed), and the
event memory's *suggestions* (closes held back; right when the message is the
one that closed the promise). *Status accuracy* now distinguishes owed, done
and dropped (cancelled or replaced): round 1 judged done / not done, so a
promise wrongly called cancelled counted as right there.

### Choosing the writer

The tickets must be rewritten by a model other than the one that reads them,
so with DeepSeek reading, DeepSeek could no longer write. Seven cheap models
with strict structured output were tried on the same 60 dev messages (40 of
them planted, so harder than an average ticket), at temperature 0.8:

| writer | discarded (lost a span, not one message) | returned unchanged | reworded but added content | reworded and kept | USD for 60 |
|---|---|---|---|---|---|
| mistralai/mistral-nemo | 6 | 46 | 3 | 5 | 0.00025 |
| mistralai/mistral-small-24b-instruct-2501 | 16 | 5 | 13 | 26 | 0.00061 |
| mistralai/mistral-small-3.2-24b-instruct | 19 | 1 | 17 | 23 | 0.00114 |
| google/gemma-3-12b-it | 5 | 8 | 12 | 35 | 0.00181 |
| google/gemma-3-27b-it | 46 | 0 | 9 | 5 | 0.00159 |
| qwen/qwen3-30b-a3b-instruct-2507 | 5 | 31 | 5 | 19 | 0.00134 |
| **~z-ai/glm-flash-latest**, reasoning off | 14¹ | 13 | 0¹ | **33** | 0.00172 |

¹ GLM was tried last, after the stricter check below existed, so its 14
discards include rewrites that added content; the other rows were judged
against that check afterwards, from the saved outputs.

The discard rate alone misled: Mistral Nemo "kept every span" by returning
the text unchanged. And the cheap writers that did reword added things a
label would have to account for — "мы разберёмся" after a status ping, "так
и не привезли" after a thank-you, a complaint about bonuses after a
confirmation — so a rewrite is now also discarded when it has more numbers,
questions, negations or promise-like verbs than the original
(`bench.rewrite.acceptable`). Judged that way, GLM Flash (the alias is served
as `z-ai/glm-5.3-flash`) reworded the most and added nothing; Gemma 3 12B,
the next, wrote stray markup ("}", "(none)") into the text, and Mistral Small
drifted in meaning and in the speaker's gender. With reasoning on, GLM cost
six times as much ($0.0107 for the 60) for the same work. The cheapest
writer that rewrites reliably is therefore GLM Flash with reasoning off, at
about $0.03 per thousand messages. On the full splits it rewrote dev2 and
test2 once, for $0.115; 21% of test messages (725 of 3,446) were discarded
and a further 6% came back unchanged, so 27% keep their template wording
(round 1: 5.6%, under a looser check).

### Test split (20 tickets)

Closes, at the end of each ticket:

| metric | event_memory | full_context | rolling_summary (4) | mem0 (4) | event_memory, guards off (replay) |
|---|---|---|---|---|---|
| **false closes per ticket** | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| false closes per ticket, any checkpoint | 0.000 [0.000, 0.000] | 0.050 [0.000, 0.150] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| wrong-target closes per ticket | 0.000 [0.000, 0.000] | 0.050 [0.000, 0.150] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| missed-close rate | 0.019 [0.000, 0.039] | 0.051 [0.020, 0.086] | 0.125 [0.000, 0.125] | 0.031 [0.000, 0.094] | 0.013 [0.000, 0.032] |
| close precision | 1.000 [1.000, 1.000] | 0.993 [0.978, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| suggestions per ticket | 0.050 [0.000, 0.150] | — | — | — | — |

The same, counted:

| method | tickets | closes right | **false** | wrong target | missed | suggestions (right / other) |
|---|---|---|---|---|---|---|
| event_memory | 20 | 153 | **0** | 0 | 3 of 156 | 1 / 0 |
| full_context | 20 | 147 | 0 (1 at a handoff) | 1 | 8 of 156 | — |
| rolling_summary | 4 | 7 | 0 | 0 | 1 of 8 | — |
| mem0 | 4 | 31 | 0 | 0 | 1 of 32 | — |
| event_memory, guards off | 20 | 154 | 0 | 0 | 2 of 156 | — |

(The rolling summary's 8 are the closes of the one ticket whose end it
reached; see below.)

Accuracy at the end of each ticket:

| metric | event_memory | full_context | rolling_summary (4) | mem0 (4) |
|---|---|---|---|---|
| commitment precision | 0.986 [0.973, 0.997] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.983 [0.954, 1.000] |
| commitment recall | 1.000 [1.000, 1.000] | 0.997 [0.989, 1.000] | 0.217 [0.000, 0.672] | 0.967 [0.897, 1.000] |
| **commitment F1** | 0.993 [0.986, 0.998] | 0.998 [0.995, 1.000] | 0.356 [0.000, 0.804] | 0.975 [0.943, 1.000] |
| **deadline accuracy** | 1.000 [1.000, 1.000] | 0.885 [0.834, 0.930] | 1.000 [1.000, 1.000] | 0.914 [0.828, 0.981] |
| status accuracy | 0.990 [0.979, 1.000] | 0.969 [0.945, 0.986] | 0.923 [0.923, 1.000] | 0.983 [0.952, 1.000] |
| breach-alert precision | 0.968 [0.937, 1.000] | 0.928 [0.877, 0.970] | 0.800 [0.800, 1.000] | 0.950 [0.880, 1.000] |
| **breach-alert recall** | 1.000 [1.000, 1.000] | 0.989 [0.966, 1.000] | 0.211 [0.000, 0.706] | 1.000 [1.000, 1.000] |
| false alerts per ticket | 0.150 [0.000, 0.300] | 0.350 [0.150, 0.650] | 0.250 [0.000, 0.750] | 0.250 [0.000, 0.750] |

Context, over every handoff and the end (87 checkpoints; 16 for the subsets):

| metric | event_memory | full_context | rolling_summary (4) | mem0 (4) |
|---|---|---|---|---|
| **freshness error** (lower is better) | 0.083 [0.023, 0.149] | 0.291 [0.233, 0.340] | 0.159 [0.000, 0.360] | 0.318 [0.173, 0.400] |
| freshness error, handoffs only | 0.079 [0.020, 0.141] | 0.281 [0.218, 0.336] | 0.175 [0.000, 0.360] | 0.319 [0.132, 0.411] |
| fact change right | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.722 [0.400, 0.920] | 1.000 [1.000, 1.000] |
| dispute precision | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] |
| dispute recall | 0.928 [0.835, 1.000] | 0.506 [0.389, 0.618] | 0.619 [0.433, 0.889] | 0.619 [0.143, 0.857] |
| failed-step precision | 1.000 [1.000, 1.000] | 0.720 [0.665, 0.780] | 1.000 [1.000, 1.000] | 0.636 [0.600, 0.727] |
| failed-step recall | 0.981 [0.940, 1.000] | 1.000 [1.000, 1.000] | 0.750 [0.600, 0.938] | 1.000 [1.000, 1.000] |
| unanswered checkpoints | 0.000 | 0.000 | 0.188 (3 of 16) | 0.000 |

Paired differences, method minus event memory, over the tickets both ran on
(`*`: the interval excludes zero):

| metric | full_context (20) | rolling_summary (4) | mem0 (4) |
|---|---|---|---|
| commitment F1 | +0.005 [−0.002, +0.012] | −0.644 [−1.000, −0.196] * | −0.025 [−0.057, +0.000] |
| deadline accuracy | −0.115 [−0.166, −0.070] * | +0.000 | −0.086 [−0.172, −0.019] * |
| breach-alert recall | −0.011 [−0.034, +0.000] | −0.789 [−1.000, −0.294] * | +0.000 |
| false alerts per ticket | +0.200 [+0.000, +0.450] | +0.000 [−0.750, +0.750] | +0.000 [−0.750, +0.750] |
| freshness error | +0.207 [+0.143, +0.273] * | +0.077 [−0.177, +0.360] | +0.237 [+0.083, +0.400] * |
| fact change right | +0.000 | −0.278 [−0.600, −0.080] * | +0.000 |
| dispute recall | −0.422 [−0.582, −0.271] * | −0.381 [−0.567, −0.111] * | −0.381 [−0.857, −0.143] * |
| failed-step recall | +0.019 [+0.000, +0.060] | −0.250 [−0.400, −0.062] * | +0.000 |
| USD per ticket | −0.008 * | +0.128 * | +0.039 * |

Deadlines of the promises each method found, at the end: the event memory
287 of 287 right; full context 253 of 286, with 17 errors that are the "end
of week" convention (Sunday instead of Friday) and 16 that are arithmetic,
mostly business-day counting; Mem0 53 of 58 (2 convention, 3 arithmetic).

**The rolling summary was cut short.** Its cost was projected from the first
20 events of a ticket at $0.34 for its 4 tickets; it cost more than $0.56,
because its summaries grew to 963–1,883 words — it ignored its 500-word bound
— and it rewrote the whole summary after each of ~185 events (304,000
completion tokens a ticket). Three of its four tickets reached the run's
spending cap at events 159, 180 and 195 of 177, 193 and 195, so their last
checkpoint is unanswered and their end-of-ticket figures above (F1 0.356,
alert recall 0.211) measure the cut, not the method. At the 12 handoff
checkpoints that every method answered on those 4 tickets, it found 100 of
104 promises (event memory 104), its deadlines were 0.910 right (event memory
1.000), it missed 10 of 44 closes (event memory 2 of 47) and made 6 false
alerts (event memory 2), and its freshness error was 0.175 (event memory
0.086). It made no false close.

### Every false close, explained

*The invariant asked for:* false closes on the test split, zero or as close
to it as possible. **The event memory made none** — at the end of any ticket
or at any handoff, with the guards on and with them off.

Other methods: full context made one, at a handoff of SIM-4242-M0018: it
reported "Пришлём вам накладную до конца недели" (a promise that was broken)
as *claimed*, citing message 90, "Вам отправлено по накладной VC-626" — the
company's side of the dispute about which model was shipped, which mentions
a waybill and promises nothing. By the end it had the promise open again. It also made one wrong-target close
(SIM-4242-M0010): the renegotiated "выслать трек-номер" reported as fulfilled
by the confirmation of its replacement. Rolling summary and Mem0 made none on
their 4 tickets.

What the event memory got wrong instead, all of it visible:

- **One true close held back by the guards** (SIM-4242-M0016, message 99,
  "Деньги за доставку пришли, спасибо!"). The model copied the promise's
  wording as "вернём деньги за доставку" from "Мы вернём вам деньги за
  доставку через неделю", dropping "вам", so the two references did not agree
  and the close was refused. The promise stayed open with "возможно,
  выполнено по сообщению №99 — подтвердите" in the brief — a correct
  suggestion — and its deadline later raised one false alert. Comparing the
  span word by word, allowing skipped function words, would have admitted it;
  that change was found on the test split and was not made.
- **Two closes the model did not propose** (M0002 and M0019: "Спасибо, что
  отписались по возврату"). Nothing was proposed, so nothing could be
  admitted; both promises stayed open and each raised a false alert after its
  deadline. These are the other two of the three false alerts.
- **Four extra promises** (precision 0.986), all from filler the writer turned
  into a vague promise: "Новой информации пока нет" became "...как только что-то
  появится — сразу дадим знать". The extractor read them correctly as vague
  promises; no label accounts for them. They carry no deadline and raised no
  alert. The writer check (`rewrite.acceptable`) does not know "дадим знать",
  "уведомим", "проинформируем".

### What the guards did

The guards are in the reducer, so their effect is measured on identical model
output: each run's recorded proposals folded again with them on (as run) and
off. A refused close is judged against the labels: *a false close prevented*
when the promise was still owed at that message, *a true close blocked* when
that message is the one that closed it.

| run | guards | false closes | wrong target | missed closes | suggestions | breach alerts (right, false, missed) | status accuracy |
|---|---|---|---|---|---|---|---|
| dev2 run 1 | on | 0 | 0 | 7 of 31 | 2 | 14, 4, 1 | 0.865 |
| dev2 run 1 | off | 0 | 2 | 6 of 31 | 0 | 14, 3, 1 | 0.846 |
| dev2 run 2 | on | 0 | 0 | 2 of 31 | 0 | 14, 1, 1 | 0.962 |
| dev2 run 2 | off | 0 | 0 | 2 of 31 | 0 | 14, 1, 1 | 0.962 |
| dev2 run 3 | on | 0 | 0 | 1 of 31 | 3 | 14, 1, 1 | 0.981 |
| dev2 run 3 | off | **2** | 1 | 0 of 31 | 0 | 12, 0, **3** | 0.943 |
| dev2 run 4 | on | 0 | 0 | 0 of 31 | 0 | 14, 0, 1 | 1.000 |
| dev2 run 4 | off | 0 | 0 | 0 of 31 | 0 | 14, 0, 1 | 1.000 |
| **test2** | on | 0 | 0 | 3 of 156 | 1 | 91, 3, 0 | 0.990 |
| **test2** | off | 0 | 0 | 2 of 156 | 0 | 91, 2, 0 | 0.993 |

(All rows use the final reducer; a dev run's proposals were made with the
instructions as they were then.) Over the four dev runs the final guards
refused 5 closes, all of them false: three renegotiations that were really a
second, similar promise ("перезвоню насчёт рассрочки" taken to replace
"...насчёт оплаты"; "проверю статус возврата" taken to replace "уточню на
складе"), and the confirmation that, in the same ticket, would then have
closed the wrong one of the pair. They blocked no true close on dev. On test
they refused one close, a true one (above).

**How many true closes the relevance check blocks** was designed on dev and
the oracle: no planted close of 475 template tickets of all three profiles is
refused, and none of the 124 true closes in the four dev runs. The design
changed twice on that evidence: a promise worded only in generic verbs
("напишу вам до пятницы") could not be closed or renegotiated at all, so a
generic verb now counts for one point against two for a word that names a
thing; and a renegotiation must not name a different thing than the promise
it replaces.

So on this test split the guards cost one false alert and prevented nothing,
because the model, with the instructions tuned on dev, did not make the
mistakes they catch. They are insurance against a model, a prompt or a ticket
in which it does: in round 1 the same kind of mistake retired 5 of 32 broken
promises, and on dev run 3 it would have retired 2 of 15.

### Tuning on dev2

Four runs of the event memory alone ($0.044 each); no baseline ran before
the test.

1. *No false close, but 7 of 31 true closes missed.* The first version of the
   instructions told the model to report nothing when unsure; with reasoning
   off it then stayed silent on plain confirmations ("Спасибо, что
   перезвонили!"). Doubt belongs to the reducer, so the model was asked to
   report every close it can quote.
2. *2 missed.* Both traced to promises recorded with minimal quotes ("напишу
   вам до пятницы" for "Уточню на складе и напишу вам до пятницы"): a later
   "Ответ про склад получила" had nothing to name. The model is now asked for
   the whole promise clause, and generic verbs weigh 1 instead of 0.
3. *No miss, but 2 false closes and 1 wrong-target close got through*: the
   twin trap, as a renegotiation (above). Renegotiations must now name the
   same thing; the instructions say so.
4. *0 false, 0 wrong-target, 0 missed; status accuracy 1.000.* Frozen.

### Comparison with round 1

| event memory | round 1 (test, 20 × 56 messages) | round 2 (test2, 20 × 172 messages) |
|---|---|---|
| reader | GLM 5.3 Flash, reasoning low | DeepSeek V4.1 Flash, reasoning off |
| commitment F1 | 0.968 | 0.993 |
| deadline accuracy | 1.000 | 1.000 |
| status accuracy | 0.946 | 0.990 |
| breach-alert recall | 0.812 | 1.000 |
| false alerts per ticket | 0.000 | 0.150 |
| false closes at the end (re-scored) | 6 | 0 |
| false closes at any checkpoint | 15 | 0 |
| freshness error | 0.058 | 0.083 |
| dispute recall | 0.931 | 0.928 |
| USD per ticket / per message | 0.0076 / 0.00014 | 0.012 / 0.00007 |

Which effect is which:

- **The guards** are separated exactly (table above): none of round 2's
  improvement in false closes on test is theirs; the guards-off replay has the
  same zero.
- **Model and instructions** together account for the drop from 6 false
  closes to 0, on tickets that are harder for closing (twin promises,
  renegotiations, cancellations; round 1 had none). They cannot be separated:
  the instructions were rewritten for this model, and running GLM on round
  2's tickets is not allowed, because GLM wrote them. The bare-thanks rule is
  the instructions' part; that DeepSeek follows it with reasoning off is the
  model's.
- **Ticket length** made the context harder for every method, the baselines
  most: full context's freshness error went from 0.181 to 0.291 and its
  dispute recall from 0.861 to 0.506; Mem0's from 0.173 to 0.318 and 0.875 to
  0.619. The event memory's freshness error rose from 0.058 to 0.083 (the
  intervals overlap). The gap that was round 1's clearest finding is wider on
  long tickets.
- **What got worse.** The event memory now raises false alerts (0.15 per
  ticket against 0 in round 1): when a close is missed or refused, the promise
  stays open, which is the fail-safe direction by design. Full context's
  deadline accuracy fell from 0.923 to 0.885 (DeepSeek's date arithmetic, on
  more promises per ticket). Per ticket the event memory costs more (longer
  tickets), per message half as much (cheaper model, prompt cache).

### Cost per ticket

| | event memory | full context (checkpoints) | rolling summary | Mem0 |
|---|---|---|---|---|
| USD per ticket | 0.012 | 0.0040 | 0.14 (more; 3 of 4 cut) | 0.052 |
| USD per message | 0.00007 | 0.00002 | 0.00076 | 0.00028 |
| prompt tokens per ticket | 320,766 | 20,154 | 361,367 | 1,749,432 |
| completion tokens per ticket | 12,916 | 3,597 | 304,214 | 18,486 |
| model calls per ticket | 172 | 4.4 | 185 | 509 (LLM + embeddings) |
| model seconds per ticket | 304 | 32 | 2,058 | 409 |

The event memory reads one message and the card per call (1,860 prompt
tokens on average); 89% of its prompt tokens were served from DeepInfra's
prompt cache — the fixed instructions come first — which is why pinned
routing matters. Full context is
cheapest because it is asked only 4–5 times per ticket — fine for a handoff
brief, not for an alert at the moment a deadline passes.

### Threats to validity (round 2)

Everything listed for round 1 still holds (simulated tickets, the generator
written by the method's authors, one extracting model, the "end of week"
convention, scoring by value). In addition:

- **The guards were not exercised on test.** The model made no wrong close
  for them to catch, so the test split says nothing about how many they would
  catch; the evidence that they work is the dev replay (5 of 5 false closes
  refused, run 3) and the adversarial suite, both of which their designer
  saw. Their cost on test (one true close blocked of 154) is measured.
- **Tuning saw the trap.** The renegotiation rule and the instruction about
  similar promises were added after dev run 3 showed the twin trap — a trap
  the same team built into the generator. The test split has the same kind of
  trap with different words and objects; a real ticket's traps will differ.
- **27% of test messages keep template wording** (21% discarded by the
  stricter rewrite check, 6% returned unchanged), against 5.6% in round 1.
  Template text is easier to read; this flatters every method equally, but
  more than in round 1.
- **The writer still adds content sometimes.** Four filler messages became
  vague promises; the event memory found them and was charged with false
  positives, full context ignored them. The labels are otherwise unaffected
  as far as we found.
- **Subsets.** Rolling summary and Mem0 ran on 4 tickets; their intervals
  are wide and their differences rest on 4 tickets. Three of the rolling
  summary's four tickets were cut by the budget before their last checkpoint.
- **Reader and writer are both "flash" models of other families than round
  1's pair**, so round 1 and round 2 differ in reader, writer, ticket length,
  ticket content and instructions at once; only the guards are isolated.
- **Provider.** All calls were served by one host (DeepInfra, fp8). Another
  host of the same model may answer differently.
- **Latency.** Up to 12 tickets ran at once against one host; model seconds
  include queueing.

### Reproducing round 2

```bash
pip install -e ".[dev,mem0]"
python -m duebook.bench.experiment --split dev2 --rewrite-only --max-usd 0.05   # done once
python -m duebook.bench.experiment --split dev2 --methods event_memory --max-usd 0.15 \
    --out results/round2/dev2-4.json
python -m duebook.bench.experiment --split test2 --max-usd 1.07 --sample-events 20 \
    --subset rolling_summary=4,mem0=4 --workers 12                              # refuses to run twice
python -m duebook.bench.report results/round2/test2.json --dev results/round2/dev2-*.json
```

Round 2's event memory had no verifier. Since round 4, `event_memory` runs
with it, and round 2's configuration is reported beside it as
`event_memory_no_verifier`.

The rewritten text is in `results/round2/tickets/`; `results/round2/
writer-trial.json` holds the writer trial; `results/round2/spend.jsonl` every
charge of the round. Answers are cached under `.cache/responses/` (not
committed), so on the machine that ran them the dev and test runs replay
without a model call.


## Round 1: GLM 5.3 Flash, 50–64 message tickets

First real comparison of the method (structured event memory: a model
proposes per message, the reducer admits) with (a) the whole conversation in
the model's context, (b) a rolling summary and (c) Mem0, on simulated
long-running support tickets. Run on 2026-11-17. Raw records: `results/` (round 1's files are the ones not under `results/round2/`).

### Short version

On the 20 test tickets (50–64 messages, 2–3 handoffs each):

- **Deadlines (RQ1).** The event memory placed every deadline of every promise
  it found correctly (92/92). The baselines, which compute dates themselves,
  were wrong on 7–8% (full context 0.923, Mem0 0.920, rolling summary 0.934;
  paired intervals exclude zero). About half of those errors are a convention
  the baselines were not told ("до конца недели" read as Sunday, not Friday);
  the rest are arithmetic, mostly business-day counting.
- **Freshness (RQ2).** Of the facts each method presented as current at the
  handoffs and at the end, 5.8% were stale or contested for the event memory
  and 17–18% for every baseline. This is the clearest difference, and its
  intervals exclude zero against all three.
- **Commitment detection (RQ1/RQ3).** The event memory (F1 0.968), full
  context (0.978) and Mem0 (0.951) are statistically indistinguishable; the
  rolling summary is far behind (0.731).
- **Where the event memory loses.** Breach-alert recall: 0.81 against 0.97 for
  full context and 0.94 for Mem0 (the paired interval against full context
  touches zero; borderline). Cause, found in the records: the extractor
  sometimes attached `supersedes`, `cancel` or `resolved` to the wrong
  promise, and the reducer admitted it, because it checks *who* may change a
  promise, not *whether the message is about that promise*. A broken promise
  silently retired raises no alert; this cost 5 of its 6 missed alerts. It
  also has the lowest status accuracy (0.946) for the same reason. It raised
  no false alert.
- **Cost.** Per ticket: event memory $0.0076, full context $0.0015 (asked only
  at the 3–4 checkpoints), rolling summary $0.021, Mem0 $0.038. Asked at every
  message — which an up-to-date view or a timely alert needs — full context
  would cost a projected $0.022 per ticket here and $0.20 on a 200-message
  ticket, against $0.030 for the event memory.

On the two 200+ message tickets the gap widens: the event memory scored 1.0
on every measure except one missed alert, the rolling summary kept 2 of 23
promises, and full context presented a stale or contested value in 30% of the
facts it called current.

### What was run

| | |
|---|---|
| Extracting model, every method | `z-ai/glm-5.3-flash` via OpenRouter, reasoning effort `low`, temperature 0 |
| Writer (rewrites ticket text) | `deepseek/deepseek-v4.1-flash`, reasoning off, temperature 0.8 |
| Mem0 | `mem0ai==2.2.1`, LLM as above, embedder `openai/text-embedding-3-small` via OpenRouter, local Qdrant per ticket |
| Test split | seed 202, 20 tickets, 50–64 messages (mean 56.2), 8 with 2 handoffs and 12 with 3, 94 planted promises; run once |
| Dev split | seed 101, 6 tickets; used for tuning (two runs, below) |
| Long split | seed 303, 2 tickets of 210 and 200 messages, 3 and 4 handoffs, 23 promises |
| Checkpoints | every handoff (the brief for the operator taking over) and the end of the ticket |
| Intervals | 95% percentile bootstrap over tickets (2,000 resamples); differences are paired on the same resamples |
| Total charged | $2.80 of the $5 budget, by the OpenRouter key's counter (`results/spend.jsonl`) |

**Tickets.** Generated with planted events and true-by-construction labels
(`duebook.bench.generate`), then rewritten message by message by the writer
model, keeping every planted span verbatim. 63 of 1,124 test messages (5.6%)
kept their template text because the rewrite lost a planted span or turned
into a dialogue. Most messages are filler (status pings, thanks, "any news?").

**Methods.** Every method answers in the same format at the same checkpoints
(`duebook.bench.result`) and is scored by the same code (`duebook.bench.score`):

- *event memory* — the extractor reads each message against the card; the
  reducer admits; at a checkpoint the card is read deterministically (no
  model call). The model never computes a date.
- *full context* — at a checkpoint, the whole transcript so far, numbered and
  time-stamped, and one question.
- *rolling summary* — after every event, the previous summary and the event
  in, the next summary out (at most 500 words); at a checkpoint, the question
  is answered from the summary alone.
- *Mem0* — every event added as it arrives; at a checkpoint, everything Mem0
  holds for the ticket (`get_all`) and the question.

The baselines' question carries the same rules the extractor is given (only
the speaker can promise; a disputed value is a dispute, not a fact) and asks
for deadlines as absolute local times.

**Measures.** Commitments: a found promise matches a planted one by source
message and owing side. Deadlines: exact instant within one minute, or
correctly none. Breach alert: a company promise held open past a deadline the
method trusts, at the checkpoint. Freshness error: of the presented current
facts that carry a planted value (matched by value, not slot name), the share
stale or contested at that moment. Disputes, failed steps: precision and
recall at every checkpoint where they exist.

### Test split (20 tickets)

Accuracy at the end of each ticket:

| metric | event_memory | full_context | rolling_summary | mem0 |
|---|---|---|---|---|
| commitment precision | 0.958 [0.920, 0.990] | 0.989 [0.967, 1.000] | 0.836 [0.702, 0.940] | 0.967 [0.929, 1.000] |
| commitment recall | 0.979 [0.945, 1.000] | 0.968 [0.912, 1.000] | 0.649 [0.484, 0.813] | 0.936 [0.876, 0.980] |
| **commitment F1** | 0.968 [0.940, 0.990] | 0.978 [0.948, 1.000] | 0.731 [0.575, 0.861] | 0.951 [0.913, 0.980] |
| **deadline accuracy** | 1.000 [1.000, 1.000] | 0.923 [0.849, 0.979] | 0.934 [0.877, 0.984] | 0.920 [0.872, 0.966] |
| status accuracy | 0.946 [0.894, 0.989] | 0.967 [0.919, 1.000] | 0.984 [0.945, 1.000] | 0.966 [0.917, 1.000] |
| breach-alert precision | 1.000 [1.000, 1.000] | 0.969 [0.906, 1.000] | 0.815 [0.613, 1.000] | 0.938 [0.857, 1.000] |
| breach-alert recall | 0.812 [0.657, 0.939] | 0.969 [0.897, 1.000] | 0.688 [0.469, 0.900] | 0.938 [0.848, 1.000] |
| **false alerts per ticket** | 0.000 [0.000, 0.000] | 0.050 [0.000, 0.150] | 0.250 [0.000, 0.550] | 0.100 [0.000, 0.250] |

Context, over every handoff and the end (80 checkpoints):

| metric | event_memory | full_context | rolling_summary | mem0 |
|---|---|---|---|---|
| **freshness error** (lower is better) | 0.058 [0.000, 0.123] | 0.181 [0.126, 0.237] | 0.177 [0.115, 0.242] | 0.173 [0.117, 0.228] |
| freshness error, handoffs only | 0.049 [0.000, 0.103] | 0.186 [0.127, 0.245] | 0.159 [0.092, 0.230] | 0.163 [0.098, 0.225] |
| fact change right | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.872 [0.747, 0.966] | 0.949 [0.873, 1.000] |
| dispute precision | 0.971 [0.906, 1.000] | 0.954 [0.889, 1.000] | 0.927 [0.822, 1.000] | 0.984 [0.953, 1.000] |
| dispute recall | 0.931 [0.831, 1.000] | 0.861 [0.729, 0.963] | 0.708 [0.556, 0.848] | 0.875 [0.765, 0.985] |
| false disputes per ticket | 0.100 [0.000, 0.300] | 0.150 [0.000, 0.300] | 0.200 [0.000, 0.450] | 0.050 [0.000, 0.150] |
| failed-step precision | 1.000 [1.000, 1.000] | 0.961 [0.917, 1.000] | 0.795 [0.728, 0.868] | 0.839 [0.770, 0.920] |
| failed-step recall | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 0.904 [0.800, 1.000] | 1.000 [1.000, 1.000] |

No method failed to answer a checkpoint on the test split.

Cost and time (per ticket unless stated):

| metric | event_memory | full_context | rolling_summary | mem0 |
|---|---|---|---|---|
| charged USD | 0.0076 [0.0072, 0.0080] | 0.0015 [0.0013, 0.0016] | 0.021 [0.019, 0.023] | 0.038 [0.036, 0.039] |
| charged USD per message | 0.00014 | 0.00003 | 0.00037 | 0.00067 |
| prompt tokens | 64,160 | 6,945 | 51,965 | 541,845 |
| completion tokens (incl. reasoning) | 5,270 | 1,751 | 35,391 | 12,110 |
| model calls | 56.2 | 3.6 | 63.6 | 156.3 (LLM + embeddings) |
| model seconds per message, while reading | 1.48 | 0 | 3.78 | 2.79 |
| model seconds per checkpoint answer | 0 (card read, no call) | 3.88 | 3.03 | 3.51 |
| model seconds, whole ticket | 83 | 14 | 223 | 170 |

Paired differences, method minus event memory (`*`: the interval excludes zero):

| metric | full_context | rolling_summary | mem0 |
|---|---|---|---|
| commitment F1 | +0.010 [−0.029, +0.046] | −0.238 [−0.400, −0.101] * | −0.017 [−0.060, +0.020] |
| deadline accuracy | −0.077 [−0.151, −0.021] * | −0.066 [−0.123, −0.016] * | −0.080 [−0.128, −0.034] * |
| breach-alert recall | +0.156 [+0.000, +0.312] | −0.125 [−0.387, +0.125] | +0.125 [−0.031, +0.312] |
| false alerts per ticket | +0.050 [+0.000, +0.150] | +0.250 [+0.000, +0.550] | +0.100 [+0.000, +0.250] |
| freshness error | +0.123 [+0.040, +0.205] * | +0.120 [+0.047, +0.196] * | +0.115 [+0.040, +0.189] * |
| fact change right | +0.000 | −0.128 [−0.253, −0.034] * | −0.051 [−0.127, +0.000] |
| dispute recall | −0.069 [−0.241, +0.090] | −0.222 [−0.422, −0.016] * | −0.056 [−0.208, +0.113] |
| failed-step recall | +0.000 | −0.096 [−0.200, +0.000] | +0.000 |
| USD per ticket | −0.006 * | +0.013 * | +0.030 * |

### Deadlines a model computes itself

The baselines state deadlines as absolute times; the event memory's model
only describes what was said and code resolves it. Of the deadlines of the
promises each method found:

| method | right | "end of week" read as Sunday | arithmetic wrong | strict | either convention |
|---|---|---|---|---|---|
| event_memory | 92 | 0 | 0 | 1.000 | 1.000 |
| full_context | 84 | 3 | 4 | 0.923 | 0.956 |
| rolling_summary | 57 | 3 | 1 | 0.934 | 0.984 |
| mem0 | 81 | 5 | 2 | 0.920 | 0.977 |

The convention column is not entirely the baselines' fault: their question
said a day-only deadline means the end of that day, but not that a week ends
on Friday. The arithmetic errors are real: "в течение 3 рабочих дней" said
on a Wednesday evening became Wednesday next week (should be Monday), said on
a Tuesday became the next Tuesday (should be Friday); "в течение 5 рабочих
дней" said on a Saturday became Monday after next. No method invented or
dropped a deadline. Every wrong deadline is an alert at the wrong time, and
none of them is detectable by looking at the answer.

### Long tickets (2 tickets, 210 and 200 messages)

Two tickets give no meaningful intervals, so these are counts:

| | event_memory | full_context | rolling_summary | mem0 |
|---|---|---|---|---|
| promises found (of 23), false | 23, 0 | 23, 0 | 2, 1 | 23, 0 |
| deadlines right | 23/23 | 21/23 | 2/2 | 19/23 |
| breach alerts: right, false, missed | 6, 0, 1 | 7, 1, 0 | 0, 0, 7 | 7, 0, 0 |
| presented facts stale or contested | 0 of 28 | 12 of 40 | 1 of 9 | 4 of 32 |
| fact changes right | 14/14 | 12/14 | 5/14 | 14/14 |
| disputes found (of 12), false | 12, 0 | 8, 0 | 3, 2 | 10, 0 |
| failed steps found (of 13) | 13 | 12 | 3 | 13 |
| USD per ticket | 0.030 | 0.0051 | 0.065 | 0.138 |
| model seconds per ticket | 274 | 37 | 650 | 591 |

The rolling summary collapsed: its final summaries were 222 and 135 words,
well under the 500-word bound, so it lost promises by compressing, not because
the bound forced it to.

### How cost scales

| USD per ticket | 56 messages (test) | 200+ messages (long) | growth |
|---|---|---|---|
| event memory | 0.0076 | 0.030 | ×3.9 (linear in messages) |
| full context, at checkpoints (measured) | 0.0015 | 0.0051 | ×3.5 |
| full context, at every message (projected) | 0.022 | 0.20 | ×9.1 (quadratic) |
| rolling summary | 0.021 | 0.065 | ×3.1 |
| Mem0 | 0.038 | 0.138 | ×3.6 |

The projection re-renders the transcript prefix before every event and prices
it with the tokens-per-character ratio and the price per token measured in
the full-context runs (0.33 and 0.37 tokens per character). As measured here,
full context is the cheapest method because it was asked only 3–5 times per
ticket; that is a fair protocol for briefing a new operator, not for alerting
on a deadline as it passes.

### The extractor's prompt on long tickets

The extractor sees the fixed instructions, the card so far and one message,
never the transcript. Measured on the two long tickets, the prompt grew from
1,008 to 1,304 tokens and from 1,010 to 1,305 (+29%) over 200+ messages,
most of it fixed instructions; per-message cost stayed flat. A bound on what
the model is shown of the card (`extract.prompt.Bound`: settled steps, old
open questions and old facts capped, everything the model can act on always
shown) would have saved 1.4% and 2.4% at the end of these tickets, computed on
the identical card sequence. The card does not grow enough to matter, so the
bound exists, is tested, and is off by default.

![Prompt tokens per message on the two long tickets, measured and with the bound](prompt-growth.svg)

### What happened on the dev split

Dev run 1 (6 tickets, all methods) found problems that were not about memory:

- The extractor marked a company's counter-claim ("по накладной мы отправили
  BL-597") as a *change*, so the reducer superseded the customer's value
  instead of recording a dispute, or filed it under a new slot
  (`waybill.number`), so nothing conflicted. Dispute recall was 0.36.
- Thanks that implied a promise was kept were not always read as
  confirmation; status reports ("мы уже занимаемся") were read as promises.
- The baselines listed both sides of a dispute as current facts and called a
  company's plan change a dispute.
- Mem0 crashed inside its own code on 4 of 6 tickets when the model returned
  its memory list as strings.
- The writer model, asked to rephrase a one-word filler message, sometimes
  wrote a whole exchange with a new agent promise in it (about 3% of
  messages). All tickets were rewritten again with such rewrites rejected.

One round of fixes followed: clarified instructions for the extractor and the
same clarifications, where the rule is shared, in the baselines' question;
generic slot aliases (a product's model, a charged amount); a repair of the
string-memory format and retries for Mem0. Dev run 2, after the fixes:

| metric | event_memory | full_context | rolling_summary | mem0 |
|---|---|---|---|---|
| commitment F1 | 0.984 | 0.984 | 1.000 | 0.984 |
| deadline accuracy | 1.000 | 0.933 | 0.968 | 0.933 |
| freshness error | 0.068 | 0.107 | 0.180 | 0.093 |
| dispute recall | 0.857 | 1.000 | 1.000 | 0.786 |
| breach-alert recall | 1.000 | 1.000 | 1.000 | 0.889 |

The test split was then run once, with no further change.

### What the results say about the research questions

- **RQ1 (extraction).** A per-message extractor with a deterministic reducer
  finds promises as well as a model reading the whole transcript, and is the
  only method whose deadlines were all right — because no model computed
  them. Its weak point is assigning a message to the right *existing*
  promise: wrong `supersedes`/`cancel`/`resolved` references cost 5 of the 32
  breach alerts on the test split (a sixth was lost to a promise it never
  found).
- **RQ2 (current context).** The card keeps one current value per slot and
  shows a dispute as a dispute; the baselines, asked explicitly not to, still
  presented stale or contested values as current in about one fact in six.
- **RQ3 (against summarisation and LLM memory).** Better than the rolling
  summary on nearly everything, and decisively on long tickets. Against full
  context and Mem0: equal on finding promises, better on deadlines and
  freshness, worse on breach-alert recall, cheaper than Mem0 by 5×, and more
  expensive than full context asked only at checkpoints.

### Threats to validity

- **Simulated tickets.** Every ticket is generated from templates and then
  paraphrased message by message. Real tickets interleave topics, refer back
  loosely, contain typos, attachments, quoted e-mails and several promises in
  one message. The planted events are clean and one per message. The
  absolute numbers say little about real tickets; the comparison between
  methods on identical input is what this benchmark is for.
- **The generator was written by the same team as the method.** The planted
  event types (promise, change, dispute, failed fix) are the categories the
  event memory was designed around, and the scorer measures what the card
  holds. The baselines are asked for the same categories with the same rules,
  which removes the advantage of being *asked* the right question, but not of
  being *built* for it.
- **Easy for a long-context model.** A standard ticket is about 3–4 thousand
  tokens and a long one 10–14 thousand, well inside the model's window; full
  context is near the ceiling on most measures. Tickets long enough to stress
  the window were not generated.
- **One extracting model.** All methods read with `glm-5.3-flash` at low
  reasoning effort. Another model may rank them differently, in particular on
  date arithmetic, where the baselines' errors are the model's own.
- **One writer, per-message rewriting.** The writer sees one message at a
  time, not the conversation; 5.6% of test messages kept template wording.
- **The "end of week" convention.** The gold label reads it as Friday (the
  resolver's choice) and the baselines were not told; the table above gives
  both readings.
- **Tuning.** The extractor's instructions and the baselines' question were
  each adjusted once on the 6 dev tickets. Six tickets is little; a second
  round was not attempted for that reason. The weakness found on the test
  split (wrong `supersedes` references) was not fixed, because fixing it and
  re-running would be tuning on test.
- **Small test set.** 20 tickets give wide intervals; a difference whose
  paired interval includes zero is not established either way. The
  breach-alert recall gap to full context is one such borderline case.
- **Latency.** Up to 16 tickets ran at once against one provider, so model
  seconds include queueing; they show orders of magnitude.
- **Mem0 configuration.** Open-source Mem0 2.2.1 with its own OpenAI
  providers pointed at OpenRouter, custom instructions describing a support
  ticket, and `get_all` at checkpoints. Its extraction prompt resolves
  relative dates against the real current date (the open-source version
  ignores message timestamps), which the instructions and per-message
  timestamps work around imperfectly. The format repair and retries were
  needed for this model. The hosted platform, graph memory and `search`-based
  retrieval were not evaluated.
- **The rolling summary bound** is 500 words; it did not bind on any ticket.
- **Scoring by value.** A fact or dispute is recognised by its planted value
  (a number exactly, a word by stem). A method that paraphrases a value beyond
  recognition is counted as presenting an unjudged fact, not a wrong one.
- **Status of a kept promise** is judged as done/not done; whether a
  confirmation was by the right side is not scored separately.

### Reproducing

```bash
pip install -e ".[dev,mem0]"
python -m duebook.bench --seed 1 --tickets 200                   # oracle self-check
R1="--model z-ai/glm-5.3-flash --writer deepseek/deepseek-v4.1-flash --session-cap 5"
python -m duebook.bench.experiment --split dev $R1 --max-usd 1.0     # tuning
python -m duebook.bench.experiment --split test $R1 --max-usd 2.5    # refuses to run twice
python -m duebook.bench.experiment --split long $R1 --max-usd 1.2
python -m duebook.bench.report results/test.json --long results/long.json
```

(Round 1 ran with the code as of the merge of `feat/benchmark-baselines`;
since then the reducer has close guards, the prompts changed, and the
default reader is DeepSeek. Round 1's numbers are as they were recorded.)

The rewritten ticket text is cached in `results/tickets/`, so a rerun reads
exactly the same words. `results/dev-1.json` and `results/dev-2.json` are the
two dev runs; `results/probe/long-unbounded.json` is the first measurement of
the extractor's prompt on a long ticket, made before the rewriter was fixed
(its text differs from `results/tickets/long.json`).
