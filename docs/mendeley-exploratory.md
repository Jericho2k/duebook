# Mendeley v3 exploratory evaluation

Status as of 2026-11-22. Preparation only. Actual external model calls **0**.
Actual inference spend **$0**. This extends the real-data harness in
[PR #28](https://github.com/Jericho2k/duebook/pull/28) using the two private
candidates from [PR #30](https://github.com/Jericho2k/duebook/pull/30).

## What is included

| Alias | Visible release | Reporter | Assignee | Uncertain `others` withheld | Private excluded | Prospective input | Direct handoffs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| M1 | 142 | 40 | 62 | 40 | 54 | 102 | 1 |
| M2 | 134 | 38 | 80 | 16 | 17 | 118 | 1 |

Both are included in the proposed **known-side exploratory subset**. The local
review transcript keeps all 276 visible comments. The release is a curated,
masked dataset of real support exchanges. The publisher split and altered the
wording, including lowercasing and placeholder substitution. Attachment
references, call summaries and anonymized identifiers remain where published.
Original attachments, oral conversations, complete original wording and the
completeness of the original ticket are unverified. The frozen study does not
contact authors or companies. [Help Desk Tickets v3](https://doi.org/10.17632/btm76zndnt.3)

Reconstruction matches the original private manifest's full ID set for each
ticket. It joins fragments within the same comment with a single separator,
keeps the published comment ID, author mask, role and UTC timestamp, and orders
comments by time and published comment sequence. It preserves same-text
duplicates with distinct source IDs. Reporter and assignee role labels must
agree with the case's reporter and assignment history. `others` remains
explicit in the local transcript. The product has no neutral participant side,
so those comments are withheld from inference and V1 matchable anchors.
Workflow history contributes observed assignments and status transitions within
the visible observation window. Initial status and repeated status values are
omitted, and the first assignment is distinct from a transfer. Ticket status
alone does not establish fulfillment of a specific promise.

## Provisional labels and audit

The Codex draft was made from the release and the label guide before any
predictions for these cases. It has 10 V1 commitments for M1 and 13 for M2, and
9 ambiguous entries across both. A separate evidence file holds 8 grouped
audit records with quotations and source IDs, including unscored candidates
from unknown-side comments, possible restatements, renegotiations and outcomes
that depend on off-ticket work. Both cases are pending owner review. Exact
deadlines are left unresolved where a local timezone, working calendar or
precise cutoff is missing. Multiple actions in one message have separate
records. V1's same-anchor identity and excluded-field rules stay in effect.
The audit preserves distinctions V1 cannot score.

| Frozen artifact | SHA-256 |
| --- | --- |
| Prospective bundle | `388557000fde39fa070be2dceef473359d01a5f8edfc0bf8611459759ca9591b` |
| Complete visible source review | `8589015987e77cd50749b93c86ebe8131433bf7ed0e6ef4849a5d937ba2a2a59` |
| Provisional V1 draft | `42aef1bd3a7f5592d58c80808a77bfd4ec461893c21ae872196366307c4ca8cc` |
| Supplementary evidence | `0b440345c5031a0a50793567fb5fc7b224e21280d4921f5c166ae1c47a6f6491` |

The bundle records the release CSV and private candidate manifest fingerprints.
The private `SHA256SUMS` also covers the candidate selection and estimate.
Transcripts, source ticket IDs, annotations, prospective payloads, review
copies and all future run results remain in ignored local storage.

## Review and reproduce locally

Use the preparation checkout with Duebook's benchmark dependencies installed.
The example assumes the files are under `.cache/real-world` in the checkout that
holds the private PR #30 source data. Set `DUEBOOK_REAL_DATA_DIR` to that
directory's absolute path if the branch is in a separate worktree.

```sh
data="${DUEBOOK_REAL_DATA_DIR:-$PWD/.cache/real-world}"
p="$data/mendeley-exploratory"
(cd "$p" && shasum -a 256 -c SHA256SUMS)
PYTHONPATH=src python scripts/label_twcs.py "$p/bundle.json" \
  "$p/annotations-reviewed.json" --source-review "$p/source-review.json"
```

Open [the local editor](http://127.0.0.1:8765). It shows the redacted evaluation
text for eligible comments and marks the remaining comments as review context
only. The frozen full-source JSON keeps the publisher's released text. Only
the editable label copy is changed by the editor. Review the separate
`annotation-evidence.json` alongside it.

```sh
umask 077
tmp=$(mktemp -d)
PYTHONPATH=src python -m duebook.bench.experiment real prepare-mendeley \
  "$data/support-feasibility/mendeley-v3" \
  "$data/support-feasibility/final/mendeley-ticket-manifest.jsonl" \
  "$p/selection.json" "$tmp/bundle.json" "$tmp/source-review.json"
cmp "$p/bundle.json" "$tmp/bundle.json"
cmp "$p/source-review.json" "$tmp/source-review.json"
PYTHONPATH=src python -m duebook.bench.experiment real offline "$p/bundle.json" \
  --labels "$p/annotations-draft.json" --out "$tmp/offline.json"
PYTHONPATH=src python -m duebook.bench.experiment real compare-labels "$p/bundle.json" \
  --labels "$p/annotations-reviewed.json" --draft-labels "$p/annotations-draft.json"
```

Both reconstruction outputs reproduced byte for byte. The empty-proposal fold
validated all evidence quotations used by V1. It emitted 114 event records for
M1 and 127 for M2, with one handoff and one end checkpoint each. The V1 empty
control counted 10 and 13 misses, respectively. These are fixture checks.
The product engine, extraction prompts and schemas, verifier, score definitions
and historical results are untouched. This cohort selects the existing
closing-rule version 5 and the original Round 10 work calendar (Europe/Moscow)
in the benchmark. The calendar and UTC source timestamps do not resolve a
local clock phrase stated in a masked conversation.

## Route, privacy and budget

The reference is [Round 10](results.md#round-10-the-final-method), `event_memory` with
`deepseek/deepseek-v4.1-flash` through OpenRouter, pinned to DeepInfra FP8.
The reader uses temperature 0, reasoning off and an 800-token answer cap.
The existing unreadable-answer path uses low reasoning with up to 4,000
tokens, and the existing low-reasoning verifier checks closing proposals.
Extraction is incremental and uses the previous opposite-side public turn
and current card. Labels are evaluated locally. The existing real-data
transport requires `zdr: true` and `data_collection: deny` on extraction,
escalation and verification. The route has no provider fallback.

The local screen combined a manual read of both visible transcripts and
pattern checks on the prospective input for emails, URLs, IPv4 addresses,
international-format phone numbers and credential assignments. It found two
additional direct name/location matches in M1. Both were redacted in the
bundle. The released actor masks, exact dates and technical descriptions can
still permit linkage or expose business-sensitive context. Private notes and
uncertain-role text are excluded. The raw released text stays local.

The publisher identifies the work as CC BY 4.0. Attribution, a link to the
license and disclosure of changes are required when sharing the licensed
material. The license does not establish clearance for any underlying personal
data or third-party rights. Confirm a basis for sending the redacted,
pseudonymous payload to the router and host. Confirm that the exact FP8
endpoint is eligible for ZDR and collection-deny with the intended account,
and that its applicable terms and processing location fit that basis.
OpenRouter documents in-flight safety screening, potential retention of
flagged traffic and routine metadata collection. DeepInfra's terms describe
retention exceptions. Endpoint eligibility and the applicability of those
terms to this routed request remain unverified. [Release](https://data.mendeley.com/datasets/btm76zndnt/3),
[license](https://creativecommons.org/licenses/by/4.0/),
[OpenRouter data handling](https://openrouter.ai/docs/guides/privacy/data-collection),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[DeepInfra terms](https://deepinfra.com/terms)

| Existing estimator | USD |
| --- | ---: |
| Expected (220 messages) | 0.125435464 |
| Stress | 0.662344480 |
| Proposed maximum across all attempts | 0.75 |

Stress is the documented high-use estimate. Larger cards, retries, failures
or rate changes can exceed it. A strict billing worst case has no verified
bound from the client, which meters posted usage before the next call.
Require a dedicated key restricted to the route with a fresh, non-resetting
provider-side cap of $0.75 or less and confirm its treatment of in-flight
charges before proceeding. Stop if that maximum cannot be enforced.
[OpenRouter spend controls](https://openrouter.ai/blog/tutorials/team-spend-controls-setup/)

## Future execution after explicit approval

Owner review and separate authorization are required first. The private
approval record must bind `bundle_sha256`, `labels_sha256` (the owner-reviewed
file), `route`, `estimate` (the exact JSON object from `estimate.json`),
`max_usd`, `approved_by`, `rights_and_provider_basis` and
`external_inference: true`. No approval file or provider key is provisioned
by preparation. Present this prepared version and hashes to the owner before
any provider request. If the input is changed, re-freeze, relabel affected
evidence and seek approval for the new hashes.

After approval, with the capped key provided securely as
`DUEBOOK_OPENROUTER_API_KEY` and `p` as above, the exact commands are:

```sh
umask 077
(cd "$p" && shasum -a 256 -c SHA256SUMS)
PYTHONPATH=src python -m duebook.bench.experiment real run "$p/bundle.json" \
  --labels "$p/annotations-reviewed.json" --approval "$p/owner-approval.json" \
  --max-usd 0.75 --out "$p/run-01.json"
PYTHONPATH=src python -m duebook.bench.experiment real report "$p/bundle.json" \
  --labels "$p/annotations-reviewed.json" --draft-labels "$p/annotations-draft.json" \
  --run "$p/run-01.json" --out "$p/report-01.md"
```

`run-01.json` binds the inputs and contains ordered proposals with verifier
verdicts, per-event reducer rejections including partial admissions, resulting
cards and source event IDs, V1 snapshots, token counts, model and wall latency,
provider and cost records. `run-01.journal.jsonl` records each case when its
attempt finishes. Inspect the existing files after a cap or failure. Complete
journal lines can be read locally, and the fold of stored proposals needs no
model. Abrupt interruption can lose the in-flight case and some provider charges
may land after the stop. Preserve those partial records, reconcile the provider
ledger and get separate approval for any retry under the remaining aggregate
budget and a fresh output name. The CLI refuses to reuse the original run name.

After inference this setup can examine concrete extraction, ownership, closing,
handoff and evidence failures in these two conversations, and describe measured
cost and latency. Selection by length and readability, omitted roles and
source context, one help desk, early handoffs and provisional annotations limit
any comparison with the synthetic benchmark. It cannot establish general
accuracy across customers or companies, production readiness, or performance
on complete original tickets.
