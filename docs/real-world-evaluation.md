# Duebook real-world evaluation

Source assessment as of 21 November 2026. The benchmark will replay authentic public messages in event order and judge the card after the last observed message. A public transcript may omit direct messages, deleted messages, work in other systems, and anything that happened after that boundary.

## Sources and permitted use

This is a non-commercial academic evaluation for an HSE master's project. The TWCS corpus was published by Thought Vector on Kaggle, with PointScrape credited. CC BY-NC-SA 4.0 permits non-commercial use with attribution. The VS Code cohort consists of public GitHub issues used for inference. Raw text stays outside the repository. The paid run sends text through OpenRouter to DeepInfra and requests zero data retention and no training use. The owner's local approval file records the basis for proceeding and the spend limit. [TWCS data card](https://www.kaggle.com/datasets/thoughtvector/customer-support-on-twitter) · [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) · [OpenRouter ZDR](https://openrouter.ai/docs/guides/features/zdr).

## Labeling guide

- Label the frozen transcript before seeing any model output. Mark a ticket reviewed after reading it, even if it has no promises.
- A promise commits a side to an action. For example, “I'll call you tomorrow,” “I'll send the screenshot tonight,” and “Our finance team will refund the overcharge” are promises. Delegated work is owed by the company.
- “The refund was sent” is a status report, “The email should arrive soon” is a prediction, and “Please send the screenshot” is a request. Record conditional offers and unresolved ownership or identity under `ambiguous`.
- Give each distinct promise an ID, a short action plus its object, and the owing side. In `opening`, record the earliest message ID and copy a literal quote. Add later restatements as further evidence. Enter the source actor ID when known. Signed TWCS agents can use `account:INITIALS`.
- Mark the deadline `absent`, `ambiguous`, or `exact`. Copy the original date phrase and its message ID. For an exact deadline, add an ISO 8601 instant with an offset.
- Record the observed state in `fulfillment` and cite the message and verbatim quote. The debtor's report supports `claimed`. Cite confirmation from the other side for `fulfilled`. Use `unknown` if the public transcript ends before the outcome is established.
- Plan on about five minutes per thread. If possible, have a second reader independently label 10 TWCS threads and report agreement on promise anchors.

## Method

1. Freeze and hash each snapshot and draw before labeling. Keep the existing 40 random and 10 long-running VS Code threads. Draw 30 TWCS threads without replacement in seeded order, with at most five threads featuring any one brand. Report selection counts and results by source and cohort.
2. Preserve the source body verbatim. Keep the issue title, edit timestamp, source links, reply edges, actor association and normalization rules in the local record. The title is metadata and the engine receives the issue body as posted. The UTC conversion represents the original instant. Resolve equal timestamps by stable source ID with the issue opener before comments. This tie break is inferred. Cases without enough text, timestamp or role data to replay are ineligible. Run through the existing event-memory bench and use the last observed timestamp, with UTC as an evaluation calendar assumption. A timestamp offset does not establish a speaker's local time or business calendar.
3. `inbound` provides the TWCS side mapping at account level. It establishes neither an individual representative nor a handoff. GitHub author associations and bot indicators are observed metadata. The issue reporter maps to customer for the whole thread. Other OWNER, MEMBER and COLLABORATOR comments map to agent. Other human comments map to customer as a proxy for participants outside the project team. These issue-tracker roles are inferred, can be wrong, and can affect both binding and verification. A bot retains its observed role. Author turnover, issue assignment and issue closure provide no handoff or fulfillment event in this pilot.
4. Issue closure, a thank-you, or silence alone leaves fulfillment unconfirmed. Have a second reader independently label the VS Code challenge cases and record adjudication for disagreements.
5. Use the synthetic benchmark's source-message and owing-side match and one-minute deadline tolerance. This measures anchored detection. A reviewer must inspect identity where several actions share a post, a commitment is restated later, or a predicted action differs despite a matching anchor. Use only judgeable deadlines and statuses and show their denominators. Claimed fulfillment and unknown transcript outcomes need separate treatment from the synthetic status categories. Report handoff outcomes for the TWCS sign-off cases and false alerts where a deadline passes within the observation window and there is enough evidence to judge it. Historical cost and latency can be shown with configurations and units, with cache, host, language and run design differences visible.

## Initial intake results

The [source manifest](../results/real-world/intake.json) records the 50 selected issue URLs, source and bundle SHA-256 digests, and their positions in each cohort. The source frame contains all 154 results returned by the frozen GitHub search. Of these, 92 were eligible, 61 lacked nonblank text from both mapped human sides, and one lacked a message body. Thirteen of the eligible threads left after the random draw met the long-case thresholds. The challenge group takes the longest ten of those. The raw snapshot, normalized bundle and annotation sheet are local, under `.cache/real-world/`.

| Cohort | Threads | Messages | Messages per thread, min / median / max | Span in days, min / median / max | Inference estimate, expected / stress |
|---|---:|---:|---:|---:|---:|
| Random | 40 | 291 | 3 / 5.5 / 53 | 0.09 / 37.64 / 658.53 | $0.20 / $0.93 |
| Long-running challenge | 10 | 131 | 9 / 12 / 25 | 27.77 / 122.93 / 649.87 | $0.11 / $0.45 |
| Total for budgeting | 50 | 422 | — | — | $0.30 / $1.38 |

All 422 engine event bodies, source creation times and author IDs were checked against the corresponding REST records. All 50 threads completed a local empty-proposal fold through the existing engine. The fold served as an import check and made zero model calls. The GitHub API snapshot contains current bodies. Three comments have an update timestamp later than their own thread's last creation time, and 45 issue records have such an update. An issue update can concern metadata. The replay uses the captured bodies, which may differ from those visible at the original posting time.

Three observed cases establish annotation boundaries before any model output exists.

- **Reporter confirmation versus fix claim.** In a randomly selected testing-performance issue, a maintainer says changes were merged and asks the reporter to try an Insiders build. Record the claimed change and the request to test as separate observations. The statement alone supplies no reporter confirmation. [Maintainer comment](https://github.com/microsoft/vscode/issues/238241#issuecomment-6733157647).
- **Administrative closure.** A randomly selected installation/access issue includes network details submitted by participants, then a bot message closing the thread after a request for information. The bot's stated reason provides no evidence that access was restored. [Request for diagnostics](https://github.com/microsoft/vscode/issues/237408#issuecomment-3948267125) · [Bot closure](https://github.com/microsoft/vscode/issues/237408#issuecomment-3990454063).
- **Short confirmation with context.** In a challenge issue, a bot asks for a specific verification response after announcing a fix, and the reporter replies with that marker. The context supports interpreting the marker as a verification signal for the terminal issue. A commitment or deadline would require its own evidence. One technical helper also appears as a GitHub `CONTRIBUTOR`, showing where the role proxy can understate a participant's support role. [Technical helper](https://github.com/microsoft/vscode/issues/237900#issuecomment-2619546222) · [Verification request](https://github.com/microsoft/vscode/issues/237900#issuecomment-2686488254) · [Reporter reply](https://github.com/microsoft/vscode/issues/237900#issuecomment-2694068914).

The expected cost assumes 1.1 calls per nonblank message at 3,000 fixed and card input tokens plus twice the current body size in UTF-8 bytes, and 150 output tokens per call. The stress scenario uses 2 calls, 6,000 fixed and card tokens, and 1,500 output tokens. Both use uncached pricing of $0.14 input and $0.42 output per million tokens. Verifier calls, retries, a larger card and long preceding messages can take actual cost above that stress scenario. Actual charges remain subject to provider billing. [OpenRouter price listing](https://openrouter.ai/deepseek/deepseek-v4.1-flash).

The historical [synthetic `test5` record](../results/round10/test5.json) has 20 tickets with end-anchor precision 0.998, recall 1.000 and F1 0.999 (463 TP, 1 FP, 0 FN). It recorded 3,766 model calls, $0.3410 in usage and 5,478.0 summed model seconds. These are the historical values for later context. The real set has no independently reviewed labels or live model run yet. Actual real accuracy, model errors, model latency, and changes versus synthetic are therefore unavailable. External charge to date is $0.

## Prepare the two label sheets

Keep the original GitHub bundle as `.cache/real-world/vscode-baseline.json` and compare its SHA-256 to `bundle_sha256` in [the frozen manifest](../results/real-world/intake.json). Use the archived private bundle or the original snapshots if reconstruction is needed. The manifest links identify the selected cases.

Use Python 3.12 and the local TWCS CSV. The importer reads and hashes the CSV in one pass, stores message text in a temporary disk database and groups messages by in-file reply edges. It counts threads excluded for falling below six messages, fewer than two readable messages per side, or missing a company message with promise or time wording, in that order. The generated TWCS manifest records tweet IDs, brand IDs, counts, hashes and the number of threads with inferred handoffs. The local bundle contains the raw text.

```bash
root=.cache/real-world
python -m duebook.bench.experiment real prepare "$root/twcs.json" \
  --twcs-csv /path/to/twcs.csv --manifest results/real-world/twcs-intake.json
for cohort in vscode-baseline twcs; do
  python -m duebook.bench.experiment real annotations "$root/$cohort.json" \
    --out "$root/$cohort-annotations.json"
  python -m duebook.bench.experiment real estimate "$root/$cohort.json" \
    > "$root/$cohort-estimate.json"
done
jq -s '{expected_usd: map(.expected_usd) | add, stress_usd: map(.stress_usd) | add}' \
  "$root/vscode-baseline-estimate.json" "$root/twcs-estimate.json"
```

Proceed only if the total expected cost is under $0.50. The existing VS Code manifest preserves the original estimate. The stress projection describes a second cost scenario.

The two annotation files are blank and tied to the corresponding bundle hashes. Each reviewed VS Code challenge entry needs a different `second_reviewer` and an `adjudication` note.

| Field | Allowed values or format |
|---|---|
| `id`, `action` | Unique ticket-local ID and a short action plus its object |
| `owner_side`, `owner_actor_id` | Owing side, with a source actor ID or `null` |
| `opening` | Ordered list of `{"message_id": "…", "quote": "…"}` evidence objects |
| `deadline` | `{"kind": "absent\|exact\|ambiguous", "at": null, "evidence": []}`. Supply an ISO 8601 timestamp with offset for `exact` and the literal cited time expression for `exact` or `ambiguous`. A date whose calendar or timezone is unresolved is `ambiguous`. Include an optional `note` explaining the interpretation. |
| `fulfillment` | `{"state": "open_observed\|claimed\|fulfilled\|cancelled\|superseded\|unknown", "evidence": []}`. Cite the confirming, claiming, cancellation, replacement or explicit still-owed message for a known state. Use `unknown` where the transcript stops before an outcome can be established. |
| `ambiguous` at ticket level | List of `{"kind": "identity\|ownership\|conditional\|other", "evidence": [], "note": "…"}` entries. Put the potentially commitment-bearing message first. A first message with unresolved identity or side is excluded from automated anchor scoring. |

After labeling, the owner records the source and provider basis and approved amount locally in a JSON object with `bundle_sha256`, `labels_sha256`, `route`, `external_inference` set to `true`, `approved_by`, `rights_and_provider_basis`, `max_usd`, and `estimate` copied from the estimate output. Hash the finished label file after the blind review. Keep prompts, model replies, saved proposals and transcripts under `.cache/real-world/`.

```bash
python -m duebook.bench.experiment real run "$root/vscode-baseline.json" \
  --labels "$root/vscode-baseline-annotations.json" --approval "$root/vscode-approval.json" \
  --max-usd "$approved_usd" --out "$root/vscode-run.json"
python -m duebook.bench.experiment real report "$root/vscode-baseline.json" \
  --labels "$root/vscode-baseline-annotations.json" --run "$root/vscode-run.json" \
  --out "$root/vscode-report.md"
```

The runner processes serially with the existing reader, escalation, verifier, reducer and per-call ledger. The ledger stops new calls after reported or priced charges reach the cap. One call already in flight, an internal retry, or usage missing after a timeout can still affect the final bill. Use the provider's account limit for a firm spend ceiling. An interrupted run leaves a journal with partial results and charges. A repeated attempt needs its own review of remaining spend before using a new output name. The report shows processing failures with their cohort and counts them as partial cards. Review evidence links for same-turn identity issues, possible restatements and role mapping mismatches before interpreting the anchor scores as semantic accuracy.
