# Competitors: memory systems a reviewer would expect to see compared

RQ3 asks whether structured event memory tracks commitments better than
summarisation **and existing LLM-memory solutions**. Until round 3 the only
memory system compared was Mem0 2.2.1. This page surveys the rest of the
niche, decides which systems can be run as baselines here, and says which
baseline stands in for the closed helpdesk copilots that cannot be run.

Checked on 2026-11-17 against primary sources: the GitHub repositories, the
PyPI metadata of the released packages, and for Graphiti and LangMem the
released source itself. arxiv.org and the vendors' help centres were not
reachable from the build container, so papers are cited by arXiv id and the
helpdesk copilots by the documentation already cited in
`docs/report/01-obzor.md` (refs [3]–[6]), not re-read today.

## Summary

| System | Open source, licence | Stores | Time / validity | Infrastructure | OpenAI-compatible LLM | Status | Baseline here |
|---|---|---|---|---|---|---|---|
| **Graphiti** (Zep's engine) | yes, Apache-2.0 | entities, facts (edges), episodes | **bi-temporal**: `valid_at`, `invalid_at`, `expired_at`; a contradicted fact is invalidated, not deleted | graph DB: Neo4j, FalkorDB (embedded **FalkorDB Lite**), Neptune; Kuzu deprecated | yes, `OpenAIGenericClient` | active, 0.31.2 (2026-10-20) | **implemented** (`graphiti`) |
| Zep Cloud | no, hosted | the same, managed | the same | hosted | n/a | active | approximated by Graphiti |
| Mem0 | yes, Apache-2.0 | extracted memories | none; the model chooses ADD/UPDATE/DELETE | vector store, embeddings | yes | active, 2.2.1 | already compared |
| Letta (MemGPT) | yes, Apache-2.0 | editable memory blocks in context, archival store | none documented | the V1 server is retired; current code is the `letta-code` terminal agent | — | repurposed | not run, see below |
| LangMem | yes, MIT | memories in a LangGraph store | none in source or README | LangGraph store, embeddings | via LangChain | 0.0.30 (2025-10-27) | not run: Mem0's design again |
| Cognee | yes, Apache-2.0 | document chunks → entity/relation graph + vectors | none documented | graph + vector + relational stores | via its LLM config | active, 1.6.8 | not run: document memory |
| A-MEM | yes, MIT | Zettelkasten notes with links | a creation timestamp only | ChromaDB, sentence-transformers | OpenAI or Ollama backends | research prototype | not run |
| MemoryOS (BAI-LAB) | yes, Apache-2.0 | short/mid/long-term pages, persona | page timestamps; no supersession | local embeddings, optional ChromaDB | yes, `openai_base_url` | research artifact | not run |
| MemOS (MemTensor) | yes, Apache-2.0 | "MemCubes" in a graph | "replacing memories over time"; no intervals documented | Neo4j + Qdrant (docker compose) | not documented | active | not run: heavy, Docker |
| Supermemory | yes, MIT (local binary); hosted API | facts, profiles, documents | "handles knowledge updates and contradictions, forgets expired information" | local binary or hosted | yes for LLM | active | not run |
| Hindsight | yes, MIT | world facts, experiences, observations, mental models | event timestamp on `retain`, time-range recall | PostgreSQL + pgvector (embedded pg0) | yes ("any OpenAI-compatible endpoint") | active | best next candidate |
| Memobase, MIRIX, Memori, MemU | open SDKs; servers on Postgres/Redis; Memori's engine hosted | user profiles, events, typed memories | timestamps at most | Postgres, Redis, Docker | partly | active | not run: user-profile memory |

## Systems

### Graphiti and Zep — the closest competitor

[getzep/graphiti](https://github.com/getzep/graphiti), Apache-2.0,
`graphiti-core` 0.31.2. The paper is *Zep: A Temporal Knowledge Graph
Architecture for Agent Memory* (arXiv:2501.13956). Zep Cloud is the hosted
product built on it; the [getzep/zep](https://github.com/getzep/zep)
repository says it is "not Zep's product or service" and points to Graphiti
as the open-source engine.

- **Stores** episodes (the raw message, kept for provenance), entities with
  evolving summaries, and facts: edges `entity → relation → entity` with a
  natural-language `fact`.
- **Time.** Every fact carries `valid_at` and `invalid_at` (when it was true
  in the world) and `created_at` / `expired_at` (when the graph learnt and
  retired it). Each episode is added with its own `reference_time`, and the
  extraction prompt resolves relative times against it. When a new fact
  contradicts an old one, a model call names the contradicted facts and code
  closes their interval (`resolve_edge_contradictions`). This is the nearest
  thing in the field to our supersession (RQ2).
- **What it does not model.** Contradiction is decided by the model, and
  "the customer says X, the company says Y" is not separated from "the
  customer changed X": the newer fact invalidates the older in both cases.
  The released prompt says outright that "soft deadlines are not
  `invalid_at` values" and that for a promise `valid_at` is the time it was
  made: a deadline is free text inside the fact. There is no owner, status or
  handoff.
- **Infrastructure.** A graph database: Neo4j 5.26, FalkorDB, or Amazon
  Neptune; Kuzu is marked deprecated in the README because upstream Kuzu is
  unmaintained. **FalkorDB Lite** (`falkordblite`, Python ≥ 3.12) embeds the
  database in the Python process, so no server and no Docker are needed.
- **Models.** `OpenAIGenericClient` talks to any OpenAI-compatible
  `/chat/completions` endpoint with JSON-schema output; embeddings through
  `OpenAIEmbedder` with a `base_url`. Several model calls per episode
  (extract entities, resolve them against the graph, extract facts, resolve
  and invalidate facts, update entity summaries; measured below in
  `docs/results.md`), and embeddings for every entity and fact.
- **Feasibility: high.** Same reading model, embedded database, pinned
  release. Implemented.

### Letta (formerly MemGPT)

[letta-ai/letta](https://github.com/letta-ai/letta), Apache-2.0; paper
MemGPT (arXiv:2310.08560). The agent keeps **memory blocks** in its context
and edits them with tool calls; older history is searched through tools.

- **Status.** The repository README now says the current source "lives in
  letta-ai/letta-code" and that the archive branch "contains the retired
  Letta V1 API server". The PyPI package `letta` 0.34.12 is "Letta Code:
  stateful agents in your terminal" and ships the `letta_code` binary, not a
  server.
- **Time.** None documented: a block is free text the agent rewrites.
- **Feasibility: low.** Running it means pinning the retired V1 server with
  its Postgres, or driving a terminal coding agent. And the memory is the
  agent's own free-text rewrite of its blocks, which the **rolling summary**
  baseline already measures: a bounded text updated after every event.

### LangMem

[langchain-ai/langmem](https://github.com/langchain-ai/langmem), MIT,
0.0.30 (2025-10-27). Memory tools for an agent ("the agent gets to decide
what and when to store") and a background manager that "extracts,
consolidates, and updates" memories in a LangGraph store.

- **Time.** None: the released source has no timestamps or validity fields
  on memories.
- **Feasibility: medium, value low.** It runs on LangChain with any
  OpenAI-compatible endpoint, but its consolidation (the model rewrites the
  set of memories) is Mem0's design. It would add a dependency stack and
  tell us little that Mem0 does not.

### Cognee

[topoteretes/cognee](https://github.com/topoteretes/cognee), Apache-2.0,
1.6.8. Turns documents into "entities, relationships, and searchable
chunks"; the defaults run locally, Postgres can hold relational, vector and
(as a demo feature) graph state.

- **Time.** No validity or supersession documented; chunks carry document
  metadata.
- **Feasibility: medium, fit low.** It is built for a corpus of documents
  ingested and "cognified" in batches, not a conversation read message by
  message; a per-message pipeline would be our adapter, not Cognee.

### A-MEM

[agiresearch/A-mem](https://github.com/agiresearch/A-mem), MIT; paper
arXiv:2502.12110. Zettelkasten notes whose links and descriptions "evolve"
as notes arrive.

- **Time.** A timestamp per note; no intervals, no supersession.
- **Infrastructure.** ChromaDB and a sentence-transformers model; LLM
  backends `openai` or `ollama`. (The PyPI name `agentic-memory` belongs to
  an unrelated project; A-MEM installs from its repository.)
- **Feasibility: low.** A research prototype (31 commits); reproduction lives
  in a separate repository. Its update model is Mem0's.

### MemoryOS and MemOS

Two different projects with similar names.

- **MemoryOS** ([BAI-LAB/MemoryOS](https://github.com/BAI-LAB/MemoryOS),
  Apache-2.0, arXiv:2506.06326): short-, mid- and long-term "persona memory"
  with an updater that promotes information between tiers, local embedding
  models, an `openai_base_url`. Reports LoCoMo. Built for a user's persona, not for the
  state of a case; no supersession.
- **MemOS** ([MemTensor/MemOS](https://github.com/MemTensor/MemOS),
  Apache-2.0, arXiv:2507.03724): a memory "operating system" of MemCubes;
  self-hosting is `docker compose up` with Neo4j and Qdrant. Reports LoCoMo
  and LongMemEval through its own harness.
- **Feasibility: low.** MemOS needs Docker and two servers; MemoryOS models a
  persona. Neither has validity semantics a reviewer would hold against us.

### Supermemory

[supermemoryai/supermemory](https://github.com/supermemoryai/supermemory),
MIT. A hosted memory API with a local mode shipped as a binary or Docker
image ("You don't need to build or clone the server source"). It says it
"handles knowledge updates and contradictions, forgets expired information".

- **Feasibility: low.** The engine is a prebuilt binary, and the updates are
  made by its model without documented semantics we could score or explain.

### Newer systems

- **Hindsight** ([vectorize-io/hindsight](https://github.com/vectorize-io/hindsight),
  MIT): world facts, experiences, observations and mental models in
  PostgreSQL + pgvector (embedded `pg0` for no server); `retain` takes an
  event timestamp and recall filters by time; any OpenAI-compatible LLM. The
  strongest second candidate: it has event times and runs without Docker.
  Not implemented here, because its default embeddings and rerankers are its
  own models and it brings a Postgres per ticket — more than "cheap".
- **Memobase** (Apache-2.0; FastAPI, Postgres, Redis), **MIRIX**
  (Apache-2.0; Postgres, optional Neo4j; arXiv:2507.07957), **Memori**
  (Apache-2.0 SDKs, hosted engine), **MemU** (Apache-2.0; SQLite or
  pgvector): user-profile and assistant memories with timestamps at most.
  None models a commitment or a fact's validity.

## Closed helpdesk copilots

They cannot be run. Per their documentation (refs [3]–[6] of
`docs/report/01-obzor.md`: Zendesk, Intercom, Freshdesk, Jira Service
Management), each produces a free-text summary of the ticket on demand, from
the transcript, at that moment. Salesforce's documentation was not read for
the report and could not be reached today; its row is an assumption to
check.

| Product | Feature | Approximated by |
|---|---|---|
| Zendesk | ticket summaries (agent copilot) | **full context**: one model reads the whole transcript when asked |
| Intercom | Fin AI Copilot, AI Summarize for handover | **full context** |
| Freshdesk | Freddy AI Copilot, Ticket Summary | **full context** |
| Jira Service Management | Atlassian Intelligence, summarize comments (not persisted) | **full context** |
| Salesforce | Einstein / Agentforce work summaries (not verified) | **full context** if regenerated on demand; **rolling summary** if a stored summary is carried forward |

The full-context baseline is the generous approximation: it gets a strong
reader, every message, and our checkpoint question with its rules, which a
product summary button does not.

## Recommendation

1. **Graphiti — implement.** It is the one existing system whose design
   claims what RQ2 measures: facts with validity intervals that are closed,
   not deleted, when contradicted. It runs in the container with an embedded
   graph database, takes the same reading model through an OpenAI-compatible
   client, and stands in for Zep Cloud. Done:
   `src/duebook/bench/baselines/graphiti_memory.py`.
2. **Hindsight — next, if a second system is wanted.** Event timestamps and
   time-filtered recall, MIT, runs on embedded Postgres; worth it only if
   reviewers ask for a second structured competitor.
3. **Not run, with the baseline that covers them:** Letta (rolling summary:
   free text the agent rewrites), LangMem and A-MEM (Mem0: a model that
   rewrites a set of memories), the helpdesk copilots (full context, or
   rolling summary for Salesforce), Cognee, MemOS, MemoryOS, Supermemory
   and the profile memories (designed for documents or a user's persona;
   none models a commitment or a fact's validity).

None of the surveyed systems stores a commitment with an owner, a deadline
resolved by code and a status, or separates a change of a value from a
disagreement between the sides; that holds for the open systems by their
source and documentation, and for the copilots by their documentation.
