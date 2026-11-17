-- Which tickets have stored events the processor has not looked at yet.
--
-- The webhook only ingests; a separate processor applies. It must find the
-- tickets with work without scanning every ticket or every event, so each
-- ticket row counts the events ingested for it, and the processor writes back
-- the count it had seen when it last advanced the ticket. A ticket has work
-- exactly when ingested > processed, and a partial index keeps that set small.
--
-- A counter rather than a flag: an event that arrives while the processor is
-- working raises ``ingested`` past the value the processor read, so marking
-- the ticket done cannot swallow it.

alter table tickets
    add column ingested    bigint      not null default 0,
    add column processed   bigint      not null default 0,
    -- When the ticket last went from "nothing to do" to "has work": the
    -- processor serves the longest-waiting tickets first.
    add column ingested_at timestamptz;

create index tickets_pending on tickets (ingested_at) where ingested > processed;

-- Events stored before this migration and not applied yet are work too.
insert into tickets (ticket_id, last_seq, card, updated_at)
select distinct e.ticket_id, 0,
       jsonb_build_object('type', 'Card', 'ticket_id', e.ticket_id), now()
from events e
where not exists (select 1 from tickets t where t.ticket_id = e.ticket_id);

update tickets t set ingested = 1, ingested_at = now()
where exists (select 1 from events e where e.ticket_id = t.ticket_id and e.seq > t.last_seq);
