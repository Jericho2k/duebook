"""The alert outbox: a notice is written once, claimed by one worker, recorded once.

``lifecycle.notices`` derives alerts from a card and the clock, and gives each
a key that says what it is about. This table makes that key a primary key, so
however many times a restarted scheduler derives the same alert, it is queued
once.

Sending is the part no database can make atomic: a worker can die after the
message left and before the fact was recorded. The outbox therefore promises
the two things it can actually keep:

* nothing is lost — a claim is a lease, not ownership. A claim older than
  ``claim_ttl`` belongs to a worker presumed dead and is taken over;
* a delivery is recorded once — ``delivered`` only ever sets ``delivered_at``
  on a row where it is still empty.

A notice may therefore be *attempted* twice, only when a worker dies in the
window between sending and recording, and the second attempt carries the same
key. A channel that honours the key as an idempotency key shows the recipient
each alert exactly once.

Every method takes ``now``. The outbox has no clock of its own, which is what
lets a test move time instead of sleeping.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from ..lifecycle import Notice, NoticeKind

_CLAIM = """
with picked as (
    select key from outbox
    where delivered_at is null and discarded_at is null
      and available_at <= %(now)s
      and (claimed_at is null or claimed_at <= %(stale)s)
    order by available_at, key
    limit %(limit)s
    for update skip locked
)
update outbox o
set claim_token = gen_random_uuid(), claimed_by = %(worker)s, claimed_at = %(now)s,
    attempts = o.attempts + 1
from picked
where o.key = picked.key
returning o.*
"""


@dataclass(frozen=True, slots=True)
class Claim:
    """One worker's lease on one notice."""

    notice: Notice
    #: Identifies this lease. A worker that lost its lease to a takeover holds
    #: a token the row no longer carries.
    token: str
    #: How many times the notice has been claimed, this one included.
    attempts: int


@dataclass(slots=True)
class Outbox:
    conn: psycopg.Connection
    #: How long a claim is honoured before another worker may take it over.
    #: Must exceed the longest time a healthy worker needs to send one batch.
    claim_ttl: timedelta = timedelta(minutes=5)

    def enqueue(self, notices: Iterable[Notice], now: datetime) -> tuple[str, ...]:
        """Queue notices. Returns the keys that were new; known ones are left alone.

        Joins the caller's transaction when there is one, so an alert is queued
        atomically with the card change that caused it.
        """
        new = []
        with self.conn.transaction():
            for n in notices:
                row = self.conn.execute(
                    "insert into outbox (key, ticket_id, commitment_id, kind, due_at, recipient,"
                    " enqueued_at, available_at) values (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " on conflict (key) do nothing returning key",
                    (n.key, n.ticket_id, n.commitment_id, n.kind.value, n.due_at, n.recipient,
                     now, now),
                ).fetchone()  # fmt: skip
                if row is not None:
                    new.append(n.key)
        return tuple(new)

    def claim(self, worker: str, now: datetime, limit: int = 10) -> list[Claim]:
        """Lease up to ``limit`` notices that are unsent and not validly claimed.

        SKIP LOCKED lets concurrent workers pass over rows another is in the
        middle of claiming instead of queueing behind them.
        """
        with self.conn.transaction():
            rows = self.conn.execute(
                _CLAIM,
                {"now": now, "stale": now - self.claim_ttl, "limit": limit, "worker": worker},
            ).fetchall()
        return [
            Claim(_notice(row), str(row["claim_token"]), row["attempts"])
            for row in sorted(rows, key=lambda row: (row["available_at"], row["key"]))
        ]

    def delivered(self, claim: Claim, now: datetime) -> bool:
        """Record that the notice was sent. False when that was already recorded.

        Deliberately not conditional on the claim token: the message has left,
        and that is true whether or not this worker's lease expired meanwhile.
        """
        with self.conn.transaction():
            cursor = self.conn.execute(
                "update outbox set delivered_at = %s, last_error = null"
                " where key = %s and delivered_at is null and discarded_at is null",
                (now, claim.notice.key),
            )
        return cursor.rowcount == 1

    def failed(self, claim: Claim, error: str, retry_at: datetime) -> bool:
        """Give the lease back after a failed send, to be retried at ``retry_at``."""
        with self.conn.transaction():
            cursor = self.conn.execute(
                "update outbox set claim_token = null, claimed_by = null, claimed_at = null,"
                " last_error = %s, available_at = %s"
                " where key = %s and claim_token = %s and delivered_at is null",
                (error[:500], retry_at, claim.notice.key, claim.token),
            )
        return cursor.rowcount == 1

    def release(self, claim: Claim) -> bool:
        """Give back a lease without attempting the send, e.g. when the worker is stopping.

        The notice is available again at once and the claim does not count as
        an attempt, so the next worker neither waits for the lease to go stale
        nor backs off for a failure that never happened.
        """
        with self.conn.transaction():
            cursor = self.conn.execute(
                "update outbox set claim_token = null, claimed_by = null, claimed_at = null,"
                " attempts = greatest(attempts - 1, 0)"
                " where key = %s and claim_token = %s and delivered_at is null",
                (claim.notice.key, claim.token),
            )
        return cursor.rowcount == 1

    def discard(self, claim: Claim, reason: str, now: datetime) -> bool:
        """Retire a notice that stopped being true before it was sent."""
        with self.conn.transaction():
            cursor = self.conn.execute(
                "update outbox set discarded_at = %s, discard_reason = %s"
                " where key = %s and claim_token = %s"
                " and delivered_at is null and discarded_at is null",
                (now, reason, claim.notice.key, claim.token),
            )
        return cursor.rowcount == 1

    def pending(self) -> int:
        """Notices neither delivered nor discarded."""
        row = self.conn.execute(
            "select count(*) as n from outbox where delivered_at is null and discarded_at is null"
        ).fetchone()
        return row["n"]


def _notice(row: dict) -> Notice:
    return Notice(
        key=row["key"],
        kind=NoticeKind(row["kind"]),
        ticket_id=row["ticket_id"],
        commitment_id=row["commitment_id"],
        due_at=row["due_at"],
        recipient=row["recipient"],
    )
