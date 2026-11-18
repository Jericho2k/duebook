ALTER TABLE event_proposals ADD COLUMN close_rules SMALLINT NOT NULL DEFAULT 1
    CHECK (close_rules IN (1, 2));
ALTER TABLE event_proposals ALTER COLUMN close_rules SET DEFAULT 2;
