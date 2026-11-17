import json
from datetime import datetime, timedelta

import pytest
from helpers import CAL, IVAN, START, persist, promise, story

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from duebook import signing  # noqa: E402
from duebook.api import create_app  # noqa: E402
from duebook.brief import compose  # noqa: E402
from duebook.lifecycle import status_at  # noqa: E402
from duebook.settings import Settings  # noqa: E402
from duebook.store.codec import decode_card, encode  # noqa: E402

pytestmark = pytest.mark.usefixtures("conn")

SECRET = "hook-secret"
KEY = "read-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def client_for(dsn, clock, **overrides) -> TestClient:
    fields = {"database_url": dsn, "timezone": "Europe/Moscow", **overrides}
    return TestClient(create_app(Settings(**fields), clock=clock))


@pytest.fixture
def client(dsn, clock):
    with client_for(dsn, clock) as c:
        yield c


@pytest.fixture
def secured(dsn, clock):
    with client_for(dsn, clock, webhook_secret=SECRET, api_key=KEY) as c:
        yield c


def body(event) -> bytes:
    return json.dumps(encode(event), ensure_ascii=False).encode()


def post(client, event_or_raw, secret=None, raw=False):
    data = event_or_raw if raw else body(event_or_raw)
    headers = {"Content-Type": "application/json"}
    if secret:
        headers[signing.HEADER] = signing.sign(secret, data)
    return client.post("/events", content=data, headers=headers)


# ------------------------------------------------------------- webhook ----


def test_an_event_is_stored_and_acknowledged_without_being_applied(client, store):
    event = story().log[1][0]
    response = post(client, event)
    assert response.status_code == 202
    assert response.json() == {"ticket_id": "T-1", "seq": 2, "new": True}
    assert store.event("T-1", 2) == event and store.card("T-1").last_seq == 0
    assert store.pending() == [("T-1", 1)]


def test_a_redelivered_event_is_acknowledged_as_not_new(client):
    event = story().log[0][0]
    assert post(client, event).json()["new"] is True
    again = post(client, event)
    assert again.status_code == 202 and again.json()["new"] is False


def test_a_different_event_at_an_occupied_position_is_a_conflict(client):
    first, second = story().log[1][0], story().log[2][0]
    post(client, first)
    clash = encode(second)
    clash["seq"] = first.seq
    response = post(client, json.dumps(clash).encode(), raw=True)
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("change", "status"),
    [
        (lambda raw: b"{not json", 400),
        (lambda raw: json.dumps([raw]).encode(), 422),
        (lambda raw: json.dumps({**raw, "seq": "2"}).encode(), 422),
        (lambda raw: json.dumps({**raw, "seq": 0}).encode(), 422),
        (lambda raw: json.dumps({**raw, "text": 5}).encode(), 422),
        (lambda raw: json.dumps({**raw, "at": "2027-01-11T10:00:00"}).encode(), 422),
        (lambda raw: json.dumps({k: v for k, v in raw.items() if k != "author"}).encode(), 422),
        (lambda raw: json.dumps({**raw, "author": {**raw["author"], "role": "boss"}}).encode(),
         422),
        (lambda raw: json.dumps({**raw, "type": "Card"}).encode(), 422),
        (lambda raw: json.dumps({**raw, "type": "Event"}).encode(), 422),
        (lambda raw: json.dumps({**raw, "ticket_id": " "}).encode(), 422),
    ],
)  # fmt: skip
def test_a_malformed_event_is_refused_before_it_reaches_the_append_only_log(
    client, conn, change, status
):
    raw = encode(story().log[1][0])
    assert post(client, change(raw), raw=True).status_code == status
    assert conn.execute("select count(*) as n from events").fetchone()["n"] == 0


def test_an_oversized_event_is_refused_before_it_is_read(client, conn):
    from duebook.api.app import MAX_EVENT_BYTES

    response = client.post(
        "/events", content=b"{" + b" " * MAX_EVENT_BYTES + b"}",
        headers={"content-type": "application/json"},
    )  # fmt: skip
    assert response.status_code == 413
    assert conn.execute("select count(*) as n from events").fetchone()["n"] == 0


def test_with_a_secret_configured_only_a_correctly_signed_event_is_accepted(secured, conn):
    event = story().log[0][0]
    assert post(secured, event).status_code == 401
    assert post(secured, event, secret="wrong").status_code == 401
    data = body(event)
    # A signature of the same event serialised differently is not a signature of these bytes.
    other = json.dumps(encode(event), separators=(",", ":")).encode()
    forged = {signing.HEADER: signing.sign(SECRET, other)}
    assert secured.post("/events", content=data, headers=forged).status_code == 401
    assert conn.execute("select count(*) as n from events").fetchone()["n"] == 0

    assert post(secured, event, secret=SECRET).status_code == 202


def test_the_webhook_needs_the_signature_not_the_api_key(secured):
    event = story().log[0][0]
    assert post(secured, event, secret=SECRET).status_code == 202


def test_signatures_are_compared_safely_whatever_the_header_holds():
    assert signing.verify(SECRET, b"{}", signing.sign(SECRET, b"{}"))
    assert not signing.verify(SECRET, b"{}", "sha256=ünïcode")
    assert not signing.verify(SECRET, b"{}", None)


# --------------------------------------------------------------- reads ----


def test_read_endpoints_require_the_api_key_when_one_is_configured(secured, store):
    persist(store, story())
    for path in ("/tickets/T-1/card", "/tickets/T-1/brief", "/tickets/T-1/status"):
        assert secured.get(path).status_code == 401
        assert secured.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401
        assert secured.get(path, headers=AUTH).status_code == 200


def test_an_unknown_ticket_is_not_found(client):
    for path in ("/tickets/T-x/card", "/tickets/T-x/brief", "/tickets/T-x/status"):
        assert client.get(path).status_code == 404


def test_the_card_is_the_stored_card_with_each_commitments_status_now(client, store, clock):
    ticket = story()
    persist(store, ticket)
    clock.now = START + timedelta(days=2)
    data = client.get("/tickets/T-1/card").json()
    assert decode_card(data["card"]) == ticket.card and data["as_of"] == 7
    assert data["commitment_status"] == {
        c.id: status_at(c, clock.now).value for c in ticket.card.commitments
    }
    assert "breached" in data["commitment_status"].values()


def test_the_card_as_of_an_event_is_replayed_from_the_log(client, store):
    ticket = story()
    persist(store, ticket)
    data = client.get("/tickets/T-1/card", params={"as_of": 4}).json()
    assert decode_card(data["card"]) == store.replay("T-1", upto=4)
    assert data["as_of"] == 4 and data["at"] == ticket.log[3][0].at.isoformat()
    assert client.get("/tickets/T-1/card", params={"as_of": 8}).status_code == 404
    assert client.get("/tickets/T-1/card", params={"as_of": 0}).status_code == 422


def test_the_brief_as_text_is_the_composed_brief_with_authors_names(client, store, clock):
    ticket = story()
    persist(store, ticket)
    clock.now = START + timedelta(days=1, hours=2)
    text = client.get("/tickets/T-1/brief", params={"budget": 400}).text
    names = {"cust-1": "Анна", "agent-ivan": "Иван"}
    assert text == compose(ticket.card, clock.now, budget=400, cal=CAL, names=names).text
    assert "Иван:" in text


def test_the_brief_as_json_links_every_line_to_the_message_it_rests_on(client, store, clock):
    ticket = story()
    persist(store, ticket)
    clock.now = START + timedelta(days=5)
    data = client.get("/tickets/T-1/brief", params={"format": "json"}).json()
    assert data["complete"] is True and data["sections"]
    urgent = data["sections"][0]
    assert urgent["key"] == "urgent" and "ПРОСРОЧЕНО" in urgent["lines"][0]["text"]
    for section in data["sections"]:
        for line in section["lines"]:
            assert line["sources"]
            for src in line["sources"]:
                event = store.event("T-1", src["seq"])
                assert event.id == src["event_id"] and src["quote"] in event.text
                assert src["author"] == event.author.name


def test_a_brief_cut_to_a_budget_says_what_was_left_out(client, store):
    persist(store, story())
    data = client.get("/tickets/T-1/brief", params={"budget": 150, "format": "json"}).json()
    assert data["complete"] is False and data["dropped"]
    assert client.get("/tickets/T-1/brief", params={"budget": 0}).status_code == 422
    assert client.get("/tickets/T-1/brief", params={"format": "xml"}).status_code == 422


def test_status_reports_the_backlog_and_the_hold(client, store):
    ticket = story()
    for event, proposals in ticket.log[:2]:
        store.record(event, proposals, now=START)
    for event, _ in ticket.log[2:]:
        store.ingest(event, START)
    store.hold("T-1", 3, "ModelError: http 503", now=START, retry_at=START + timedelta(minutes=1))
    data = client.get("/tickets/T-1/status").json()
    assert data["last_seq"] == 2 and data["backlog"] == 5
    assert data["hold"]["seq"] == 3 and data["hold"]["attempts"] == 1
    assert datetime.fromisoformat(data["hold"]["retry_at"]) == START + timedelta(minutes=1)


def test_health_counts_pending_alerts_and_held_tickets(client, store):
    persist(store, promise("T-1"), now=START + timedelta(hours=3))
    data = client.get("/health").json()
    assert data == {"database": "ok", "pending_outbox": 1, "held_tickets": 0}
    assert data["pending_outbox"] == store.outbox.pending()


def test_health_reports_an_unreachable_database_instead_of_failing_to_start(clock):
    dead = "postgresql://duebook:duebook@127.0.0.1:1/nowhere?connect_timeout=1"
    with client_for(dead, clock) as c:
        response = c.get("/health")
        assert response.status_code == 503 and response.json()["database"] == "unreachable"


def test_the_recipient_of_an_alert_is_on_the_card_the_api_returns(client, store, clock):
    persist(store, promise("T-1"))
    data = client.get("/tickets/T-1/card").json()
    assert data["card"]["commitments"][0]["responsible"] == IVAN.id
