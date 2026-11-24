"""The HTTP face of duebook: a webhook that only stores, and read endpoints.

    app = create_app(Settings.from_env())

``POST /events`` is what a helpdesk calls. It validates the event, stores it
with ``Store.ingest`` and answers 202 — it never calls the model. A helpdesk
that waits seconds for an answer times out and redelivers; storing first and
applying later (``duebook process``) keeps the acknowledgement fast and makes
redelivery harmless, since a stored event is recognised by its position.

The read endpoints render what the store holds: the card now or as of any
event, the handoff brief with the source of every line, and where a ticket's
processing stands. They are guarded by an API key; the webhook is guarded by
a signature over its raw body instead, because a helpdesk can sign but is
rarely able to send a bearer token.

Every request borrows a connection from a pool and returns it at the end. Each
store call is its own short transaction; no transaction stays open across a
request.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from .. import signing
from ..brief import Brief, compose
from ..card import Card, Source
from ..events import CheckKind
from ..lifecycle import status_at
from ..settings import Settings
from ..store import Conflict, Store
from ..store.codec import encode
from .validation import Invalid, read_event
from .zammad import TICKET_PREFIX, zammad_router

#: The largest webhook body accepted. One ticket event is a few kilobytes; a
#: limit keeps a misbehaving sender from filling memory or the append-only log.
MAX_EVENT_BYTES = 256 * 1024


def create_app(
    settings: Settings,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """The application. ``clock`` is injectable so tests can stand at any moment."""
    pool = ConnectionPool(
        settings.require_database(),
        min_size=settings.pool_min,
        max_size=settings.pool_max,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
        name="duebook-api",
    )
    cal, policy = settings.calendar(), settings.policy()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Not waiting for the first connection: the API should come up and
        # report an unreachable database on /health, not refuse to start.
        pool.open(wait=False)
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="duebook", version="0.1.0", lifespan=lifespan)
    app.state.pool = pool
    @contextmanager
    def store(timeout: float = 5.0) -> Iterator[Store]:
        with pool.connection(timeout=timeout) as conn:
            yield Store(conn, cal, policy)

    if settings.zammad_url:
        app.mount(
            "/assets/operator", StaticFiles(directory=Path(__file__).with_name("fonts")),
            name="operator-fonts",
        )
        app.include_router(zammad_router(settings.zammad_url, settings.timezone, store, clock))

    def authorized(ticket_id: str, request: Request) -> None:
        if ticket_id.startswith(TICKET_PREFIX):
            raise HTTPException(404, "ticket unavailable on this route")
        if not settings.api_key:
            return
        scheme, _, key = request.headers.get("authorization", "").partition(" ")
        expected = settings.api_key.encode()
        if scheme.lower() != "bearer" or not hmac.compare_digest(key.strip().encode(), expected):
            raise HTTPException(
                401, "a valid API key is required", headers={"WWW-Authenticate": "Bearer"}
            )

    guarded = [Depends(authorized)]

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    # ------------------------------------------------------------ webhook ----

    @app.post("/events", status_code=202)
    async def receive(request: Request) -> dict[str, Any]:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_EVENT_BYTES:
            raise HTTPException(413, f"an event must not exceed {MAX_EVENT_BYTES} bytes")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_EVENT_BYTES:
                raise HTTPException(413, f"an event must not exceed {MAX_EVENT_BYTES} bytes")
        body = bytes(body)
        if settings.webhook_secret and not signing.verify(
            settings.webhook_secret, body, request.headers.get(signing.HEADER)
        ):
            raise HTTPException(401, "missing or invalid signature")
        try:
            raw = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPException(400, f"the body is not JSON: {error}") from error
        try:
            event = read_event(raw)
        except Invalid as error:
            raise HTTPException(422, str(error)) from error
        if event.ticket_id.startswith(TICKET_PREFIX) and not settings.webhook_secret:
            raise HTTPException(403, "Zammad imports require signed ingestion")

        def ingest() -> bool:
            with store() as s:
                return s.ingest(event, clock())

        try:
            new = await run_in_threadpool(ingest)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        return {"ticket_id": event.ticket_id, "seq": event.seq, "new": new}

    # -------------------------------------------------------------- reads ----

    @app.get("/tickets/{ticket_id}/card", dependencies=guarded)
    def card(ticket_id: str, as_of: Annotated[int | None, Query(ge=1)] = None) -> dict[str, Any]:
        with store() as s:
            current = _known(s, ticket_id)
            if as_of is None:
                shown, now = current, clock()
            else:
                if as_of > current.last_seq:
                    raise HTTPException(404, f"event {as_of} has not been applied yet")
                shown = s.replay(ticket_id, upto=as_of)
                # A card as of an event is judged at that event's moment:
                # what was overdue then, not what is overdue now.
                now = s.event(ticket_id, as_of).at
        return {
            "ticket_id": ticket_id,
            "as_of": shown.last_seq,
            "at": now.isoformat(),
            "card": encode(shown),
            "commitment_status": {c.id: status_at(c, now, policy).value for c in shown.commitments},
        }

    @app.get("/tickets/{ticket_id}/brief", dependencies=guarded, response_model=None)
    def brief(
        ticket_id: str,
        budget: Annotated[int | None, Query(ge=1)] = None,
        format: Literal["text", "json"] = "text",
    ) -> PlainTextResponse | dict[str, Any]:
        with store() as s, s.conn.transaction():
            s.conn.execute("set transaction isolation level repeatable read, read only")
            current = _known(s, ticket_id)
            pending = s.next_check(current)
            if pending is not None and pending.kind is CheckKind.HANDOFF:
                raise HTTPException(202, "Handoff reconciliation is pending", {"Retry-After": "1"})
            names = s.authors(ticket_id)
        now = clock()
        b = compose(current, now, budget=budget, cal=cal, policy=policy, names=names)
        if format == "text":
            return PlainTextResponse(b.text)
        return _brief_json(ticket_id, b, now, names)

    @app.get("/tickets/{ticket_id}/status", dependencies=guarded)
    def status(ticket_id: str) -> dict[str, Any]:
        with store() as s:
            current = _known(s, ticket_id)
            backlog = s.backlog(ticket_id)
            hold = s.held(ticket_id)
            verification = s.verifier_counts(ticket_id)
        return {
            "ticket_id": ticket_id,
            "last_seq": current.last_seq,
            "backlog": backlog,
            **verification,
            "hold": None
            if hold is None
            else {
                "seq": hold.seq,
                "error": hold.error,
                "attempts": hold.attempts,
                "first_failed_at": hold.first_failed_at.isoformat(),
                "retry_at": hold.retry_at.isoformat(),
            },
        }

    @app.get("/health", response_model=None)
    def health() -> JSONResponse:
        try:
            with store(timeout=2.0) as s:
                s.conn.execute("select 1")
                counts = {
                    "pending_outbox": s.outbox.pending(),
                    "held_tickets": s.held_count(),
                    **s.verifier_counts(),
                }
        except (PoolTimeout, psycopg.Error):
            return JSONResponse({"database": "unreachable"}, status_code=503)
        return JSONResponse({"database": "ok", **counts})

    return app


def _known(s: Store, ticket_id: str) -> Card:
    if not s.known(ticket_id):
        raise HTTPException(404, f"no events stored for ticket {ticket_id}")
    return s.card(ticket_id)


def _brief_json(ticket_id: str, b: Brief, now: datetime, names: dict[str, str]) -> dict[str, Any]:
    def source(src: Source) -> dict[str, Any]:
        return {
            "event_id": src.event_id,
            "seq": src.seq,
            "author_id": src.author_id,
            "author": names.get(src.author_id, src.author_id),
            "side": src.side.value,
            "quote": src.quote,
        }

    return {
        "ticket_id": ticket_id,
        "at": now.isoformat(),
        "text": b.text,
        "tokens": b.tokens,
        "budget": b.budget,
        "complete": b.complete,
        "sections": [
            {
                "key": section.key,
                "title": section.title,
                "lines": [
                    {"text": line.text, "sources": [source(src) for src in line.sources]}
                    for line in section.lines
                ],
            }
            for section in b.sections
        ],
        "dropped": [{"key": d.key, "title": d.title, "items": d.items} for d in b.dropped],
    }
