-- An alert about a message nobody could read (decision 0002) is about a
-- message, not a commitment.
alter table outbox
    alter column commitment_id drop not null,
    add column seq bigint;
