# Results: event memory against three baselines

First real comparison of the method (structured event memory: a model
proposes per message, the reducer admits) with (a) the whole conversation in
the model's context, (b) a rolling summary and (c) Mem0, on simulated
long-running support tickets. Run on 2026-11-17. Raw records: `results/`.

## Short version

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

## What was run

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

## Test split (20 tickets)

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

## Long tickets (2 tickets, 210 and 200 messages)

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

## What happened on the dev split

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

## What the results say about the research questions

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

## Threats to validity

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

## Reproducing

```bash
pip install -e ".[dev,mem0]"
python -m duebook.bench --seed 1 --tickets 200                   # oracle self-check
python -m duebook.bench.experiment --split dev --max-usd 1.0     # tuning
python -m duebook.bench.experiment --split test --max-usd 2.5    # refuses to run twice
python -m duebook.bench.experiment --split long --max-usd 1.2
python -m duebook.bench.report results/test.json --long results/long.json
```

The rewritten ticket text is cached in `results/tickets/`, so a rerun reads
exactly the same words. `results/dev-1.json` and `results/dev-2.json` are the
two dev runs; `results/probe/long-unbounded.json` is the first measurement of
the extractor's prompt on a long ticket, made before the rewriter was fixed
(its text differs from `results/tickets/long.json`).
