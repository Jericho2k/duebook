"""Database fixtures.

Tests that need PostgreSQL run only when TEST_DATABASE_URL points at a
database they may wipe. CI provides one; without it they are skipped, and the
rest of the suite — the pure core — runs anywhere.
"""

from __future__ import annotations

import os

import pytest

TABLES = "events, event_proposals, tickets, outbox"


@pytest.fixture(scope="session")
def dsn() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    from duebook.store import connect, migrate

    with connect(url) as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
        migrate(conn)
    return url


@pytest.fixture
def conn(dsn):
    from duebook.store import connect

    with connect(dsn) as connection:
        connection.execute(f"truncate {TABLES}")
        yield connection


@pytest.fixture
def store(conn):
    from helpers import CAL

    from duebook.store import Store

    return Store(conn, CAL)
