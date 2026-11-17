-- A ticket held at an event the extractor could not read.
--
-- The event is in the log but has no event_proposals row: it has not been
-- applied, and neither has anything after it. A hold is the visible record of
-- that state, with what failed, how often, and when to try again. It is
-- deleted in the transaction that finally applies the event.

create table holds (
    ticket_id       text        primary key,
    seq             bigint      not null,
    error           text        not null,
    attempts        integer     not null,
    first_failed_at timestamptz not null,
    last_failed_at  timestamptz not null,
    retry_at        timestamptz not null,
    -- What the failed attempts cost. A call that returns nothing usable is
    -- still billed.
    usage           jsonb,
    foreign key (ticket_id, seq) references events (ticket_id, seq)
);

create index holds_retry_at on holds (retry_at);

-- Entries the model produced that could not be read as proposals. Counted
-- next to the refusals: both measure how well the extractor is doing.
alter table event_proposals add column malformed jsonb;
