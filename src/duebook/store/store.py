"""The event store: events in, a card snapshot and queued alerts out, atomically.

One transaction per applied event writes four things together — the event,
the proposals made for it, the new card snapshot and any alerts that became
due. Either all of them exist or none does, so there is no moment at which the
snapshot is ahead of the log or an alert exists for a card state that was
never stored.

The log is the truth and the snapshot is a cache of its fold. ``replay``
rebuilds a card from stored events and stored proposals alone, without the
model, which is what makes a card auditable and lets the snapshot be thrown
away and recomputed.

The reducer stays pure: this module calls it, inside the transaction, on
values it has read. Time comes in as ``now`` and is never read from a clock
here.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..card import Card
from ..events import Event, MessagePosted, Role, Side, other_side, remember_turn
from ..lifecycle import AlertPolicy, next_wakeup, notices
from ..proposals import Proposal
from ..reducer import Guards, Rejection, apply
from ..slots import DEFAULT as DEFAULT_VOCABULARY
from ..slots import Vocabulary
from ..temporal import WorkCalendar
from .codec import decode_card, decode_event, decode_proposals, encode
from .outbox import Outbox


class Conflict(ValueError):
    """A different event is already stored under this ticket and seq, or this id.

    Redelivery of the same event is harmless and silently accepted. Two
    different events claiming one position is a fault in the source that must
    not be papered over by keeping whichever arrived first.
    """


@dataclass(frozen=True, slots=True)
class Recorded:
    card: Card
    rejections: tuple[Rejection, ...] = ()
    #: True when the event had been applied before and nothing was written.
    replayed: bool = False
    #: Keys of the alerts this event newly queued.
    enqueued: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Hold:
    """A ticket stopped at an event whose extraction failed."""

    ticket_id: str
    seq: int
    error: str
    attempts: int
    first_failed_at: datetime
    retry_at: datetime
    #: What the failed attempts cost, as stored.
    usage: Any = None


@dataclass(slots=True)
class Store:
    conn: psycopg.Connection
    #: The calendar deadlines are resolved against. Replay reproduces a card
    #: only under the calendar it was built with.
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    policy: AlertPolicy = field(default_factory=AlertPolicy)
    #: Like the calendar, part of what a replay depends on.
    vocab: Vocabulary = DEFAULT_VOCABULARY
    outbox: Outbox = field(init=False)

    def __post_init__(self) -> None:
        self.outbox = Outbox(self.conn)

    # ------------------------------------------------------------ writing ----

    def ingest(self, event: Event, now: datetime) -> bool:
        """Store an event without applying it. False when it was already stored.

        Idempotent on (ticket_id, seq): a redelivered event is recognised and
        ignored. Events may be ingested in any order; they are applied in seq
        order by ``record``.
        """
        body = encode(event)
        with self.conn.transaction():
            row = self.conn.execute(
                "insert into events (ticket_id, seq, event_id, kind, at, body, received_at)"
                " values (%s, %s, %s, %s, %s, %s, %s) on conflict do nothing returning seq",
                (event.ticket_id, event.seq, event.id, body["type"], event.at, Jsonb(body), now),
            ).fetchone()
            if row is not None:
                self._mark_pending(event.ticket_id, now)
                return True
            stored = self.conn.execute(
                "select body from events where ticket_id = %s and seq = %s",
                (event.ticket_id, event.seq),
            ).fetchone()
        if stored is None:
            raise Conflict(f"event id {event.id} is already stored at another seq")
        if stored["body"] != body:
            raise Conflict(f"a different event is stored as {event.ticket_id}#{event.seq}")
        return False

    def record(
        self,
        event: Event,
        proposals: Sequence[Proposal] = (),
        *,
        now: datetime,
        usage: Any = None,
        verifier_usage: Any = None,
        malformed: Any = None,
    ) -> Recorded:
        """Apply one event and persist everything that follows from it.

        Raises ``reducer.OutOfOrder`` when the event is not the ticket's next
        one; nothing is written in that case.
        """
        with self.conn.transaction():
            self.ingest(event, now)
            card = self._lock(event.ticket_id, now)
            previous = self.previous_turn(event) if any(
                getattr(p, "context_quote", "") for p in proposals
            ) else None
            outcome = apply(card, event, proposals, self.cal, self.vocab, previous=previous)
            if outcome.replayed:
                return Recorded(card, replayed=True)

            refused = [
                {"index": _position(proposals, r.proposal), "reason": r.reason.value}
                for r in outcome.rejections
            ]
            self.conn.execute(
                "insert into event_proposals"
                " (ticket_id, seq, proposals, rejections, usage, verifier_usage, malformed,"
                " applied_at) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (event.ticket_id, event.seq, Jsonb(encode(tuple(proposals))), Jsonb(refused),
                 _json(usage), _json(verifier_usage), _json(malformed), now),
            )  # fmt: skip
            # The event is read at last: whatever held the ticket here is over.
            self.conn.execute(
                "delete from holds where ticket_id = %s and seq <= %s",
                (event.ticket_id, event.seq),
            )
            enqueued = self._settle(outcome.card, now)
        return Recorded(outcome.card, outcome.rejections, enqueued=enqueued)

    def wake(self, now: datetime, limit: int = 100) -> int:
        """Queue the alerts of tickets whose wake-up time has come.

        Only rows with ``wake_at <= now`` are read — an index range, not a scan
        of every ticket. Queuing an alert and moving ``wake_at`` forward happen
        in one transaction, so a scheduler killed half-way leaves the ticket
        due and the work is simply done again; the outbox key keeps the second
        pass from queuing anything twice.

        Returns the number of tickets handled.
        """
        with self.conn.transaction():
            rows = self.conn.execute(
                "select card from tickets where wake_at <= %s"
                " order by wake_at limit %s for update skip locked",
                (now, limit),
            ).fetchall()
            for row in rows:
                self._settle(decode_card(row["card"]), now)
        return len(rows)

    def hold(
        self,
        ticket_id: str,
        seq: int,
        error: str,
        *,
        now: datetime,
        retry_at: datetime,
        usage: Any = None,
    ) -> Hold:
        """Record that extraction failed at ``seq``. The ticket stays where it is."""
        with self.conn.transaction():
            row = self.conn.execute(
                "insert into holds (ticket_id, seq, error, attempts, first_failed_at,"
                " last_failed_at, retry_at, usage) values (%s, %s, %s, 1, %s, %s, %s, %s)"
                " on conflict (ticket_id) do update set"
                " attempts = case when holds.seq = excluded.seq then holds.attempts + 1 else 1 end,"
                " first_failed_at = case when holds.seq = excluded.seq"
                "  then holds.first_failed_at else excluded.first_failed_at end,"
                " seq = excluded.seq, error = excluded.error, usage = excluded.usage,"
                " last_failed_at = excluded.last_failed_at, retry_at = excluded.retry_at"
                " returning *",
                (ticket_id, seq, error[:1000], now, now, retry_at, _json(usage)),
            ).fetchone()
        return _hold(row)

    def processed(self, ticket_id: str, seen: int) -> None:
        """Note that the processor advanced the ticket as far as ``seen`` ingests allow.

        ``seen`` is the ``ingested`` count read by ``pending`` BEFORE advancing.
        An event that arrived meanwhile has raised the count past it, so the
        ticket stays pending and is picked up again.
        """
        self.conn.execute(
            "update tickets set processed = greatest(processed, %s) where ticket_id = %s",
            (seen, ticket_id),
        )

    @contextmanager
    def try_exclusive(self, ticket_id: str) -> Iterator[bool]:
        """Like ``exclusive``, but yields False at once when another session has the ticket.

        Lets several processors share the work: one that finds a ticket taken
        moves on to the next instead of queueing behind the model call.
        """
        row = self.conn.execute(
            "select pg_try_advisory_lock(hashtextextended(%s, 0)) as got", (ticket_id,)
        ).fetchone()
        try:
            yield row["got"]
        finally:
            if row["got"]:
                self.conn.execute(
                    "select pg_advisory_unlock(hashtextextended(%s, 0))", (ticket_id,)
                )

    @contextmanager
    def exclusive(self, ticket_id: str) -> Iterator[None]:
        """Hold the ticket for the duration of the block; wait if another worker has it.

        A session-level advisory lock rather than a row lock, because the block
        includes a model call and no transaction should stay open across one.
        If the process dies the session ends and the lock goes with it.
        """
        self.conn.execute("select pg_advisory_lock(hashtextextended(%s, 0))", (ticket_id,))
        try:
            yield
        finally:
            self.conn.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (ticket_id,))

    def rebuild(self, ticket_id: str, now: datetime) -> Card:
        """Throw the snapshot away and recompute it from the log."""
        with self.conn.transaction():
            self._lock(ticket_id, now, read=False)
            card = self.replay(ticket_id)
            self._settle(card, now)
        return card

    # ------------------------------------------------------------ reading ----

    def card(self, ticket_id: str) -> Card:
        """The current snapshot; an empty card for a ticket never seen."""
        row = self.conn.execute(
            "select card from tickets where ticket_id = %s", (ticket_id,)
        ).fetchone()
        return decode_card(row["card"]) if row else Card(ticket_id)

    def event(self, ticket_id: str, seq: int) -> Event | None:
        row = self.conn.execute(
            "select body from events where ticket_id = %s and seq = %s", (ticket_id, seq)
        ).fetchone()
        return decode_event(row["body"]) if row else None

    def events(self, ticket_id: str) -> Iterator[Event]:
        """The acknowledged stream, including events still waiting for processing."""
        rows = self.conn.execute(
            "select body from events where ticket_id = %s order by seq", (ticket_id,),
        )
        return (decode_event(row["body"]) for row in rows)

    def previous_turn(self, event: Event) -> MessagePosted | None:
        side = other_side(event)
        if side is None:
            return None
        roles = [Role.CUSTOMER] if side is Side.CUSTOMER else [Role.AGENT, Role.BOT]
        row = self.conn.execute(
            "select body from events where ticket_id = %s and seq < %s"
            " and body->>'type' = 'MessagePosted' and (body->>'public')::boolean"
            " and body->'author'->>'role' = any(%s) and body->>'text' ~ '[^[:space:]]'"
            " order by seq desc limit 1",
            (event.ticket_id, event.seq, roles),
        ).fetchone()
        return decode_event(row["body"]) if row else None

    def stream(
        self, ticket_id: str, upto: int | None = None
    ) -> Iterator[tuple[Event, tuple[Proposal, ...]]]:
        """Applied events with their proposals, in order."""
        for row in self._applied(ticket_id, upto):
            yield decode_event(row["body"]), decode_proposals(row["proposals"])

    def _applied(self, ticket_id: str, upto: int | None) -> list[dict]:
        return self.conn.execute(
            "select e.body, p.proposals, p.close_rules from events e"
            " join event_proposals p using (ticket_id, seq)"
            " where e.ticket_id = %s and (%s::bigint is null or e.seq <= %s) order by e.seq",
            (ticket_id, upto, upto),
        ).fetchall()

    def replay(self, ticket_id: str, upto: int | None = None) -> Card:
        """The card rebuilt from the log alone — as of ``upto`` when given."""
        card, turns = Card(ticket_id), {}
        for row in self._applied(ticket_id, upto):
            event, proposals = decode_event(row["body"]), decode_proposals(row["proposals"])
            card = apply(card, event, proposals, self.cal, self.vocab,
                         guards=Guards(rules=row["close_rules"]),
                         previous=turns.get(other_side(event))).card  # fmt: skip
            remember_turn(turns, event)
        return card

    def held(self, ticket_id: str) -> Hold | None:
        row = self.conn.execute("select * from holds where ticket_id = %s", (ticket_id,)).fetchone()
        return _hold(row) if row else None

    def holds_due(self, now: datetime, limit: int = 100) -> list[Hold]:
        """Held tickets whose next attempt is due."""
        rows = self.conn.execute(
            "select * from holds where retry_at <= %s order by retry_at limit %s", (now, limit)
        ).fetchall()
        return [_hold(row) for row in rows]

    def pending(self, limit: int = 100) -> list[tuple[str, int]]:
        """Tickets with events ingested since the processor last looked: (ticket_id, ingested).

        Longest-waiting first. Reads the partial index ``tickets_pending``, so
        the cost grows with the work waiting, not with the number of tickets.
        """
        rows = self.conn.execute(
            "select ticket_id, ingested from tickets where ingested > processed"
            " order by ingested_at, ticket_id limit %s",
            (limit,),
        ).fetchall()
        return [(row["ticket_id"], row["ingested"]) for row in rows]

    def backlog(self, ticket_id: str) -> int:
        """How many stored events have not been applied yet."""
        row = self.conn.execute(
            "select count(*) as n from events e where e.ticket_id = %s and not exists"
            " (select 1 from event_proposals p where p.ticket_id = e.ticket_id and p.seq = e.seq)",
            (ticket_id,),
        ).fetchone()
        return row["n"]

    def known(self, ticket_id: str) -> bool:
        """Whether any event of this ticket has been stored."""
        row = self.conn.execute(
            "select 1 from tickets where ticket_id = %s", (ticket_id,)
        ).fetchone()
        return row is not None

    def verifier_counts(self, ticket_id: str | None = None) -> dict[str, int]:
        """Verifier calls and failures on applied events, for one ticket or all."""
        query = (
            "select coalesce(sum((verifier_usage->>'calls')::integer), 0) as verifier_calls,"
            " coalesce(sum((verifier_usage->>'failures')::integer), 0) as verifier_failures"
            " from event_proposals where verifier_usage is not null"
        )
        if ticket_id is not None:
            return self.conn.execute(query + " and ticket_id = %s", (ticket_id,)).fetchone()
        return self.conn.execute(query).fetchone()

    def authors(self, ticket_id: str) -> dict[str, str]:
        """Display names of the ticket's message authors, as their latest message gives them."""
        rows = self.conn.execute(
            "select distinct on (body->'author'->>'id') body->'author'->>'id' as id,"
            " body->'author'->>'name' as name from events"
            " where ticket_id = %s and kind = 'MessagePosted'"
            " order by body->'author'->>'id', seq desc",
            (ticket_id,),
        ).fetchall()
        return {row["id"]: row["name"] for row in rows if row["name"]}

    def held_count(self) -> int:
        return self.conn.execute("select count(*) as n from holds").fetchone()["n"]

    def next_wake(self) -> datetime | None:
        """The earliest instant at which any ticket's alerts change."""
        row = self.conn.execute("select min(wake_at) as at from tickets").fetchone()
        return row["at"]

    # ----------------------------------------------------------- internal ----

    def _mark_pending(self, ticket_id: str, now: datetime) -> None:
        """Count a newly stored event against its ticket, creating the row if needed."""
        self.conn.execute(
            "insert into tickets (ticket_id, last_seq, card, updated_at, ingested, ingested_at)"
            " values (%s, 0, %s, %s, 1, %s) on conflict (ticket_id) do update set"
            " ingested_at = case when tickets.ingested > tickets.processed"
            "  then tickets.ingested_at else excluded.ingested_at end,"
            " ingested = tickets.ingested + 1",
            (ticket_id, Jsonb(encode(Card(ticket_id))), now, now),
        )

    def _lock(self, ticket_id: str, now: datetime, read: bool = True) -> Card | None:
        """Take the ticket's row lock, creating the row for a new ticket.

        ``read=False`` takes the lock without trusting the snapshot under it.
        """
        self.conn.execute(
            "insert into tickets (ticket_id, last_seq, card, updated_at)"
            " values (%s, 0, %s, %s) on conflict (ticket_id) do nothing",
            (ticket_id, Jsonb(encode(Card(ticket_id))), now),
        )
        row = self.conn.execute(
            "select card from tickets where ticket_id = %s for update", (ticket_id,)
        ).fetchone()
        return decode_card(row["card"]) if read else None

    def _settle(self, card: Card, now: datetime) -> tuple[str, ...]:
        """Write the snapshot, queue what is due now, and note when to look again."""
        enqueued = self.outbox.enqueue(notices(card, now, self.policy), now)
        self.conn.execute(
            "update tickets set last_seq = %s, card = %s, wake_at = %s, updated_at = %s"
            " where ticket_id = %s",
            (card.last_seq, Jsonb(encode(card)), next_wakeup(card, now, self.policy), now,
             card.ticket_id),
        )  # fmt: skip
        return enqueued


def _json(value: Any) -> Jsonb | None:
    return Jsonb(value) if value is not None else None


def _hold(row: dict) -> Hold:
    return Hold(
        ticket_id=row["ticket_id"],
        seq=row["seq"],
        error=row["error"],
        attempts=row["attempts"],
        first_failed_at=row["first_failed_at"],
        retry_at=row["retry_at"],
        usage=row["usage"],
    )


def _position(proposals: Sequence[Proposal], proposal: Proposal) -> int:
    # By identity: two equal proposals in one list are still two entries.
    return next(i for i, p in enumerate(proposals) if p is proposal)
