# Duebook real-world evaluation

Source assessment as of 21 November 2026. The benchmark will replay authentic public messages in event order and judge the card after the last observed message. A public transcript may omit direct messages, deleted messages, work in other systems, and anything that happened after that boundary.

## Sources and permitted use

| Source | Provenance and format | Decision for external inference |
|---|---|---|
| Customer Support on Twitter (TWCS) | Thought Vector published version 10 on Kaggle. The publisher credits PointScrape and describes a mostly English corpus of consumer and company replies. `twcs.csv` supplies `tweet_id`, `author_id`, `inbound`, `created_at`, `text`, `response_tweet_id`, and `in_response_to_tweet_id`. IDs and mentions are anonymized and some sensitive strings are masked by the publisher. IDs point into the corpus. | **Uncleared.** Kaggle's metadata lists CC BY-NC-SA 4.0. The primary purpose of a product evaluation may be commercial even if the test is internal. The uploader's listing also leaves the scope of rights in the original posts unresolved. X's developer rules restrict redistribution of hydrated API content and expressly restrict model training. This evaluation is inference only, and today's developer rules do not establish the original collector's agreement or Duebook's rights in the archive. Keep TWCS outside the external run until the intended purpose, transfer and onward handling have documented permission. |
| Public GitHub technical issues | GitHub REST JSON preserves issue and comment IDs, raw Markdown body, created/updated timestamps, author type and association, plus links. The initial frame uses `microsoft/vscode` bug reports. Repository participants may include end users, maintainers, community helpers and bots. This is a technical issue-tracker proxy for support, with less evidence of business deadlines and handoffs than a helpdesk. | **Uncleared for transfer.** GitHub's terms D.5 grant other users rights through the service. D.6 supplies a stronger basis for contributions to an explicitly licensed repository, subject to separately applicable terms. VS Code publishes an MIT license and contribution guidance. Confirm that these cover the issue descriptions and comments in this draw, including material embedded by their authors, and that GitHub's use terms and the provider terms allow the exact transfer. GitHub's D.8 separately addresses automated access for developing or training commercial AI systems. The pilot calls an existing model for inference and evaluates Duebook's application behavior. |

- [Kaggle data card](https://www.kaggle.com/datasets/thoughtvector/customer-support-on-twitter) · [Kaggle publisher metadata API](https://www.kaggle.com/api/v1/datasets/view/thoughtvector/customer-support-on-twitter) · [Kaggle Terms of Use](https://www.kaggle.com/terms) · [CC BY-NC-SA legal code](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en) · [X Developer Policy](https://docs.x.com/developer-terms/policy) · [X restricted uses](https://docs.x.com/developer-terms/restricted-use-cases) · [TWEETSUMM paper](https://aclanthology.org/2021.findings-emnlp.24/).
- [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service) · [Issues REST fields](https://docs.github.com/en/rest/issues/issues?apiVersion=2022-11-28) · [Comments REST fields](https://docs.github.com/en/rest/issues/comments?apiVersion=2022-11-28) · [VS Code license](https://github.com/microsoft/vscode/blob/main/LICENSE.txt) · [VS Code contribution guide](https://github.com/microsoft/vscode/wiki/How-to-Contribute).

The existing production-like route is DeepSeek V4.1 Flash via OpenRouter and DeepInfra FP8, with provider fallback disabled. Verify data and contract coverage for both entities, including input rights, retention, training, any personal data in raw text, and any applicable attribution. The evaluation request uses routing with zero data retention and data collection denied. Review account prompt logging and require the ZDR-only setting that excludes Safety Retention endpoints separately. Source-content rights require their own review alongside provider handling. [OpenRouter ZDR](https://openrouter.ai/docs/guides/features/zdr) · [OpenRouter providers](https://openrouter.ai/Providers) · [DeepInfra model and retention statement](https://deepinfra.com/blog/deepseek-v4-1-flash-deepinfra) · [OpenRouter data processing agreement](https://openrouter.ai/assets/data-processing-agreement.pdf).

## Method

1. Freeze and hash the source snapshot and draw before labeling. Target 40 seeded random threads without replacement and 10 disjoint long-running cases, selected from the remainder by observed span and length. Report by source and cohort. The random inference population is the eligible snapshot frame, with public threads and both mapped sides. The random inference population is narrower than the overall support population. Publish the frame, denominators and exclusions without transcript text.
2. Preserve the source body verbatim. Keep the issue title, edit timestamp, source links, reply edges, actor association and normalization rules in the local record. The title is metadata and the engine receives the issue body as posted. The UTC conversion represents the original instant. Resolve equal timestamps by stable source ID with the issue opener before comments. This tie break is inferred. Cases without enough text, timestamp or role data to replay are ineligible. Run through the existing event-memory bench and use the last observed timestamp, with UTC as an evaluation calendar assumption. A timestamp offset does not establish a speaker's local time or business calendar.
3. `inbound` provides the TWCS side mapping at account level. It establishes neither an individual representative nor a handoff. GitHub author associations and bot indicators are observed metadata. The issue reporter maps to customer for the whole thread. Other OWNER, MEMBER and COLLABORATOR comments map to agent. Other human comments map to customer as a proxy for participants outside the project team. These issue-tracker roles are inferred, can be wrong, and can affect both binding and verification. A bot retains its observed role. Author turnover, issue assignment and issue closure provide no handoff or fulfillment event in this pilot.
4. Label from the transcript before exposing any predictions. Record each distinct undertaking once, including the action and object, the canonical first source, the owing side and actor where known, time expression and certainty, and transcript-visible fulfillment and supporting messages. Keep requests, vague plans, conditional offers, and ambiguous identity or ownership distinguishable from definite unconditional commitments. Require an independent second read on the challenge cases and record adjudication for disagreements. Record censored outcomes as unknown where appropriate. A debtor's claim is evidence of a claim. Issue closure, thanking someone, an incomplete public exchange, or silence provide no confirmation on their own.
5. Use the synthetic benchmark's source-message and owing-side match and one-minute deadline tolerance. This measures anchored detection. A reviewer must inspect identity where several actions share a post, a commitment is restated later, or a predicted action differs despite a matching anchor. Use only judgeable deadlines and statuses and show their denominators. Claimed fulfillment and unknown transcript outcomes need separate treatment from the synthetic status categories. Handoff, breach-alert, factual freshness, and synthetic hard-case metrics have no matching gold in this pilot. Historical cost and latency can be shown with configurations and units, with cache, host, language and run design differences visible.

## Initial intake results

The [source manifest](../results/real-world/intake.json) records the 50 selected issue URLs, source and bundle SHA-256 digests, and their positions in each cohort. The source frame contains all 154 results returned by the frozen GitHub search. Of these, 92 were eligible, 61 lacked nonblank text from both mapped human sides, and one lacked a message body. Thirteen of the eligible threads left after the random draw met the long-case thresholds. The challenge group takes the longest ten of those. The raw snapshot, normalized bundle and annotation sheet are local, under `.cache/real-world/`.

| Cohort | Threads | Messages | Messages per thread, min / median / max | Span in days, min / median / max | Inference estimate, expected / stress |
|---|---:|---:|---:|---:|---:|
| Random | 40 | 291 | 3 / 5.5 / 53 | 0.09 / 37.64 / 658.53 | $0.20 / $0.93 |
| Long-running challenge | 10 | 131 | 9 / 12 / 25 | 27.77 / 122.93 / 649.87 | $0.11 / $0.45 |
| Total for budgeting | 50 | 422 | — | — | $0.30 / $1.38 |

All 422 engine event bodies, source creation times and author IDs were checked against the corresponding REST records. All 50 threads completed a local empty-proposal fold through the existing engine. The fold served as an import check and made zero model calls. The GitHub API snapshot contains current bodies. Three comments have an update timestamp later than their own thread's last creation time, and 45 issue records have such an update. An issue update can concern metadata. The replay supplies the bodies captured in the snapshot and so cannot establish what someone would have seen at each historical point.

Three observed cases establish annotation boundaries before any model output exists.

- **Reporter confirmation versus fix claim.** In a randomly selected testing-performance issue, a maintainer says changes were merged and asks the reporter to try an Insiders build. Record the claimed change and the request to test as separate observations. The statement alone supplies no reporter confirmation. [Maintainer comment](https://github.com/microsoft/vscode/issues/238241#issuecomment-6733157647).
- **Administrative closure.** A randomly selected installation/access issue includes network details submitted by participants, then a bot message closing the thread after a request for information. The bot's stated reason provides no evidence that access was restored. The raw logs and submitted network details also need review before this snapshot can be shared with another processor. [Request for diagnostics](https://github.com/microsoft/vscode/issues/237408#issuecomment-3948267125) · [Bot closure](https://github.com/microsoft/vscode/issues/237408#issuecomment-3990454063).
- **Short confirmation with context.** In a challenge issue, a bot asks for a specific verification response after announcing a fix, and the reporter replies with that marker. The context supports interpreting the marker as a verification signal for the terminal issue. A commitment or deadline would require its own evidence. One technical helper also appears as a GitHub `CONTRIBUTOR`, showing where the role proxy can understate a participant's support role. [Technical helper](https://github.com/microsoft/vscode/issues/237900#issuecomment-2619546222) · [Verification request](https://github.com/microsoft/vscode/issues/237900#issuecomment-2686488254) · [Reporter reply](https://github.com/microsoft/vscode/issues/237900#issuecomment-2694068914).

The expected cost assumes 1.1 calls per nonblank message at 3,000 fixed and card input tokens plus twice the current body size in UTF-8 bytes, and 150 output tokens per call. The stress scenario uses 2 calls, 6,000 fixed and card tokens, and 1,500 output tokens. Both use uncached pricing of $0.14 input and $0.42 output per million tokens. Verifier calls, retries, a larger card and long preceding messages can take actual cost above that stress scenario. Actual charges remain subject to provider billing. [OpenRouter price listing](https://openrouter.ai/deepseek/deepseek-v4.1-flash).

The historical [synthetic `test5` record](../results/round10/test5.json) has 20 tickets with end-anchor precision 0.998, recall 1.000 and F1 0.999 (463 TP, 1 FP, 0 FN). It recorded 3,766 model calls, $0.3410 in usage and 5,478.0 summed model seconds. These are the historical values for later context. The real set has no independently reviewed labels or live model run yet. Actual real accuracy, model errors, model latency, and changes versus synthetic are therefore unavailable. External charge to date is $0.

## Reproduce the draw and prepare labels

Use Python 3.12 for the frozen draw. A fresh API retrieval may change as issue bodies, comments and search membership change. Retain the raw frame and comment arrays locally to reproduce the exact intake hashes. Fetching independently can reproduce the method. The selected URLs and snapshot digests identify the intake described above.

```bash
set -o pipefail
root=.cache/real-world
mkdir -p "$root/vscode-comments"
query='repo:microsoft/vscode is:issue label:bug created:2025-01-01..2025-01-31 comments:>=2'
gh api --paginate --method GET search/issues -f "q=$query" -f sort=created \
  -f order=asc -F per_page=100 --jq '.' | jq -s '.' > "$root/vscode-frame.json"
for n in $(jq -r '.[].items[].number' "$root/vscode-frame.json"); do
  gh api --paginate -H 'Accept: application/vnd.github.raw+json' \
    "repos/microsoft/vscode/issues/$n/comments?per_page=100" --jq '.[]' \
    | jq -s '.' > "$root/vscode-comments/$n.json" || exit 1
done
python -m duebook.bench.experiment real prepare "$root/vscode-baseline.json" \
  --github-frame "$root/vscode-frame.json" --github-comments "$root/vscode-comments"
python -m duebook.bench.experiment real estimate "$root/vscode-baseline.json" \
  > "$root/vscode-estimate.json"
python -m duebook.bench.experiment real annotations "$root/vscode-baseline.json" \
  --out "$root/vscode-annotations.json"
```

The adapter verifies frame completeness and issue/comment identity and counts. The search frame uses repository, label, creation window and comment count. A thread must have usable observed text from both mapped human sides and usable message times. The draw sorts eligible IDs and samples with a seeded local PRNG. From the remainder it selects cases with at least eight messages across seven days, ranked by span, then message count and ID. The random draw can itself contain long cases. `--exclude exclusions.json` takes a local JSON object mapping whole case IDs to documented reasons. Apply rights or data-scope exclusions to the frame and freeze a new draw before annotating the revised cohort. Leave the evidence text intact.

For TWCS, when its source use is cleared, pass `--twcs-csv /path/to/twcs.csv` in place of the GitHub arguments. The importer forms connected components of in-file reply edges in either direction, including branches. It keeps unresolved reply references on their source messages. The importer uses the frozen archive and its in-file links as the sole evidence for the replay. Its full CSV path holds the reply graph and rows in memory and requires several GB of working memory. The pilot verified this adapter with test rows only.

The `annotations` command writes one blank entry per frozen case, with its `ticket_id` and the bundle hash. Read the ordered `messages` array and use each message's source `id`, plus a literal substring, for evidence. A reviewed ticket with no definite commitments is valid. An empty, pending entry is unreviewed. Each reviewed challenge entry needs a different `second_reviewer` plus an `adjudication` note. Record a stable local ID for each distinct commitment and use the earliest observed undertaking first in `opening`. Add subsequent restatements as further `opening` evidence. Fill `owner_side` with `customer` or `company` and use the observed `owner_actor_id` when supported, otherwise `null`. Use the speaker's promise in context, with the following field structure.

| Field | Allowed values or format |
|---|---|
| `id`, `action` | Unique ticket-local ID and a short action plus its object |
| `owner_side`, `owner_actor_id` | Owing side, with a source actor ID or `null` |
| `opening` | Ordered list of `{"message_id": "…", "quote": "…"}` evidence objects |
| `deadline` | `{"kind": "absent\|exact\|ambiguous", "at": null, "evidence": []}`. Supply an ISO 8601 timestamp with offset for `exact` and the literal cited time expression for `exact` or `ambiguous`. A date whose calendar or timezone is unresolved is `ambiguous`. Include an optional `note` explaining the interpretation. |
| `fulfillment` | `{"state": "open_observed\|claimed\|fulfilled\|cancelled\|superseded\|unknown", "evidence": []}`. Cite the confirming, claiming, cancellation, replacement or explicit still-owed message for a known state. Use `unknown` where the transcript stops before an outcome can be established. |
| `ambiguous` at ticket level | List of `{"kind": "identity\|ownership\|conditional\|other", "evidence": [], "note": "…"}` entries. Put the potentially commitment-bearing message first. A first message with unresolved identity or side is excluded from automated anchor scoring. |

Once the source and provider are cleared and the amount approved, record that decision locally in a JSON object with `bundle_sha256`, `labels_sha256`, `route`, `external_inference` set to `true`, `approved_by`, `rights_and_provider_basis`, `max_usd`, and `estimate` copied from the estimate output. Include the applicable terms or grants and relevant provider/account settings in the basis. Hash the finished label file after the blind review. Only code and link-level intake summaries belong in Git. Keep raw prompts, model replies, saved proposals and transcripts in the private workspace.

```bash
python -m duebook.bench.experiment real run "$root/vscode-baseline.json" \
  --labels "$root/vscode-annotations.json" --approval "$root/vscode-approval.json" \
  --max-usd "$approved_usd" --out "$root/vscode-run.json"
python -m duebook.bench.experiment real report "$root/vscode-baseline.json" \
  --labels "$root/vscode-annotations.json" --run "$root/vscode-run.json" \
  --out "$root/vscode-report.md"
```

The runner processes serially with the existing reader, escalation, verifier, reducer and per-call ledger. The ledger stops new calls after reported or priced charges reach the cap. One call already in flight, an internal retry, or usage missing after a timeout can still affect the final bill. Use the provider's account limit for a firm spend ceiling. An interrupted run leaves a journal with partial results and charges. A repeated attempt needs its own review of remaining spend before using a new output name. The report shows processing failures with their cohort and counts them as partial cards. Review evidence links for same-turn identity issues, possible restatements and role mapping mismatches before interpreting the anchor scores as semantic accuracy.
