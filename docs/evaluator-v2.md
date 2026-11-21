# Duebook Evaluator V2

V2 is an opt-in offline scorer for saved event-memory runs. The existing benchmark commands still use V1. V2 replays the saved proposals with the existing fold, reducer, verifier verdicts and alert clock. It checks rebuilt cards against any saved snapshots under the selected rule version and calendar. Round 10 and Round 10c keep their original labels, records and metric definitions.

## Reproduce the fixture

```bash
fixture=tests/fixtures/evaluator-v2
python -m duebook.bench.evaluator_v2 \
  --events "$fixture/events.json" --record "$fixture/record.json" \
  --calendar UTC --rules 6 --gold "$fixture/gold.json" \
  --reviews "$fixture/reviews.json" --out /tmp/duebook-v2-fixture.json
python scripts/replay_check.py
```

The commands use local artifacts and zero model calls. The output is a new JSON file with a corpus summary, separate `score_v1` and `score_v2` per ticket, the source and review hashes, and the evidence used to judge each ticket. V1 fields are taken verbatim from the saved scores. If a synthetic record lacks those fields, the command uses the existing V1 scorer with the native split labels. An event-file input without archived V1 scores has `score_v1: null`.

For an archived run, export a review packet containing every presented identity and the saved creation and transition proposals. The packet gives proposed wording, source IDs, adjudicable proposal indices, stored verdicts, replayed rejections and any retained suggestions.

```bash
python -m duebook.bench.evaluator_v2 \
  --split test3 --rewrites results/round4/tickets/test3.json \
  --record results/round10c/test3-live.json --rules 6 \
  --review-packet --out /tmp/duebook-v2-test3-review.json
```

The archived Round 10 `test5` event-memory run uses `--split test5 --rewrites results/round10/tickets/test5.json --record results/round10/test5.json --rules 5`. Keep the original replay calendar of `Europe/Moscow` for both examples. Use `--ticket ID` to select specific recorded tickets. Scoring a real event stream requires a frozen file of `{"tickets": [{"id": "T", "events": [...], "end": "..."}]}` using the existing [event codec](../src/duebook/store/codec.py). `end` is the declared observation boundary. Each event keeps its observed timestamp and ID.

## Reference labels and adjudication

Keep two files. Freeze the transcript reference before viewing any predictions. Review semantic equivalence and proposal decisions against that reference after opening the review packet. Another human can inspect every source span and reason, resolve disagreements, and trace changes through the file hashes. The evaluator uses the resulting human judgments and never invokes a semantic judge.

The gold envelope is `{"version": 2, "source_sha256": "…", "tickets": [...]}`. For `--events`, the source SHA-256 covers the original file bytes. For `--split`, it covers the generated event streams, including the frozen rewrites and observation boundaries. Copy it from the review packet. Each ticket entry has `ticket`, `reviewer`, `commitments` and optional `alert_windows` and `source_roles`.

| Gold field | Meaning |
|---|---|
| `id`, `action`, `object` | Stable ticket-local identity and a specific description of the action and its thing, subject, or recipient. Give distinct undertakings separate IDs even when one message contains both. |
| `owner`, `owner_actor`, `identity` | `owner` is `agent`, `company`, `customer`, or `null` for unknown. Optionally cite the observed actor ID. `identity` is `certain` or `uncertain`. |
| `origin`, `restatements` | The opening event's evidence and optional later references to the same undertaking. Evidence objects have `seq` and a nonblank literal `quote` from that message. |
| `initial_state`, `condition` | `open`, `pending`, or `unknown`. A `pending` offer also specifies its condition. Activation normally changes it to `open` under the same gold ID. A failed condition can be `lapsed`. |
| `deadline` | `{"kind": "exact", "at": "…", "evidence": [...]}`, `{"kind": "absent"}`, or `{"kind": "unknown"}`. Use an offset-bearing timestamp for `exact`. A time phrase with an unresolved timezone belongs in `unknown`. |
| `transitions` | Ordered changes with `seq`, literal `evidence`, and `state`, `deadline`, or both. State can become `open`, `claimed`, `fulfilled`, `cancelled`, `superseded`, `lapsed`, or `unknown`. A supersession gives `replacement`, a distinct ID originating at that event. Optional `later_evidence` cites later corroboration of the same state. |
| `alert_windows` | Ticket-level windows with `gold`, `kind`, `from`, `through`, `expected_at` and `evidence`. Kinds are `due_soon`, `breached`, `claim_unconfirmed`, and `condition_due`. Set `expected_at` to `null` for an explicitly assessed window with no expected alert. Keep windows for the same gold ID and kind disjoint. |
| `source_roles` | Optional ticket-level adjudications with `seq`, `side` and `evidence` for correcting or confirming a source author's normalized side. |

Carry forward the last evidence-backed state. `open` describes an undertaking known to be outstanding at the observation point. A debtor's statement of completion is `claimed`. Use `fulfilled` for supported completion and `unknown` when outcome evidence runs out. An ending transcript supplies an observation boundary. Marking a breach requires its own alert-window evidence, even when a promised date passed in the transcript.

The review envelope contains `version`, `gold_sha256`, `record_sha256`, `method`, `rules`, `calendar`, and `tickets`. Copy `record_sha256`, `method`, `rules` and `calendar` from the review packet. Hash the frozen gold file's exact bytes with `sha256sum`. Each ticket entry has `ticket`, `reviewer`, `identity_reviews`, and optional `proposal_reviews`.

- An identity review has `prediction`, `gold` (a list of relevant gold IDs), `judgment`, `note`, and `evidence`. Give each identity that appeared on the card a review. `equivalent` requires exactly one supported gold ID and matching action, object, owing party, and applicable condition. Different wording can be equivalent. Other judgments are `wrong_action`, `wrong_object`, `wrong_owner`, `wrong_identity`, `merged`, `split`, `unsupported`, `overgeneralized`, and `uncertain`. For `uncertain`, list the still-plausible gold IDs. Review rejected creation proposals too when evidence can locate the cause of a missed commitment.
- A proposal review has `seq`, zero-based `index` in the saved proposal array, `decision` (`admit`, `reject`, or `uncertain`), and `evidence`. Add `expected_target` if evidence identifies a specific gold obligation. Set `suggestion` to `true` if a rejected change should be retained for human review. A combined replacement proposal is judged on whether it retired its target. Its new commitment has its own identity review.
- Prediction IDs come from the reducer, typically `event-id#proposal-index`. An activated offer keeps its original offer ID in V2. Original and replacement obligations are separate identities when the agreement was renegotiated. Repeating the same agreement keeps its original gold ID.

See the frozen [gold](../tests/fixtures/evaluator-v2/gold.json) and [reviews](../tests/fixtures/evaluator-v2/reviews.json) for complete examples.

## How the metrics work

| Output | Definition and denominator |
|---|---|
| `score_v1` | Historical source-message and owing-side detection and all its original measures. Existing V1 tools continue to compute or reproduce them. |
| `semantic_end`, `semantic_messages` | One true positive per equivalence-matched pair at the end or at every message. The matcher retains an incumbent if it is still on the card, otherwise selects the earliest presented eligible prediction with a stable ID tie break. A second equivalent prediction for the same gold ID is a duplicate false positive. Wrong action, object, owner, condition, merge, split, and unsupported or overgeneralized predictions are false positives. Uncovered assessable gold is a false negative. The denominator is `TP+FP` for precision and `TP+FN` for recall. An unavailable rate is `null`. Message and corpus summaries pool counts. |
| `denominators` | Distinct message, other-event, clock, and end checkpoints with matched and excluded counts. An uncertain prediction and its unmatched plausible gold IDs are excluded. A gold identity with unclear identity or owner is also excluded. The card's party is checked separately from the review. A named personal debtor can be checked against the source actor. Company-side delegation is adjudicated from the evidence. |
| `creation` | First semantic match for each assessable gold identity, with elapsed source seconds and intervening message count. `never` means no match before the cutoff. `unjudged` means a plausible uncertain match prevented a determination. |
| `transitions`, `changes` | Each annotated state or deadline change judged at its starting event, and observed changes of a matched identity between checkpoints. State accuracy compares exact states, including `claimed` versus `fulfilled`. Transition accuracy also checks closing evidence and replacement identity. Deadline accuracy counts explicit absence and exact instants within one minute. Unknown status or deadline is excluded from its own denominator. |
| `lifecycle.episodes` | Contiguous intervals for missing or lost identity, duplicates, false closure, missed closure, claim misverification, bad deadlines and other mismatches. Each records occurrence, affected message checkpoints and elapsed seconds. `temporary` ends within the observed trace. `persistent` reaches the end. `censored` ends when the corresponding outcome or deadline becomes unassessable. Current-card gaps after an explicit processing failure are scored as held states and disclosed under coverage. |
| `alerts` | The unchanged lifecycle's eligibility evaluated at source events, its own wakeups, gold deadline and alert boundaries, and the observation end. Events sharing a timestamp update in sequence before evaluating the clock. Each `Notice.key` is counted once. The first compatible occurrence in a human-assessed window is on time within one minute, premature, or delayed. A missing occurrence counts as missed. An extra occurrence in a judged window or an alert on an established spurious commitment counts as false. Other occurrences are unjudged. `exposure` reports elapsed wait after an expected instant and time spent under premature or false eligibility. These are hypothetical scheduler observations. Delivery, worker latency and deduplication by a downstream channel require delivery records. |
| `attribution` | Saved proposals show proposed identities and targets. Recorded verification errors and vetoes, replayed admission/rejection reasons and human-reviewed unsafe changes are reported independently. A quote or date rejected by the reducer shows the point of rejection. It may have originated in extraction. Unread and held messages, source-role disagreements and cases missing a human attribution verdict have separate entries. Do not infer a sole cause from these observations. |

The semantic scorer needs per-event saved proposals and stable reducer IDs. Sparse checkpoint-only baselines and Round 10c's live full-context answers lack those IDs. They retain their existing V1 measures. The default alert policy and standard work calendar are used for the CLI replay, with the timezone selectable. The evaluator measures source chronology and evidence within the frozen observation period. It cannot establish fulfillment outside that period.

## Sample comparison on the hand-authored fixture

The fixture has 21 messages across two tickets. The proposals deliberately include confusing and erroneous outputs. Its values validate the scoring rules and make no claim about model accuracy.

| Ticket | V1 end anchor TP / FP / FN, F1 | V2 end semantic TP / FP / FN, F1 | V2 every message TP / FP / FN, F1 |
|---|---:|---:|---:|
| `IDENTITY` | 4 / 0 / 0, 1.000 | 3 / 1 / 1, 0.750 | 22 / 12 / 12, 0.647 |
| `CLOCK` | 6 / 0 / 0, 1.000 | 7 / 0 / 0, 1.000 | 42 / 0 / 0, 1.000 |

- `IDENTITY` message 1 promises both a shipping refund and an emailed invoice. An equivalent refund paraphrase earns credit. Reviewing an invoice fails the promised email action despite the right anchor. Message 2 repeats the refund and the reducer rejects the duplicate. The ambiguous confirmation at message 3 is vetoed and retained as a suggestion. The unsafe refund closure at message 4 lasts two message checkpoints and 20 minutes until the real confirmation at message 6. The final card shows fulfillment with the earlier, wrong evidence. The separate receipt claim is incorrectly marked fulfilled for five minutes before the transcript makes the outcome uncertain, so that episode is censored. [Fixture events](../tests/fixtures/evaluator-v2/events.json) · [adjudications](../tests/fixtures/evaluator-v2/reviews.json).
- `CLOCK` includes an offer tracked as a semantic identity even before activation. V1's unconditional commitment F1 has a different denominator. The legitimate postage renegotiation retires the original and creates the replacement. The condition notice fires between messages at 11:00. Of four expected alerts, one is on time, one is 20 minutes early, one is 20 minutes late and one is missed for the remaining 50-minute judged window. The invented stock deadline creates one false alert in an explicitly assessed window. Five other notices fall outside the judged kinds or windows. [Fixture events](../tests/fixtures/evaluator-v2/events.json) · [alert windows](../tests/fixtures/evaluator-v2/gold.json).
