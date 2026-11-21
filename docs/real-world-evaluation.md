# Duebook real-world evaluation

T17 replays frozen public conversations through the existing event-memory benchmark and judges the card at the last observed event. The active cohort is public Jira. Public transcripts can omit private comments, deleted posts, work in other systems, and later outcomes.

## Public Jira intake

Atlassian, Red Hat and Apache all returned search results, comments and changelogs anonymously. Red Hat's legacy host redirects to Jira Cloud, where bounded search uses v3 and raw comments use v2. Three Atlassian and Red Hat cases qualified across two projects. Apache supplied the remaining cases needed for a draw with at least three projects. [Atlassian Jira](https://jira.atlassian.com) · [Red Hat Jira](https://redhat.atlassian.net) · [Apache Jira](https://issues.apache.org/jira).

The bounded frame retrieved up to 80 issues from each of 25 projects. Search requested Bug or Support Request and a recorded assignee change. Apache exposes Bug for this search. Watcher count bounded candidate retrieval. Selection ranks qualifying cases by inferred company comment count, with seed 7373 breaking ties.

The draw preserves at least three projects and picks the largest size up to ten within both estimator caps. For that size, it uses the fewest Apache cases possible before applying the company-comment ranking. It selected four Bugs from HBASE, HDFS and JBIDE. The five least expensive eligible issues already total $1.7881 under stress. [Jira manifest](../results/real-world/jira-intake.json).

| Intake step | Issues |
|---|---:|
| Search frame | 2,000 |
| Removed for type after the Jira search filter | 0 |
| Removed below 100 source comments | 1,965 |
| Removed for incomplete anonymous snapshots | 0 |
| Removed below 100 comments after the event cutoff | 0 |
| Removed for missing readable evidence from both inferred human sides | 0 |
| Removed without an assignee change in the retained events | 0 |
| Removed below the two-week span | 0 |
| Removed below 20 inferred company comments | 9 |
| Removed below 15% inferred company comments | 1 |
| Removed for exceeding either budget alone | 0 |
| Eligible | 25 |
| Selected | 4 |

The per-issue “+1-style” share measures comments with fewer than 30 characters after trimming surrounding whitespace and no question or undertaking marker. A question mark, common question openings, and short promise phrases are recognized with text rules. This is a low-signal proxy. Fifteen of 449 selected comments (3.3%) met it.

### Event mapping and text

Each public comment becomes a message in its original Jira text, with a link to the comment. Changelog assignee transitions become handoffs, including transitions to or from unassigned. Status transitions become status events. Source timestamps are normalized to UTC, and changelog changes precede comments at equal timestamps. Fix-version changes establish an actor's role.

Roles are inferred from the public snapshot. Bot accounts and automation names map to system. The reporter maps to customer. Other actors map to company if the snapshot lists them as the current or a historical assignee or as the author of a status or fix-version change. All other commenters map to customer. These are ticket roles inferred from activity, and employment status is unverified.

For longer issues, the first 300 combined comment, assignee and status events set the observation boundary. Role inference for a clipped issue uses assignments and changelog activity through that boundary. The case must still have at least 100 retained comments and a two-week span. All four selected issues fit without clipping.

The selected issues contain 449 comments, 10 handoffs and 15 status changes. A line heuristic flagged code or logs in 28 comments and 305 lines, or 18,857 of 299,203 UTF-8 bytes. Raw bodies stay in the private cache.

The existing estimator projects **$0.330708224 expected** (cap **$0.40**) and **$1.487613680 under stress** (cap **$1.50**). Eight issues at 100 readable comments each would cost at least $2.352 under stress before accounting for message length. Model spend is **$0**.

### Recreate the Jira intake

```bash
root=.cache/real-world/jira-redraw
python -m duebook.bench.experiment real jira-snapshot "$root/jira-snapshot.json" \
  --project atlassian:JRACLOUD --project atlassian:JRASERVER --project atlassian:CONFCLOUD \
  --project atlassian:CONFSERVER --project atlassian:JSDCLOUD --project atlassian:JSDSERVER \
  --project atlassian:BCLOUD --project atlassian:BSERV --project atlassian:BAM \
  --project redhat:JBIDE --project redhat:WFLY --project redhat:JBEAP --project redhat:JBAS \
  --project redhat:RESTEASY --project redhat:ENTESB --project redhat:TEIID --project redhat:QUARKUS \
  --project apache:KAFKA --project apache:HDFS --project apache:HBASE --project apache:SPARK \
  --project apache:LUCENE --project apache:OFBIZ --project apache:CASSANDRA --project apache:SOLR
python -m duebook.bench.experiment real prepare "$root/jira.json" \
  --jira-snapshot "$root/jira-snapshot.json" --manifest "$root/jira-intake.json"
python -m duebook.bench.experiment real estimate "$root/jira.json"
```

The snapshot and frozen bundle are private. The checked-in manifest records their SHA-256 digests and the draw's links and counts. Re-freezing from the saved snapshot reproduces the draw. Use a fresh directory to fetch current API content.

## Parked TWCS cohort

The TWCS artifacts and tooling are retained for reproducibility. TWCS labeling and inference are parked while Jira is the active cohort.

### Source and permitted use

This is a non-commercial academic evaluation for an HSE master's project. Thought Vector published TWCS on Kaggle with PointScrape credited. CC BY-NC-SA 4.0 permits non-commercial use with attribution. Raw messages and human labels stay local. The paid run sends text through OpenRouter to DeepInfra and requests zero data retention and no training use. The owner's local approval records the basis and spend limit. [TWCS data card](https://www.kaggle.com/datasets/thoughtvector/customer-support-on-twitter) · [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) · [OpenRouter ZDR](https://openrouter.ai/docs/guides/features/zdr).

### Frozen TWCS draw

Seed 7373 selected 30 threads and 342 messages across 21 brands. At most three selected threads feature any one brand. Of 798,197 reply components, 695,654 had fewer than six messages, 5,245 had fewer than two readable messages per side, and 52,432 had no company message with promise or time wording. These sequential cuts left 44,866 eligible threads. The re-freeze preserves the selected message IDs and records the revised assignment rule in the bundle hash. The separate VS Code intake remains archived for future work. [TWCS manifest](../results/real-world/twcs-intake.json) · [Archived VS Code manifest](../results/real-world/intake.json).

The TWCS `inbound` flag maps source accounts to the customer or company side. A first signed company reply assigns the ticket just before that reply. An unsigned company reply leaves the last known signer in place. A later reply with a different signer infers a handoff immediately before the reply. Eight threads have an initial assignment. Later handoffs are counted separately, and one Amazon thread accounts for most of them, with 19 of 30.

| Thread | Brand | Handoffs |
|---|---|---:|
| `twcs:179341` | AmazonHelp | 19 |
| `twcs:351844` | AmazonHelp | 6 |
| `twcs:2216690` | AmazonHelp | 2 |
| `twcs:2387854` | AzureSupport | 1 |
| `twcs:1116782` | British_Airways | 1 |
| `twcs:2788737` | VirginTrains | 1 |
| Remaining 24 threads | | 0 each |

The TWCS-only estimate is **$0.194044004 expected** and **$1.027915280 under stress**. The Part B cap is **$0.40**. Model spend to date is **$0**. The scenarios assume 1.1 and 2 calls per readable message, respectively, with 3,000 or 6,000 fixed and card input tokens plus twice the body size in UTF-8 bytes, and 150 or 1,500 output tokens per call. Both use uncached pricing of $0.14 input and $0.42 output per million tokens. [OpenRouter price listing](https://openrouter.ai/deepseek/deepseek-v4.1-flash).

### Label before running a model

From the PR checkout, using Python 3.12 with Duebook installed, serve the labeling page on loopback.

```bash
root=.cache/real-world
python scripts/label_twcs.py "$root/twcs.json" "$root/twcs-annotations.json"
```

Open [the local labeling page](http://127.0.0.1:8765). If the frozen files are in another checkout, pass their full paths. The review copy comes pre-filled with the blind `codex-gpt` draft. Enter your name, accept or correct the labels, and tick **Mark thread reviewed** yourself. Use **Save thread** before switching threads. The page validates each save and resumes at the first unfinished thread.

- Review before viewing any Duebook model output. Mark a thread reviewed even if it has no promises.
- Promises commit a side to an action. “I'll call you tomorrow,” “I'll send the screenshot tonight,” and “Our finance team will refund the overcharge” qualify. Delegated work is owed by the company.
- “The refund was sent” is a status report, “The email should arrive soon” is a prediction, and “Please send the screenshot” is a request. Use **Add ambiguity** for conditional offers and unresolved ownership or identity.
- For each distinct promise, enter a short action plus its object and choose the owing side. Select the earliest message and quote for the opening. Add later restatements as further evidence. For a signed agent, the optional owner ID has the form `account:INITIALS`.
- Highlight text in a message and use **Use selection** in the relevant quote row. You can also choose the message ID and enter the literal quote directly.
- Set the deadline to absent, ambiguous, or exact. For a stated deadline, quote the time phrase. An exact deadline needs an ISO 8601 timestamp with offset. Use ambiguous if the calendar or timezone is unresolved.
- Choose the observed outcome. Cite the message supporting a known outcome. A debtor's report supports claimed, and confirmation from the other side supports fulfilled. Use unknown when the outcome remains unclear at the end of the transcript.
- Start with the 10-thread owner review sample, drawn with Python's `random.Random(1710)` from the sorted frozen thread IDs. The paid-run approval gate uses the full reviewed sheet.

Keep the baseline `twcs-annotations-draft.json` and its `.sha256` sidecar together. The free comparison works during review and verifies the draft checksum.

```bash
python -m duebook.bench.experiment real compare-labels "$root/twcs.json" \
  --draft-labels "$root/twcs-annotations-draft.json" --labels "$root/twcs-annotations.json"
```

It lists added, removed and changed promises on threads you marked reviewed. Anchor agreement divides the number of shared opening-message IDs by their union, counting once for each promise. Labels on the same message are paired by preserved ID first. Edits to side, action, deadline or outcome can agree on the anchor. A sample without anchors has no agreement percentage.

### Reproduce the draw and estimate

Use a fresh local output directory to preserve the files used for labeling. The importer reads and hashes the CSV in one pass. A temporary disk database holds the text while the importer groups messages by in-file reply edges.

```bash
repro=$(mktemp -d)
python -m duebook.bench.experiment real prepare "$repro/twcs.json" \
  --twcs-csv /path/to/twcs.csv --manifest "$repro/twcs-intake.json"
python -m duebook.bench.experiment real estimate "$repro/twcs.json" | tee "$repro/twcs-estimate.json"
```

The checked-in manifest contains tweet and brand IDs, counts, and hashes. The local bundle contains raw text. Keep it with the labels and any eventual prompts, model replies, or saved proposals under `.cache/real-world/`.

### Parked TWCS run procedure

Start after the owner confirms blind labeling is complete and signs off on inference. The local approval JSON records `bundle_sha256`, `labels_sha256`, `route`, `external_inference` set to `true`, `approved_by`, `rights_and_provider_basis`, `max_usd`, and `estimate` copied from the estimate output. Use the hash of the final label file.

```bash
python -m duebook.bench.experiment real run "$root/twcs.json" \
  --labels "$root/twcs-annotations.json" --approval "$root/twcs-approval.json" \
  --max-usd 0.40 --out "$root/twcs-run.json"
python -m duebook.bench.experiment real report "$root/twcs.json" \
  --labels "$root/twcs-annotations.json" --run "$root/twcs-run.json" \
  --draft-labels "$root/twcs-annotations-draft.json" \
  --out "$root/twcs-report.md"
```

The runner uses the existing reader, escalation, verifier, reducer, and per-call ledger. The ledger stops new calls once charges reach the cap. An in-flight call, internal retry, or missing usage after a timeout can affect the final bill. An interrupted run leaves a journal with partial results and charges. Review remaining spend before retrying.

Use the synthetic benchmark's source-message and owing-side matching and one-minute deadline tolerance. Check the action of every matched promise by hand. Report judgeable deadlines and outcomes with their denominators, false closes, false alerts for deadlines that pass within the observation window, and inheritance after handoffs. The public transcript can leave an outcome unknown. Report every missing or false item with its source and the stage where the error occurred. Include the owner's review count, promise-anchor agreement with the blind draft, and counts of labels added, removed and changed. Keep the English, public-channel, 30-thread sample and the labeling process in view when interpreting the results. [Historical synthetic test5 record](../results/round10/test5.json).
