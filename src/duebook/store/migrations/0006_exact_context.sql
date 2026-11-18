ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_close_rules_check;
ALTER TABLE event_proposals ADD CONSTRAINT event_proposals_close_rules_check
    CHECK (close_rules IN (1, 2, 3));
ALTER TABLE event_proposals ALTER COLUMN close_rules SET DEFAULT 3;
