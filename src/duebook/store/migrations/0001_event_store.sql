-- The event store, the card snapshot and the alert outbox.
--
-- events is the source of truth and is append-only: a trigger refuses UPDATE
-- and DELETE, so nothing short of a deliberate schema change can rewrite what
-- happened on a ticket. Everything else here is derived from it and can be
-- rebuilt.

create table events (
    ticket_id   text        not null,
    seq         bigint      not null check (seq >= 1),
    event_id    text        not null,
    kind        text        not null,
    at          timestamptz not null,
    body        jsonb       not null,
    received_at timestamptz not null,
    primary key (ticket_id, seq),
    unique (ticket_id, event_id)
);

create function duebook_append_only() returns trigger language plpgsql as $$
begin
    raise exception '% is append-only', tg_table_name using errcode = 'restrict_violation';
end;
$$;

create trigger events_append_only
    before update or delete on events
    for each row execute function duebook_append_only();

-- What the extractor proposed for an event and which proposals the reducer
-- refused. The whole list is kept, in order, not only the admitted part: ids
-- on the card are derived from a proposal's position, so replaying the same
-- list through the same reducer is what reproduces the same card. A row here
-- means "this event has been applied"; an event without one is waiting.
create table event_proposals (
    ticket_id  text        not null,
    seq        bigint      not null,
    proposals  jsonb       not null,
    rejections jsonb       not null,
    usage      jsonb,
    applied_at timestamptz not null,
    primary key (ticket_id, seq),
    foreign key (ticket_id, seq) references events (ticket_id, seq)
);

create trigger event_proposals_append_only
    before update or delete on event_proposals
    for each row execute function duebook_append_only();

-- One row per ticket: the card as of last_seq, and the next instant at which
-- its alerts change. The row doubles as the per-ticket lock.
create table tickets (
    ticket_id  text        primary key,
    last_seq   bigint      not null,
    card       jsonb       not null,
    wake_at    timestamptz,
    updated_at timestamptz not null
);

create index tickets_wake_at on tickets (wake_at) where wake_at is not null;

-- Alerts waiting to be sent. The primary key is Notice.key — what the alert
-- is about, not when somebody noticed — so deriving the same alert twice
-- inserts it once.
create table outbox (
    key            text        primary key,
    ticket_id      text        not null,
    commitment_id  text        not null,
    kind           text        not null,
    due_at         timestamptz not null,
    recipient      text,
    enqueued_at    timestamptz not null,
    available_at   timestamptz not null,
    claim_token    uuid,
    claimed_by     text,
    claimed_at     timestamptz,
    attempts       integer     not null default 0,
    last_error     text,
    delivered_at   timestamptz,
    discarded_at   timestamptz,
    discard_reason text,
    check (delivered_at is null or discarded_at is null)
);

create index outbox_pending on outbox (available_at)
    where delivered_at is null and discarded_at is null;
