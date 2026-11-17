"""Baseline (d): Graphiti, Zep's temporal knowledge graph (``pip install -e ".[graphiti]"``).

Every event is added as an episode with its own time as ``reference_time``,
so Graphiti resolves "tomorrow" against the message, not the wall clock. It
extracts entities and facts, and when a new fact contradicts an old one it
closes the old fact's validity interval instead of deleting it — the nearest
existing design to our supersession. At a checkpoint the whole graph of the
ticket (every fact with its validity window and source messages, and every
entity summary) is handed to the model with the shared question, as Zep's
context block presents them; search is not used, for the reason given in
``mem0_memory``.

Same plumbing choices as the Mem0 baseline: the reading model, its
reasoning and routing (``extract.client.PRESETS``) through Graphiti's
``OpenAIGenericClient``; ``openai/text-embedding-3-small`` through
OpenRouter; the database is FalkorDB Lite, embedded, one per ticket. Every
call Graphiti makes is metered, and temperature-0 answers and embeddings are
kept in the response cache.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ...events import AssigneeChanged, MessagePosted
from ...extract.client import (
    DEFAULT_MODEL,
    OPENROUTER_URL,
    ResponseCache,
    openrouter_fields,
    settings_for,
)
from ...extract.extractor import JsonModel
from ...temporal import WorkCalendar
from ..generate import SimTicket
from ..meter import Ledger, Meter, OverBudget
from ..methods import MethodRun
from ..result import Snapshot, checkpoints
from .answer import ANSWER_SCHEMA, moment, parse_answer, question, render_event
from .mem0_memory import ATTEMPTS, EMBEDDER, EMBEDDING_DIMS, usage_of

INSTRUCTIONS = """\
This is a customer support ticket; each episode is one message or ticket \
event, starting with its number (#N) and local time. Keep every promise (who \
promised what, and the deadline as an absolute date resolved against the \
message time), whether it was done and who confirmed it, the facts of the \
case (order number, plan, address, amounts, product) and their changes, \
disagreements between the customer and the company, and fixes that failed. \
A customer saying "you promised" is not a new promise.\
"""


def available() -> str | None:
    """None when Graphiti and FalkorDB Lite can be imported, else the reason."""
    try:
        _imports()
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    return None


def _imports():
    os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
    from graphiti_core import Graphiti
    from redislite.async_falkordb_client import AsyncFalkorDB

    return Graphiti, AsyncFalkorDB


@dataclass(slots=True)
class GraphitiMemory:
    #: Answers the checkpoint question (the same client the other baselines use).
    model: JsonModel
    ledger: Ledger
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    llm_model: str = DEFAULT_MODEL
    embedder: str = EMBEDDER
    base_url: str = OPENROUTER_URL
    api_key: str = "proxy-injected"
    cache: ResponseCache | None = None
    name: str = "graphiti"

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger)
        stops = {cp.seq: cp for cp in checkpoints(ticket)}
        snaps, malformed, kept, failure = [], [], [], None
        seq_of: dict[str, int] = {}  # episode uuid -> message number
        group = "".join(c if c.isalnum() or c in "-_" else "_" for c in ticket.ticket_id)
        with tempfile.TemporaryDirectory(prefix="graphiti-") as workdir, asyncio.Runner() as loop:
            graph = loop.run(self._graph(os.path.join(workdir, "graph.db"), group, meter))
            for event in ticket.events:
                if failure is None and isinstance(event, MessagePosted | AssigneeChanged):
                    meter.answering = False
                    for attempt in range(ATTEMPTS):
                        started = time.perf_counter()
                        try:
                            added = loop.run(self._add(graph, group, event))
                            seq_of[added.episode.uuid] = event.seq
                            break
                        except OverBudget as error:
                            failure = f"seq {event.seq}: {type(error).__name__}: {error}"
                            break
                        except Exception as error:
                            if attempt == ATTEMPTS - 1:
                                failure = f"seq {event.seq}: {type(error).__name__}: {error}"
                        finally:
                            # Graphiti runs its calls concurrently: count wall time.
                            meter.tally.ingest_seconds += time.perf_counter() - started
                cp = stops.get(event.seq)
                if cp is None:
                    continue
                meter.answering = True
                try:
                    facts, entities = loop.run(_contents(graph, group))
                    listing = listing_of(facts, entities, seq_of, self.cal)
                    completion = meter.complete_json(
                        question("GRAPH (what Graphiti holds for this ticket; the transcript "
                                 "is not available)", listing, cp, self.cal),
                        ANSWER_SCHEMA,
                        name="ticket_state",
                    )  # fmt: skip
                except Exception as error:
                    snaps.append(Snapshot(cp, error=f"{type(error).__name__}: {error}"))
                    failure = failure or f"answer at {cp.seq}: {type(error).__name__}: {error}"
                    continue
                read = parse_answer(completion.data, cp, self.cal)
                snaps.append(Snapshot(read.snapshot.checkpoint, read.snapshot.promises,
                                      read.snapshot.facts, read.snapshot.failed_steps,
                                      read.snapshot.disputes, failure))  # fmt: skip
                malformed += read.malformed
                kept.append({"seq": cp.seq, "facts": len(facts), "entities": len(entities),
                             "listing": listing, "answer": completion.data})  # fmt: skip
            loop.run(graph.close())
        detail = {"malformed": malformed, "checkpoints": kept, "episodes": len(seq_of),
                  "cache_hits": meter.tally.cache_hits}  # fmt: skip
        return MethodRun.of(self.name, ticket, tuple(snaps), meter.tally, failure, detail)

    # ------------------------------------------------------------- plumbing ----

    async def _graph(self, path: str, group: str, meter: Meter):
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client import LLMConfig
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
        from openai import AsyncOpenAI

        Graphiti, AsyncFalkorDB = _imports()
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        self._meter(client, meter)
        llm = LLMConfig(api_key=self.api_key, model=self.llm_model, small_model=self.llm_model,
                        base_url=self.base_url, temperature=0.0)  # fmt: skip
        embedder = OpenAIEmbedderConfig(
            embedding_model=self.embedder, api_key=self.api_key, base_url=self.base_url,
            embedding_dim=EMBEDDING_DIMS,
        )  # fmt: skip
        graph = Graphiti(
            graph_driver=FalkorDriver(falkor_db=AsyncFalkorDB(dbfilename=path), database=group),
            llm_client=OpenAIGenericClient(config=llm, client=client),
            embedder=OpenAIEmbedder(config=embedder, client=client),
            # Required by the constructor; reranking belongs to search, which is not used.
            cross_encoder=OpenAIRerankerClient(config=llm, client=client),
        )
        await graph.build_indices_and_constraints()
        return graph

    async def _add(self, graph, group: str, event):
        from graphiti_core.nodes import EpisodeType

        return await graph.add_episode(
            name=f"#{event.seq}",
            episode_body=render_event(event, self.cal),
            source_description="support ticket",
            reference_time=event.at,
            source=EpisodeType.message,
            group_id=group,
            custom_extraction_instructions=INSTRUCTIONS,
        )

    def _meter(self, client, meter: Meter) -> None:
        """Count every chat and embedding call; answer repeats from the cache."""
        chat_extra = {"usage": {"include": True},
                      **openrouter_fields(settings_for(self.llm_model))}  # fmt: skip

        def wrap(create, kind: str, parse):
            async def metered(*args, **params):
                extra = chat_extra if kind == "chat" else {"usage": {"include": True}}
                body = {**extra, **(params.pop("extra_body", None) or {})}
                key = {"graphiti": kind, **params, **body}
                stored = self.cache.get(key) if cacheable(kind, params) and self.cache else None
                if stored is not None:
                    response = parse(stored)
                    meter.ledger.reuse(usage_of(response))
                    meter.tally.cache_hits += 1
                    meter.tally.record(usage_of(response), 0.0, meter.answering)
                    return response
                meter.ledger.check()
                response = await create(*args, **params, extra_body=body)
                # Seconds are counted per episode, as wall time (see ``run``).
                meter.spend(usage_of(response), 0.0)
                if self.cache is not None and cacheable(kind, params):
                    self.cache.put(key, response.model_dump(mode="json"))
                return response

            return metered

        from openai.types import CreateEmbeddingResponse
        from openai.types.chat import ChatCompletion

        chat, embeddings = client.chat.completions, client.embeddings
        chat.create = wrap(chat.create, "chat", ChatCompletion.model_validate)
        embeddings.create = wrap(embeddings.create, "embeddings",
                                 CreateEmbeddingResponse.model_validate)  # fmt: skip


def cacheable(kind: str, params: dict[str, Any]) -> bool:
    """Embeddings always; chat only at temperature 0, as ``ResponseCache`` promises."""
    return kind == "embeddings" or params.get("temperature") == 0


async def _contents(graph, group: str):
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode

    driver = graph.driver.clone(database=group)
    facts = await EntityEdge.get_by_group_ids(driver, [group])
    entities = await EntityNode.get_by_group_ids(driver, [group])
    return facts, entities


def listing_of(facts, entities, seq_of: dict[str, int], cal: WorkCalendar) -> str:
    """Facts in order of their first message, with validity; then entity summaries."""

    def seqs(edge) -> list[int]:
        return sorted({seq_of[u] for u in edge.episodes if u in seq_of})

    lines = ["FACTS"]
    for edge in sorted(facts, key=lambda e: (seqs(e)[:1], e.created_at)):
        source = f"(from {', '.join(f'#{s}' for s in seqs(edge))}) " if seqs(edge) else ""
        lines.append(f"- {source}{edge.fact}{_window(edge, cal)}")
    lines.append("ENTITIES")
    lines += [f"- {n.name}: {n.summary}" for n in sorted(entities, key=lambda n: n.name)]
    return "\n".join(lines) if facts or entities else "(empty graph)"


def _window(edge, cal: WorkCalendar) -> str:
    parts = []
    if edge.valid_at:
        parts.append(f"valid from {_at(edge.valid_at, cal)}")
    if edge.invalid_at:
        parts.append(f"no longer true from {_at(edge.invalid_at, cal)}")
    if edge.expired_at and not edge.invalid_at:
        parts.append("retired")
    return f" [{'; '.join(parts)}]" if parts else ""


def _at(at: datetime, cal: WorkCalendar) -> str:
    # Graphiti stores UTC; a database may hand it back without the zone.
    return moment(at if at.tzinfo else at.replace(tzinfo=UTC), cal)
