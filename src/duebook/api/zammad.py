"""Read a Zammad ticket with the viewing agent's own access token."""

from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from httpx import Client, HTTPStatusError, RequestError
from pydantic import BaseModel, Field

from ..settings import ConfigError

PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
PAGE_HEADERS = {
    **PRIVATE,
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
    ),
}
DENIED = {401, 403, 404}


class _Ticket(BaseModel):
    id: int
    number: str
    title: str
    owner: str | None = None


class _Article(BaseModel):
    id: int
    ticket_id: int
    author: str | None = Field(default=None, alias="from")
    sender: str | None = None
    subject: str | None = None
    body: str | None
    content_type: str | None = None
    internal: bool
    created_at: datetime

    def display(self, timezone: ZoneInfo) -> dict[str, int | str | bool]:
        body = self.body or ""
        if (self.content_type or "").partition(";")[0].strip().lower() == "text/html":
            parser = _ArticleText()
            parser.feed(body)
            parser.close()
            body = parser.text()
        return {
            "id": self.id,
            "author": self.author or self.sender or "—",
            "time": self.created_at.astimezone(timezone).strftime("%d.%m.%Y, %H:%M %Z"),
            "internal": self.internal,
            "body": body or self.subject or "",
        }


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


def zammad_router(base_url: str, timezone: str) -> APIRouter:
    url = urlsplit(base_url)
    local = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        (url.scheme != "https" and not local)
        or not url.hostname
        or url.username is not None
        or url.query
        or url.fragment
    ):
        raise ConfigError("DUEBOOK_ZAMMAD_URL must be an HTTPS base URL or local HTTP URL")

    base = base_url.rstrip("/") + "/"
    zone = ZoneInfo(timezone)
    workspace = Path(__file__).with_name("operator.html").read_text(encoding="utf-8")
    router = APIRouter(prefix="/zammad/tickets")

    @router.get("/{ticket_id}", response_class=HTMLResponse)
    def page(ticket_id: int) -> HTMLResponse:
        return HTMLResponse(workspace, headers=PAGE_HEADERS)

    @router.get("/{ticket_id}/conversation")
    def conversation(ticket_id: int, authorization: Annotated[str, Header()] = "") -> JSONResponse:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or any(not "!" <= c <= "~" for c in token):
            raise HTTPException(401, "A Zammad access token is required", headers=PRIVATE)
        try:
            ticket, articles = _load(base, ticket_id, token)
        except HTTPStatusError as error:
            status = error.response.status_code
            if status in DENIED:
                raise HTTPException(
                    status, "Zammad denied access to this ticket", headers=PRIVATE
                ) from None
            raise HTTPException(502, "Zammad could not load the ticket", headers=PRIVATE) from None
        except (RequestError, ValueError, TypeError):
            raise HTTPException(502, "Zammad could not load the ticket", headers=PRIVATE) from None
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

    return router


def _load(base_url: str, ticket_id: int, token: str) -> tuple[_Ticket, list[_Article]]:
    with Client(
        base_url=base_url,
        headers={"Authorization": f"Token token={token}", "Accept": "application/json"},
        timeout=10,
    ) as client:
        details = client.get(f"api/v1/tickets/{ticket_id}", params={"expand": "true"})
        ticket = _Ticket(**details.raise_for_status().json())
        response = client.get(f"api/v1/ticket_articles/by_ticket/{ticket_id}").raise_for_status()
        articles = [_Article(**article) for article in response.json()]
    if ticket.id != ticket_id or any(article.ticket_id != ticket_id for article in articles):
        raise ValueError("Zammad returned a different ticket")
    return ticket, sorted(articles, key=lambda article: (article.created_at, article.id))
