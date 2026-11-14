"""Connections and schema migrations.

Migrations are plain SQL files, applied in name order, each in its own
transaction and each recorded once. There is no ORM and no migration
framework: the schema is small, and a reviewer should be able to read exactly
what the database looks like.
"""

from __future__ import annotations

from importlib import resources

import psycopg
from psycopg.rows import dict_row

#: Arbitrary constant: serialises concurrent ``migrate`` calls.
_MIGRATION_LOCK = 7_302_150_001


def connect(dsn: str) -> psycopg.Connection:
    """A connection in autocommit mode; every write opens its own transaction."""
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def migrations() -> list[tuple[str, str]]:
    """(name, sql) for every migration shipped with the package, in order."""
    folder = resources.files(__package__) / "migrations"
    files = sorted(
        (entry for entry in folder.iterdir() if entry.name.endswith(".sql")),
        key=lambda entry: entry.name,
    )
    return [(entry.name.removesuffix(".sql"), entry.read_text(encoding="utf-8")) for entry in files]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Bring the schema up to date. Returns the migrations applied by this call."""
    applied = []
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        conn.execute(
            "create table if not exists schema_migrations ("
            " name text primary key, applied_at timestamptz not null default now())"
        )
        done = {row["name"] for row in conn.execute("select name from schema_migrations")}
        for name, sql in migrations():
            if name in done:
                continue
            conn.execute(sql)
            conn.execute("insert into schema_migrations (name) values (%s)", (name,))
            applied.append(name)
    return applied
