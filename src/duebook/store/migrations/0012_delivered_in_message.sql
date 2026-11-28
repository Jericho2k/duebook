ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_close_rules_check;
ALTER TABLE event_proposals ADD CHECK (close_rules BETWEEN 1 AND 8);
ALTER TABLE event_proposals ALTER COLUMN close_rules SET DEFAULT 8;
