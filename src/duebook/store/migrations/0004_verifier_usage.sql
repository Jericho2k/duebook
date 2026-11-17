alter table event_proposals add column verifier_usage jsonb;

create index event_proposals_verified on event_proposals (ticket_id)
    where verifier_usage is not null;
