from functools import partial

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from duebook.api import create_app, zammad  # noqa: E402
from duebook.settings import Settings  # noqa: E402

PAGE = "/zammad/tickets/17"
CONVERSATION = PAGE + "/conversation"
HEADERS = {"Authorization": "Bearer agent-token"}
TICKET = {"id": 17, "number": "202600017", "title": "Refund", "owner": "Olga"}


@pytest.fixture
def client():
    settings = Settings(
        database_url="postgresql:///unused",
        zammad_url="https://support.example.test/helpdesk/",
        api_key="duebook-key",
    )
    return TestClient(create_app(settings))


def test_workspace_page_can_open_from_zammad_without_a_token(client):
    response = client.get(PAGE)
    assert response.status_code == 200
    assert 'id="db-zammad-token"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.headers["content-security-policy"]


def test_conversation_requires_the_viewers_token(client):
    assert client.get(CONVERSATION).status_code == 401
    assert client.get(CONVERSATION, headers={"Authorization": "Bearer a b"}).status_code == 401


def test_conversation_reads_sorted_text_and_preserves_zammad_identity(client, monkeypatch):
    def article(number, body, content_type="text/plain", internal=False):
        return {
            "id": number,
            "ticket_id": 17,
            "from": "Support",
            "body": body,
            "content_type": content_type,
            "internal": internal,
            "created_at": f"2026-11-19T12:{number:02}:00Z",
        }

    def respond(request):
        assert request.method == "GET"
        assert request.headers["authorization"] == "Token token=agent-token"
        if request.url.path == "/helpdesk/api/v1/tickets/17":
            assert request.url.params["expand"] == "true"
            return httpx.Response(200, json=TICKET)
        assert request.url.path == "/helpdesk/api/v1/ticket_articles/by_ticket/17"
        return httpx.Response(
            200,
            json=[
                article(22, "Internal\nfollow-up", internal=True),
                article(
                    21,
                    "<div>Refund &amp; receipt<br>Tomorrow</div><script>hidden</script>",
                    "text/html; charset=utf-8",
                ),
            ],
        )

    monkeypatch.setattr(
        zammad, "Client", partial(httpx.Client, transport=httpx.MockTransport(respond))
    )
    response = client.get(CONVERSATION, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["number"] == TICKET["number"]
    assert data["url"] == "https://support.example.test/helpdesk/#ticket/zoom/17"
    assert data["messages"] == [
        {
            "id": 21,
            "author": "Support",
            "time": "19.11.2026, 15:21 MSK",
            "internal": False,
            "body": "Refund & receipt\nTomorrow",
        },
        {
            "id": 22,
            "author": "Support",
            "time": "19.11.2026, 15:22 MSK",
            "internal": True,
            "body": "Internal\nfollow-up",
        },
    ]


def test_zammad_denial_prevents_a_transcript_lookup(client, monkeypatch):
    paths = []

    def respond(request):
        paths.append(request.url.path)
        return httpx.Response(403, json={"error": "private upstream details"})

    monkeypatch.setattr(
        zammad, "Client", partial(httpx.Client, transport=httpx.MockTransport(respond))
    )
    response = client.get(CONVERSATION, headers=HEADERS)
    assert response.status_code == 403
    assert "private upstream details" not in response.text
    assert paths == ["/helpdesk/api/v1/tickets/17"]


def test_unsigned_ingestion_cannot_write_a_zammad_pilot_stream(client):
    payload = {
        "type": "AssigneeChanged", "id": "initial:owner", "seq": 1,
        "ticket_id": zammad.stream_id("https://support.example.test/", 17),
        "at": "2026-11-19T12:00:00Z", "previous": None, "current": "27",
    }
    assert client.post("/events", json=payload).status_code == 403
