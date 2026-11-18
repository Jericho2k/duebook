-- Rule version 5: identifier slots hold a set (decision 0005) and conditional
-- offers (decision 0004). Versions 1-4 replay with one value per slot.
ALTER TABLE event_proposals DROP CONSTRAINT event_proposals_close_rules_check;
ALTER TABLE event_proposals ADD CONSTRAINT event_proposals_close_rules_check
    CHECK (close_rules IN (1, 2, 3, 4, 5));
ALTER TABLE event_proposals ALTER COLUMN close_rules SET DEFAULT 5;
