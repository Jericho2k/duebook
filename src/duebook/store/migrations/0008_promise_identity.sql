-- Rule version 4: promise identity (decision 0003). Versions 1-3 replay with
-- the duplicate rule they were decided under.
ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_close_rules_check;
ALTER TABLE event_proposals ADD CONSTRAINT event_proposals_close_rules_check
    CHECK (close_rules IN (1, 2, 3, 4));
ALTER TABLE event_proposals ALTER COLUMN close_rules SET DEFAULT 4;
