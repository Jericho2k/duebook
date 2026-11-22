# Can we build Duebook’s real-support benchmark from these releases?

**No qualifying 100–300-message case was verified.** The original Mendeley v3 files contain genuine helpdesk activity. Four tickets clear 100 distinct released comment IDs, and two still clear 100 with readable, customer-visible requester and assignee comments alone. Those two are credible acquisition leads. The release stores curated fragments and does not supply the untouched comment bodies or independent proof of an end-to-end transcript. The CSDS release tops out at 79 dialogue entries. The CallCenterEN archive examined has word timings and empty speaker labels. [Mendeley v3](https://data.mendeley.com/datasets/btm76zndnt/3) · [CSDS author release](https://github.com/xiaolinAndy/CSDS) · [CallCenterEN author release](https://huggingface.co/datasets/AIxBlock/92k-real-world-call-center-scripts-english).

## Mendeley v3

Mohammad Abdellatif published this version on 3 August 2026. The author describes exports from a real international software company's helpdesk database. This assessment downloaded the versioned Mendeley archive and matched the SHA-256 of all 10 extracted files against the publisher index. The unmodified archive and extracted files live in the private workspace. [Published release and provenance](https://data.mendeley.com/datasets/btm76zndnt/3) · [Versioned publisher file index](https://data.mendeley.com/public-api/datasets/btm76zndnt/files?version=3&folder_id=root).

| File | Actual grain and fields used |
|---|---|
| `issues.csv` | One row per issue ID. Fields include `id`, `issue_reporter`, `issue_assignee`, `issue_type`, `issue_created`, `issue_resolution_date` and `issue_comments_count`. |
| `sample_utterances.csv` | One fragment per `(issueid, id, utr_seq)`. `id` identifies the parent comment. Fields include `comment_seq`, `author`, `created`, `is_private`, `actionbody` and `author_role`. |
| `issues_change_history.csv` | One change per history `id`. Columns are `issueid`, `field`, `value`, `created` and `change_group_id`, in addition to the record ID. Fields observed were `assignee` and `status`. |
| `issues_snapshot.csv` and `issues_snapshot_sample.xlsx` | Assignment-cycle and scored-sample artifacts. The 360 explicit issue IDs in the sample workbook reconcile with the text-bearing issue IDs. |

### Coverage and message counts

- **66,691 tickets** in the issue table and **360** with released text. The other 66,331 have no released transcript in v3. The 30,104 utterance rows represent **7,286 distinct original comment IDs** in the text sample.
- **327 tickets** have readable, customer-visible material from both a published requester and assignee. **0** have independently verified complete original transcripts. The author assigns `reporter` and `assignee` based on ticket roles. `others` stays unattributed.
- Of the 7,286 grouped comments, 1,597 are reporter, 3,116 are assignee, and 2,573 are other. The **5,559 readable, customer-visible** comments include **1,582 requester**, **2,686 assignee**, and **1,291 other** comments. There are 1,690 private comments and 37 visible comments with no alphanumeric text after removing placeholders.
- The sample has **265 distinct masked author IDs**. The full history has 257,508 changes, including 62,706 assignment and 194,802 status rows. It shows **24,130 direct handoffs on 16,473 tickets**, counting adjacent nonempty, different assignee values after sorting by timestamp and history ID. First assignments and transitions through an unassigned value are excluded.

| Population and unit | Min | Median | P75 | P90 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| Published count field, all 66,691 tickets | 0 | 5 | 10 | 18 | 27 | 61 | 685 |
| Distinct released comment IDs, 360 text-bearing tickets | 3 | 11 | 25.25 | 46.1 | 58 | 99.41 | 196 |
| Readable, customer-visible comments, same 360 tickets | 2 | 8 | 19 | 36 | 53 | 80.41 | 142 |
| Readable, customer-visible requester or assignee comments, same 360 | 1 | 6 | 14 | 29 | 43 | 60.82 | 118 |

| Population | ≥50 | ≥75 | ≥100 | ≥150 | ≥200 | ≥300 | 100–300 inclusive |
|---|---:|---:|---:|---:|---:|---:|---:|
| Published count field, all tickets | 1,029 | 436 | 212 | 74 | 30 | 7 | 205 |
| Distinct released comment IDs | 33 | 13 | 4 | 2 | 0 | 0 | 4 |
| Readable, customer-visible comments | 21 | 6 | 2 | 0 | 0 | 0 | 2 |
| Readable, customer-visible requester or assignee comments | 10 | 2 | 2 | 0 | 0 | 0 | 2 |

Percentiles use linear interpolation including both endpoints. The published count field gives coverage leads. Among the 360 joined tickets, the count of released comment IDs equals that field on three tickets, exceeds it by one on 356 and exceeds it by two on one. The reason for the offset is unverified. The metadata field cannot establish transcript completeness or support a missing-message calculation.

### The four tickets that passed the released-ID length screen

Aliases preserve the same descending order as the private candidate table, which carries the original masked ticket IDs. The private ticket manifest also records per-comment source IDs. The release has no individual issue URLs. Each case can be located in `sample_utterances.csv` by its `issueid` and joined to `issues_change_history.csv`. The counts below use only local reads of the released text. There was no filter for promises, workflow language, or elapsed time. [Versioned source files](https://data.mendeley.com/datasets/btm76zndnt/3).

| Case | Released IDs | Private | Readable visible | Requester / assignee / other, visible | Handoffs | Finding |
|---|---:|---:|---:|---:|---:|---|
| M1 | 196 | 54 | 142 | 40 / 62 / 40 | 1 | 102 comments from the two attributed sides, with 50 side transitions. Diagnostic language and requester follow-ups appear in the text. Original body, visibility context and thread completeness need confirmation. |
| M2 | 151 | 17 | 134 | 38 / 80 / 16 | 1 | 118 attributed comments and 61 side transitions. Requests, relative timing, diagnostic work and resolution language appear in the text. Original bodies and completeness need confirmation. |
| M3 | 120 | 32 | 88 | 41 / 10 / 37 | 1 | Diagnostic and follow-up language appears. The customer-visible view falls below 100. |
| M4 | 100 | 18 | 81 | 14 / 45 / 22 | 1 | Request and planned-action wording appears. The customer-visible view falls below 100. |

For this inspection, the script ordered comments by `comment_seq`, joined their fragments in `utr_seq` order, and searched the resulting bodies locally for request, follow-up, diagnostic, planned-action, timing, approval and resolution language. These are workflow indicators. They do not establish whether a statement was a promise, who owned it, or whether it was fulfilled. The script applied the same inspection to all four tickets that met the length screen.

### Fidelity, missing messages and privacy

- All 7,286 comment groups have a creation timestamp with an offset, an author mask and a role. Metadata is consistent within each comment ID. No ticket reverses creation time in published sequence order. Two tickets have gaps in comment sequence and two comment groups have gaps in fragment sequence.
- There are zero duplicate fragment keys. Nine excess comment IDs share a ticket, author, timestamp and assembled payload with another comment ID. These are repeat candidates and were retained as distinct source IDs. The release does not expose comment-body revision history, so the number of edited messages is unknown.
- No blank assembled body was observed. There are 23 blank fragment rows and 5,809 comment groups with placeholders. The author’s extraction code lowercases and sentence-tokenizes comments, removes adjacent repeated paragraphs, and replaces embedded email content. The source feature guide also describes masked people, technical objects, versions, links and logs. Rejoining fragments cannot recover removed content or its original boundaries. Masked versions and people can obscure what a promise refers to. The offset is known for each creation timestamp, while the customer's locale or business calendar can still be uncertain. [Author’s extractor](https://github.com/mohammadabdellatif/toward-performance-appraisal-automation/blob/main/ds_extractor/comments.py) · [Publisher feature guide available in the release](https://data.mendeley.com/datasets/btm76zndnt/3).
- Local pattern checks matched zero email-like, IPv4-like or international phone-format-like strings in the grouped bodies. These checks are narrow. The explicit private-comment flag, internal workflows and potential identifying context still matter for onward disclosure.
- The publisher lists **CC BY 4.0**. It permits reuse of the licensed material with attribution and appropriate change notices. Its grant excludes privacy and similar personality rights, and the publisher flags third-party content. I found no separate permission for sending the underlying company transcripts to a hosted inference provider. Provider handling is a separate question. OpenRouter documents endpoint-specific retention controls and an exception to no routine retention. DeepInfra publishes its handling policy for inference inputs and outputs. Neither establishes source permission for these records. [CC BY 4.0 legal code](https://creativecommons.org/licenses/by/4.0/legalcode.en) · [OpenRouter data handling](https://openrouter.ai/docs/guides/features/zdr) · [DeepInfra privacy policy](https://deepinfra.com/privacy).

## Alternative sources

| Source and scope | Verified qualifying | Fit, access and rights |
|---|---:|---|
| CSDS, all three author-hosted JSON splits | 0 | 10,701 unique sessions, all with readable `Q` and `A` entries. The released entries per session have min 7, median 24, P90 42, P99 61 and max 79. There are 442 at 50 or more and five at 75 or more. The original authors selected real JD customer-service conversations and released role and turn order. The JSON lacks message timestamps and agent identities. Masking includes numeric values, and the public files and paper did not supply an explicit license for third-party inference. [Author download](https://github.com/xiaolinAndy/CSDS#2-dataset-download) · [Paper and provenance](https://aclanthology.org/2021.emlp-main.365/) |
| Kled Full Support Conversations | 0 verified, actual count unknown | Catalog claims 5,000+ contributor-submitted chat or email threads and lists a commercial license. No public corpus or qualifying length histogram was available. Request a sample and license terms from Kled. Ask for structured message IDs, roles, timestamps, completeness evidence, count of qualifying threads and written processor permission before purchase or transfer. [Catalog and sample request](https://www.kled.ai/datasets/customer-support-conversations) |
| CallCenterEN, author’s general customer-service inbound archive | 0 of 1,217 inspected, other archives unmeasured | Authentic calls are reported by the authors. The downloaded archive has 2,594,674 timestamped ASR word records, all with empty speaker labels. The transcript is a text field and has no independently recoverable agent/customer turns in this archive. The authors withhold audio. CC BY-NC 4.0 applies. [Author files](https://huggingface.co/datasets/AIxBlock/92k-real-world-call-center-scripts-english/tree/main) · [Corpus paper](https://arxiv.org/abs/2507.02958) |
| JDDC 2.1 | 0 verified, actual count unknown | Authentic JD written support is a promising category. Authors require an application form and an organizational email. Lengths and external-processing rights were not verified. [Author application process](https://github.com/hrlinlp/jddc2.1#dataset-application) |
| UMass MSDialog-Complete | 0 verified, actual count unknown | The publisher requires an internal-research request with name, institution and country, followed by password access. The publisher’s generic agent flag covers every non-reporter, including peers. Authority would need checking via the affiliation field before qualifying any case. The published terms prohibit sharing the data with others, so provider transfer needs explicit clearance. [Release terms and access procedure](https://ciir.cs.umass.edu/downloads/msdialog/) |

No access requests were submitted. This assessment leaves the parked public TWCS and Jira cohorts in their existing benchmark roles on PR #28.

## Acquisition recommendation

There is no defensible evaluation cohort ready to run. Start with a **12-ticket exploratory pilot** once complete records and processing rights can be verified. Request two tickets per length band (100–149, 150–199, 200–300) from each of two support teams or channels. Draw within each band using a fixed seed and original message IDs. Keep cases with incomplete resolution in scope. A set of this size can expose data and evaluation failure modes. It will not give precise estimates of production performance.

The strongest path is a helpdesk partner that can provide a permissioned, de-identified ticket export with original message IDs, authors and roles, timestamps with offsets, visibility flags, and case history. Ask the Mendeley author whether the two screened cases can be independently checked against source-authorized original exports. The published release says the underlying database cannot be shared, so this route depends on specific approval. Kled is the strongest catalogued alternative to ask for a structured sample and a length audit before agreeing to terms.

## Reproduction and repository boundary

The standard-library assessment reads the saved originals and writes a metadata-only aggregate summary, a private 66,691-row ticket/exclusion manifest, and a private four-row length-screen table. It checks publisher hashes and joins by source ID. It never publishes transcript text.

```bash
python scripts/assess_support_sources.py PRIVATE_SOURCE_ROOT PRIVATE_NEW_OUTPUT_DIRECTORY
```

The source root contains `mendeley-v3/` with the original archive, extracted files and publisher JSON, `csds/` with the authors’ three splits, and `callcenteren/` with the original inbound ZIP. The [checked-in summary](../results/real-world/support-feasibility.json) records source hashes, conditional distributions, and excluded counts. The private manifest retains the original identifiers.

[PR #28](https://github.com/Jericho2k/duebook/pull/28) remains an open draft at `4053b349`. Its source hashing, freeze manifests, annotation format, estimate and approval gates, and reporting are reusable after a new cohort qualifies. Its Jira and GitHub assumptions do not define helpdesk message grain. [PR #29](https://github.com/Jericho2k/duebook/pull/29) is closed and unmerged. Its remote `codex/evaluator-v2` branch still points to `0d7eae64`. Evaluator V1 stays in place. The reference for a later approved run is the existing [Round 10 DeepSeek configuration](results.md#round-10-the-final-method), with its extraction, verification, reducer and lifecycle unchanged.

Gold labels, model runs and external transcript transfers were all zero.
