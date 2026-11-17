"""Baseline (c): Mem0, the open-source memory layer (``pip install mem0ai``).

Every event is added to Mem0 as it arrives, the way an assistant would feed
it a conversation: customer messages as the user's, agent messages and
handoffs as the assistant's. Mem0 runs its own pipeline on each — an LLM call
that extracts memories, embeddings to deduplicate and link them, a vector
store to keep them. At a checkpoint, everything Mem0 holds for the ticket
(``get_all``) is handed to the model with the shared question; Mem0's
retrieval by similarity is not used, because a handoff needs the whole
account, not the part most similar to a query, and ``get_all`` can only
help Mem0's recall.

Configuration, chosen to change nothing but the plumbing:

* LLM: Mem0's own ``openai`` provider pointed at OpenRouter's
  OpenAI-compatible endpoint, with the same model, reasoning and provider
  routing (``extract.client.MODELS``) as every other method;
* embedder: Mem0's ``openai`` provider against OpenRouter's embeddings
  endpoint (``openai/text-embedding-3-small``);
* vector store: Qdrant in local mode, a fresh store per ticket;
* custom instructions telling it what a support ticket is and what to keep —
  the same things the other methods are told. Mem0's extraction prompt
  resolves relative dates against an "Observation Date" that the open-source
  version sets to the real current date (its ``timestamp`` argument is
  platform-only), so each message carries its own timestamp and the
  instructions say to use that.

Metering wraps the OpenAI client inside Mem0's LLM and embedder, adding the
provider's charged amount for every call to the run's ledger. Nothing about
what Mem0 sends is changed except the OpenRouter-specific request fields that
ask for usage accounting and carry the model's reasoning and routing settings.

Two repairs, both about form, not content, and both found on the dev split:

* the model sometimes returns Mem0's ``memory`` list as plain strings instead
  of objects; Mem0 2.2.1 then crashes on ``.get``. The adapter wraps such
  strings into the object form Mem0 expects (and drops entries that are
  neither), before Mem0 reads the response;
* a failed ``add`` is retried twice, as every other method's model calls are
  retried, before the ticket is stopped.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from ...events import AssigneeChanged, MessagePosted, Role
from ...extract.client import DEFAULT_MODEL, OPENROUTER_URL, openrouter_fields, settings_for
from ...extract.cost import Usage
from ...extract.extractor import JsonModel
from ...temporal import WorkCalendar
from ..generate import SimTicket
from ..meter import Ledger, Meter, OverBudget
from ..methods import MethodRun
from ..result import Snapshot, checkpoints
from .answer import ANSWER_SCHEMA, parse_answer, question, render_event

EMBEDDER = "openai/text-embedding-3-small"
#: Tries per event, as ChatClient tries each call.
ATTEMPTS = 3
EMBEDDING_DIMS = 1536

CUSTOM_INSTRUCTIONS = """\
This is a customer support ticket. "user" messages come from the customer, \
"assistant" messages from support agents (several agents take turns) or are \
ticket events such as a reassignment. Each message starts with its number \
(#N) and the local time it was sent; use THAT time, not the Observation Date, \
to turn relative times ("tomorrow", "by Friday", "in 2 hours", "within 3 \
business days") into absolute dates.

Extract and keep:
- every promise: who promised it (the company/agent or the customer), what, \
in which message (#N), and the deadline as an absolute date or date and time;
- whether a promise was done, and who confirmed it;
- facts of the case (order number, plan, delivery address, amounts, product \
model) and every change to them;
- disagreements between the customer and the company, and how they ended;
- fixes that were tried and whether they worked.

Always include the message number (#N) the memory comes from. A customer \
saying "you promised" is not a new promise. Ignore greetings, thanks and \
status pings that carry no information.\
"""


def available() -> str | None:
    """None when Mem0 can be imported, else the reason it cannot."""
    try:
        _import_memory()
    except Exception as error:  # ImportError, or a broken optional dependency
        return f"{type(error).__name__}: {error}"
    return None


def _import_memory():
    os.environ.setdefault("MEM0_TELEMETRY", "False")
    os.environ.setdefault("MEM0_DIR", os.path.join(tempfile.gettempdir(), "duebook-mem0"))
    from mem0 import Memory

    return Memory


@dataclass(slots=True)
class Mem0Memory:
    #: Answers the checkpoint question (the same client the other baselines use).
    model: JsonModel
    ledger: Ledger
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    llm_model: str = DEFAULT_MODEL
    embedder: str = EMBEDDER
    base_url: str = OPENROUTER_URL
    api_key: str = "proxy-injected"
    name: str = "mem0"

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger)
        stops = {cp.seq: cp for cp in checkpoints(ticket)}
        snaps, malformed, kept, failure, added = [], [], [], None, 0
        with tempfile.TemporaryDirectory(prefix="mem0-") as workdir:
            memory = self._memory(workdir)
            self._meter(memory, meter)
            for event in ticket.events:
                if failure is None:
                    meter.answering = False
                    for attempt in range(ATTEMPTS):
                        try:
                            added += self._add(memory, ticket, event)
                            break
                        except OverBudget as error:
                            failure = f"seq {event.seq}: {type(error).__name__}: {error}"
                            break
                        except Exception as error:
                            if attempt == ATTEMPTS - 1:
                                failure = f"seq {event.seq}: {type(error).__name__}: {error}"
                cp = stops.get(event.seq)
                if cp is None:
                    continue
                meter.answering = True
                try:
                    memories = self._all(memory, ticket)
                    listing = "\n".join(_line(m) for m in memories) or "(no memories)"
                    completion = meter.complete_json(
                        question("MEMORIES (what Mem0 holds for this ticket; the transcript "
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
                kept.append({"seq": cp.seq, "memories": [_line(m) for m in memories],
                             "answer": completion.data})  # fmt: skip
        detail = {"malformed": malformed, "checkpoints": kept, "memories_added": added}
        return MethodRun.of(self.name, ticket, tuple(snaps), meter.tally, failure, detail)

    # ------------------------------------------------------------- plumbing ----

    def _memory(self, workdir: str):
        Memory = _import_memory()
        config = {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": self.llm_model,
                    "api_key": self.api_key,
                    "openai_base_url": self.base_url,
                    "temperature": 0.0,
                    "max_tokens": 4000,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": self.embedder,
                    "api_key": self.api_key,
                    "openai_base_url": self.base_url,
                    "embedding_dims": EMBEDDING_DIMS,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "ticket",
                    "path": os.path.join(workdir, "qdrant"),
                    "embedding_model_dims": EMBEDDING_DIMS,
                    "on_disk": False,
                },
            },
            "history_db_path": os.path.join(workdir, "history.db"),
            "custom_instructions": CUSTOM_INSTRUCTIONS,
        }
        return Memory.from_config(config)

    def _meter(self, memory, meter: Meter) -> None:
        """Count every call Mem0 makes, through its own OpenAI clients."""
        extra: dict[str, Any] = {"usage": {"include": True}}
        chat_extra = {**extra, **openrouter_fields(settings_for(self.llm_model))}

        def wrap(create, chat: bool):
            def metered(*args, **params):
                meter.ledger.check()
                body = {**(chat_extra if chat else extra), **(params.pop("extra_body", None) or {})}
                started = time.perf_counter()
                response = create(*args, **params, extra_body=body)
                meter.spend(_usage(response), time.perf_counter() - started)
                if chat:
                    _repair(response)
                return response

            return metered

        completions = memory.llm.client.chat.completions
        completions.create = wrap(completions.create, chat=True)
        embeddings = memory.embedding_model.client.embeddings
        embeddings.create = wrap(embeddings.create, chat=False)

    def _add(self, memory, ticket: SimTicket, event) -> int:
        if isinstance(event, MessagePosted):
            role = "user" if event.author.role is Role.CUSTOMER else "assistant"
        elif isinstance(event, AssigneeChanged):
            role = "assistant"
        else:
            return 0
        result = memory.add(
            [{"role": role, "content": render_event(event, self.cal)}],
            user_id=ticket.ticket_id,
            metadata={"seq": event.seq},
        )
        return len((result or {}).get("results") or [])

    def _all(self, memory, ticket: SimTicket) -> list[dict]:
        found = memory.get_all(filters={"user_id": ticket.ticket_id}, top_k=10_000)
        items = list((found or {}).get("results") or [])
        return sorted(items, key=lambda m: ((m.get("metadata") or {}).get("seq") or 0,
                                            m.get("created_at") or ""))  # fmt: skip


def _repair(response) -> None:
    """Give Mem0 its ``memory`` list as objects when the model wrote plain strings."""
    try:
        message = response.choices[0].message
        data = json.loads(_unfence(message.content or ""))
    except (AttributeError, IndexError, TypeError, ValueError):
        return
    if not isinstance(data, dict) or not isinstance(data.get("memory"), list):
        return
    items = data["memory"]
    if all(isinstance(m, dict) for m in items):
        return
    fixed = []
    for n, m in enumerate(items):
        if isinstance(m, dict):
            fixed.append(m)
        elif isinstance(m, str) and m.strip():
            fixed.append({"id": str(n), "text": m.strip()})
    message.content = json.dumps({**data, "memory": fixed}, ensure_ascii=False)


def _unfence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text


def _line(memory: dict) -> str:
    seq = (memory.get("metadata") or {}).get("seq")
    origin = f"(added at #{seq}) " if seq else ""
    return f"- {origin}{memory.get('memory', '')}"


def _usage(response) -> Usage:
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage(calls=1)
    data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
    cost = data.get("cost")
    details = data.get("prompt_tokens_details") or {}
    return Usage(
        calls=1,
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        completion_tokens=int(data.get("completion_tokens") or 0),
        cached_tokens=int((details or {}).get("cached_tokens") or 0),
        reported_usd=float(cost) if isinstance(cost, int | float) else None,
    )
