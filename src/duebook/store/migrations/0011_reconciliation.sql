-- A system check belongs between two helpdesk events without consuming an external seq.
CREATE SEQUENCE event_check_order;
ALTER TABLE events ADD COLUMN check_order bigint NOT NULL DEFAULT 0;
ALTER TABLE event_proposals ADD COLUMN check_order bigint NOT NULL DEFAULT 0;
ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_ticket_id_seq_fkey;
ALTER TABLE holds DROP CONSTRAINT holds_ticket_id_seq_fkey;
ALTER TABLE events DROP CONSTRAINT events_pkey;
ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_pkey;
ALTER TABLE events ADD PRIMARY KEY (ticket_id, seq, check_order);
ALTER TABLE event_proposals ADD PRIMARY KEY (ticket_id, seq, check_order);
ALTER TABLE event_proposals ADD FOREIGN KEY (ticket_id, seq, check_order)
    REFERENCES events (ticket_id, seq, check_order);
ALTER TABLE holds ADD COLUMN check_order bigint GENERATED ALWAYS AS (0::bigint) STORED;
ALTER TABLE holds ADD FOREIGN KEY (ticket_id, seq, check_order)
    REFERENCES events (ticket_id, seq, check_order);
ALTER TABLE tickets ADD COLUMN check_at timestamptz;
UPDATE tickets SET wake_at = least(wake_at, now()) WHERE wake_at IS NOT NULL;
ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_close_rules_check;
ALTER TABLE event_proposals ADD CHECK (close_rules BETWEEN 1 AND 7);
ALTER TABLE event_proposals ALTER COLUMN close_rules SET DEFAULT 7;
