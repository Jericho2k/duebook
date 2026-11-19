"""Read a Zammad ticket with the viewing agent's own access token."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, TypeVar
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from httpx import Client, HTTPStatusError, RequestError
from pydantic import AwareDatetime, BaseModel, Field, PositiveInt, TypeAdapter

from ..card import Card, CommitmentState, Fact, Source, Suggestion
from ..events import Actor, AssigneeChanged, Event, MessagePosted, Role, Side, StatusChanged
from ..lifecycle import AlertPolicy, status_at
from ..settings import ConfigError
from ..store import Store

PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
PAGE_HEADERS = {
    **PRIVATE,
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "font-src 'self'; connect-src 'self'; form-action 'none'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}
DENIED = {401, 403, 404}
TICKET_PREFIX = "zammad:"


class InconsistentSource(ValueError):
    """The provider's responses cannot establish a complete ticket snapshot."""


class _Ticket(BaseModel):
    id: int
    number: str
    title: str
    owner: str | None = None


class _SyncTicket(_Ticket):
    created_at: AwareDatetime
    owner_id: PositiveInt | None
    state_id: PositiveInt
    state: str


_TicketType = TypeVar("_TicketType", bound=_Ticket)


class _Article(BaseModel):
    id: int
    ticket_id: int
    author: str | None = Field(default=None, alias="from")
    sender: str | None = None
    subject: str | None = None
    body: str | None
    content_type: str | None = None
    internal: bool
    created_at: AwareDatetime
    created_by_id: PositiveInt | None = None
    origin_by_id: PositiveInt | None = None

    def text(self) -> str:
        body = self.body or ""
        if (self.content_type or "").partition(";")[0].strip().lower() == "text/html":
            parser = _ArticleText()
            parser.feed(body)
            parser.close()
            body = parser.text()
        return body or self.subject or ""

    def display(self, timezone: ZoneInfo) -> dict[str, int | str | bool]:
        return {
            "id": self.id,
            "author": self.author or self.sender or "—",
            "time": self.created_at.astimezone(timezone).strftime("%d.%m.%Y, %H:%M %Z"),
            "internal": self.internal,
            "body": self.text(),
        }


class _Change(BaseModel):
    id: PositiveInt
    o_id: PositiveInt
    object: str
    type: str
    attribute: str | None = None
    id_from: PositiveInt | None = None
    id_to: PositiveInt | None = None
    related_o_id: PositiveInt | None = None
    created_at: AwareDatetime


def stream_id(base: str, ticket_id: int) -> str:
    return f"{TICKET_PREFIX}{sha256(base.encode()).hexdigest()}:{ticket_id}"


def owner_id(value: int | None) -> str | None:
    return None if value in (None, 1) else str(value)  # Zammad's Nobody user is 1.


def _message(article: _Article) -> dict | None:
    role = {"Agent": Role.AGENT, "Customer": Role.CUSTOMER}.get(article.sender)
    if article.internal or role is None:
        return None
    author_id = article.origin_by_id or article.created_by_id
    if author_id is None:
        raise ValueError("a public article is missing its author ID")
    return {
        "id": f"article:{article.id}",
        "at": article.created_at.astimezone(UTC),
        "author": Actor(str(author_id), role, article.author or ""),
        "text": article.text(),
    }


def _events(
    base: str, ticket: _SyncTicket, articles: list[_Article], history: list[_Change],
) -> list[Event]:
    ticket_id = stream_id(base, ticket.id)
    published = {article.id: _message(article) for article in articles}
    if len(published) != len(articles):
        raise ValueError("duplicate article in the provider response")
    changes: dict[int, _Change] = {}
    for change in history:
        if changes.setdefault(change.id, change) != change:
            raise ValueError("conflicting copies of a history entry")
    ordered = sorted(changes.values(), key=lambda c: (c.created_at, c.id))
    original = {"owner": ticket.owner_id, "state": ticket.state_id}
    fields = {
        c.id: c.attribute for c in ordered if c.object == "Ticket" and c.type in {"set", "updated"}
        and c.attribute in original
    }
    for field in original:
        first = next((changes[key] for key, name in fields.items() if name == field), None)
        if first:
            original[field] = first.id_from
    at = ticket.created_at.astimezone(UTC)
    events: list[Event] = [
        AssigneeChanged(id="initial:owner", ticket_id=ticket_id, seq=1, at=at,
                        previous=None, current=owner_id(original["owner"])),
        StatusChanged(id="initial:state", ticket_id=ticket_id, seq=2, at=at,
                      previous="", current=str(original["state"] or "")),
    ]  # fmt: skip
    for change in ordered:
        if change.object == "Ticket::Article" and change.type == "created":
            if change.related_o_id != ticket.id or change.o_id not in published:
                raise InconsistentSource("an article in the history is unavailable")
            message = published.pop(change.o_id)
            if message is not None:
                events.append(MessagePosted(ticket_id=ticket_id, seq=len(events) + 1, **message))
        elif change.id in fields:
            field = fields[change.id]
            if change.o_id != ticket.id or change.id_from != original[field]:
                raise InconsistentSource("ticket history has an inconsistent change")
            original[field] = change.id_to
            common = dict(id=f"history:{change.id}", ticket_id=ticket_id, seq=len(events) + 1,
                          at=change.created_at.astimezone(UTC))
            if field == "owner":
                events.append(AssigneeChanged(
                    **common, previous=owner_id(change.id_from), current=owner_id(change.id_to)
                ))
            else:
                events.append(StatusChanged(
                    **common, previous=str(change.id_from or ""), current=str(change.id_to or "")
                ))
    if any(published.values()) or original != {"owner": ticket.owner_id, "state": ticket.state_id}:
        raise InconsistentSource("ticket changed while its history was read")
    if len({event.id for event in events}) != len(events):
        raise ValueError("duplicate event in the provider history")
    return events


def source_events(
    base: str, ticket_id: int, token: str,
) -> tuple[_SyncTicket, list[_Article], list[Event]]:
    with _client(base, token) as client:
        # This endpoint requires ticket.agent as well as access to the ticket.
        raw = client.get(f"api/v1/ticket_history/{ticket_id}").raise_for_status().json()
        history = TypeAdapter(list[_Change]).validate_python(raw["history"])
        ticket, articles = _load_ticket(client, ticket_id, _SyncTicket)
    return ticket, articles, _events(base, ticket, articles, history)


class _ArticleText(HTMLParser):
    """Keep readable text and line breaks without fetching embedded resources."""

    _excluded = {"script", "style", "template", "head"}
    _blocks = {"address", "blockquote", "div", "li", "p", "pre", "tr"}
    _starts = _blocks | {"br"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.excluded: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._excluded:
            self.excluded.append(tag)
        elif not self.excluded and tag in self._starts:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.excluded and tag == self.excluded[-1]:
            self.excluded.pop()
        elif not self.excluded and tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.excluded:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines()).strip()


def secure_base(base_url: str, setting: str) -> str:
    url = urlsplit(base_url)
    local = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        (url.scheme != "https" and not local)
        or not url.hostname
        or url.username is not None
        or url.query
        or url.fragment
    ):
        raise ConfigError(f"{setting} must be an HTTPS base URL or local HTTP URL")
    return base_url.rstrip("/") + "/"


def zammad_router(
    base_url: str,
    timezone: str,
    store: Callable[[], AbstractContextManager[Store]],
    clock: Callable[[], datetime],
) -> APIRouter:
    base = secure_base(base_url, "DUEBOOK_ZAMMAD_URL")
    zone = ZoneInfo(timezone)
    workspace = Path(__file__).with_name("operator.html").read_text(encoding="utf-8")
    workspace = workspace.replace("{{provider}}", "Zammad")
    router = APIRouter(prefix="/zammad/tickets")

    @router.get("/{ticket_id}", response_class=HTMLResponse)
    def page(ticket_id: int, request: Request) -> HTMLResponse:
        fonts = request.url_for("operator-fonts", path="/").path.rstrip("/")
        page_html = workspace.replace("{{fonts}}", quote(fonts, safe="/%"))
        return HTMLResponse(page_html, headers=PAGE_HEADERS)

    def viewer_token(authorization: Annotated[str, Header()] = "") -> str:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or any(not "!" <= c <= "~" for c in token):
            raise HTTPException(401, "A Zammad access token is required", headers=PRIVATE)
        return token

    Viewer = Annotated[str, Depends(viewer_token)]

    @router.get("/{ticket_id}/conversation")
    def conversation(ticket_id: int, token: Viewer) -> JSONResponse:
        with _provider_read():
            ticket, articles = _load(base, ticket_id, token)
        return JSONResponse(
            {
                "number": ticket.number,
                "title": ticket.title,
                "owner": ticket.owner,
                "url": f"{base}#ticket/zoom/{ticket.id}",
                "messages": [article.display(zone) for article in articles],
            },
            headers=PRIVATE,
        )

    @router.get("/{ticket_id}/review")
    def review(ticket_id: int, token: Viewer, include_conversation: bool = False) -> JSONResponse:
        with _provider_read():
            ticket, articles, visible = source_events(base, ticket_id, token)
        url = f"{base}#ticket/zoom/{ticket.id}"
        current_id = stream_id(base, ticket_id)
        with store() as s, s.conn.transaction():
            s.conn.execute("set transaction isolation level repeatable read, read only")
            stored = list(s.events(current_id))
            if visible[:len(stored)] != stored:
                raise HTTPException(
                    409, "Stored evidence is unavailable at the source", headers=PRIVATE
                )
            card, hold = s.card(current_id), s.held(current_id)
        names = {
            event.author.id: event.author.name for event in stored
            if isinstance(event, MessagePosted) and event.author.name
        }
        messages = {
            "messages": [{**article.display(zone), "url": f"{url}/{article.id}"}
                         for article in articles]
        } if include_conversation else {}
        return JSONResponse(
            {
                "number": ticket.number, "title": ticket.title, "owner": ticket.owner,
                "ticket_state": ticket.state, "url": url, "timezone": timezone,
                "processing": {
                    "awaiting_sync": len(visible) - len(stored),
                    "backlog": len(stored) - card.last_seq,
                    "held": hold.seq if hold else None,
                },
                **_review_card(card, ticket, url, clock(), s.policy, names),
                **messages,
            },
            headers=PRIVATE,
        )

    return router


def _review_card(
    card: Card, ticket: _SyncTicket, url: str, now: datetime, policy: AlertPolicy,
    names: dict[str, str],
) -> dict:
    def evidence(source: Source) -> dict:
        article_id = source.event_id.removeprefix("article:")
        return {"article_id": article_id, "url": f"{url}/{article_id}", "quote": source.quote}

    def suggestion(item: Suggestion) -> dict:
        return {"kind": item.kind.value, "reason": item.reason, "source": evidence(item.source)}

    def fact(item: Fact) -> dict:
        return {"slot": item.slot, "value": item.value, "source": evidence(item.source)}

    commitments = []
    current_owner = owner_id(ticket.owner_id)
    for c in card.commitments:
        resolution = None
        if c.resolution:
            kind = c.state.value
            if c.state is CommitmentState.FULFILLED:
                kind = ("system_verification" if c.resolution.side is Side.SYSTEM
                        else "counterparty_confirmation")
            resolution = {"kind": kind, "source": evidence(c.resolution)}
        commitments.append({
            "id": c.id, "action": c.action, "party": c.owner.value,
            "responsible_id": c.responsible, "inherited_from": c.inherited_from,
            "inherited_from_name": names.get(c.inherited_from or ""),
            "responsible_name": (
                ticket.owner if c.responsible and c.responsible == current_owner else None
            ) or names.get(c.responsible or ""),
            "state": c.state.value, "status": status_at(c, now, policy).value,
            "deadline": {
                "at": c.due.deadline.due_at.isoformat(),
                "confidence": c.due.confidence.name.lower(), "quote": c.due.quote,
            } if c.due else None,
            "source": evidence(c.source), "resolution": resolution,
            "resolution_context": evidence(c.resolution_context) if c.resolution_context else None,
            "activated_by": evidence(c.activated_by) if c.activated_by else None,
            "suggestions": [suggestion(item) for item in c.suggestions] if c.unresolved else [],
        })
    facts = {fact.id: fact for fact in card.facts}
    return {
        "commitments": commitments,
        "contradictions": [
            {"slot": c.slot, "facts": [fact(facts[key]) for key in c.fact_ids]}
            for c in card.open_contradictions()
        ],
        "facts": [fact(item) for item in card.current_facts()],
        "changes": [[fact(old), fact(new)] for old, new in card.changed_facts()],
        "steps": [
            {"action": step.action, "outcome": step.outcome.value, "source": evidence(step.source),
             "outcome_source": evidence(step.outcome_source) if step.outcome_source else None}
            for step in card.steps
        ],
        "questions": [
            {"text": q.text, "waiting_on": q.waiting_on.value, "source": evidence(q.source)}
            for q in card.questions if q.open
        ],
        "unread": [evidence(item.source) for item in card.unread],
        "conditionals": [
            {"action": c.action, "condition": c.condition, "source": evidence(c.source),
             "suggestions": [suggestion(item) for item in c.suggestions]}
            for c in card.conditionals if c.pending
        ],
    }


@contextmanager
def _provider_read() -> Iterator[None]:
    try:
        yield
    except HTTPStatusError as error:
        if error.response.status_code in DENIED:
            raise HTTPException(
                error.response.status_code, "Zammad denied access to this ticket", headers=PRIVATE
            ) from None
        raise HTTPException(502, "Zammad could not load the ticket", headers=PRIVATE) from None
    except InconsistentSource:
        raise HTTPException(
            409, "Stored evidence is unavailable at the source", headers=PRIVATE
        ) from None
    except (RequestError, ValueError, TypeError, KeyError):
        raise HTTPException(502, "Zammad could not load the ticket", headers=PRIVATE) from None


def _load(base_url: str, ticket_id: int, token: str) -> tuple[_Ticket, list[_Article]]:
    with _client(base_url, token) as client:
        return _load_ticket(client, ticket_id, _Ticket)


def _client(base_url: str, token: str) -> Client:
    return Client(
        base_url=base_url,
        headers={"Authorization": f"Token token={token}", "Accept": "application/json"},
        timeout=10,
    )


def _load_ticket(
    client: Client, ticket_id: int, model: type[_TicketType],
) -> tuple[_TicketType, list[_Article]]:
    details = client.get(f"api/v1/tickets/{ticket_id}", params={"expand": "true"})
    ticket = model.model_validate(details.raise_for_status().json())
    response = client.get(f"api/v1/ticket_articles/by_ticket/{ticket_id}").raise_for_status()
    articles = TypeAdapter(list[_Article]).validate_python(response.json())
    if ticket.id != ticket_id or any(article.ticket_id != ticket_id for article in articles):
        raise ValueError("Zammad returned a different ticket")
    return ticket, sorted(articles, key=lambda article: (article.created_at, article.id))
