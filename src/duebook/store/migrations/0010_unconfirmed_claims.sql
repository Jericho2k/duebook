-- Rule version 6: an unconfirmed claim is checked, and a confirmation aimed at
-- something that is not an open promise is suggested (decision 0006).
-- Versions 1-5 replay as they were decided.
ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_close_rules_check;
ALTER TABLE event_proposals ADD CONSTRAINT event_proposals_close_rules_check
    CHECK (close_rules IN (1, 2, 3, 4, 5, 6));
ALTER TABLE event_proposals ALTER COLUMN close_rules SET DEFAULT 6;
