from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient
from helpers import ANNA, CAL, IVAN, OLGA, START, Ticket, persist, promise

from duebook import Party, apply
from duebook.api import create_app
from duebook.card import CommitmentState
from duebook.events import CheckKind
from duebook.extract.client import Completion
from duebook.extract.cost import Usage
from duebook.extract.extractor import Extraction, read_event
from duebook.extract.verifier import verifying_reader
from duebook.lifecycle import AlertPolicy, deadline_checks, handoff_checks, promise_check
from duebook.proposals import MakeCommitment, ResolveCommitment, Verdict, Verification
from duebook.reducer import Reason
from duebook.settings import Settings

YES = Verification(Verdict.YES)
PROMISE = "I will send the invoice"
PROOF = "I received the invoice"


def join(original, repeat):
    ticket = Ticket()
    ticket.say(IVAN, original, MakeCommitment(Party.AGENT, original, original))
    return ticket.say(
        IVAN, repeat, MakeCommitment(Party.COMPANY, repeat, repeat, restates="e1#0",
                                    target_quote="invoice", verification=YES, reconciliation=YES)
    )


def test_reconciled_join_keeps_one_promise():
    ticket = Ticket()
    original = f"{PROMISE} in 8 hours"
    ticket.say(IVAN, original, MakeCommitment(Party.AGENT, "send invoice", original))
    card = ticket.card
    repeat = "I will send the PDF invoice in 8 hours"
    ticket.say(IVAN, repeat)
    event = ticket.log[-1][0]
    reader, verifier, reconciler = Mock(), Mock(), Mock()
    reader.extract_with_context.return_value = Extraction(
        (MakeCommitment(Party.COMPANY, "send PDF invoice", repeat),)
    )
    reconciler.complete_json.return_value = Completion(
        {"result": "reschedule", "id": "e1#0", "target_quote": "invoice"}, Usage()
    )
    verifier.complete_json.return_value = Completion(
        {"verdict": "yes", "evidence_quote": repeat}, Usage()
    )
    model = verifying_reader(reader, verifier, CAL, reconciler=reconciler)
    proposals = read_event(model, card, event).proposals
    assert proposals[0].restates == "e1#0" and proposals[0].supersedes is None
    outcome = apply(card, event, proposals, CAL)
    assert [p.id for p in outcome.card.commitments] == ["e1#0"]
    assert outcome.rejections[0].reason is Reason.DUPLICATE


def test_reconciled_join_cannot_drop_a_naming_word():
    outcome = join(f"{PROMISE} for Alpha", PROMISE)
    assert [p.id for p in outcome.card.commitments] == ["e1#0", "e2#0"]


def earlier_proof(author=ANNA):
    ticket = Ticket()
    ticket.say(IVAN, PROMISE, MakeCommitment(Party.AGENT, "send invoice", PROMISE))
    ticket.wait(minutes=10).say(author, PROOF)
    ticket.wait(minutes=1).assign(IVAN.id, OLGA.id)
    card = ticket.card
    target = card.commitments[0]
    messages = [ticket.log[1][0]]
    check = promise_check(card, target, CheckKind.HANDOFF, ticket.now, messages)
    proposal = ResolveCommitment(target.id, PROOF, "invoice", YES, evidence_id=messages[0].id)
    return ticket, check, proposal


def test_check_closes_on_a_quoted_earlier_message(store, dsn):
    ticket, _, proposal = earlier_proof()
    store.schedule_checks = True
    persist(store, ticket)
    check = store.next_check(store.card(ticket.card.ticket_id))
    app = create_app(Settings(database_url=dsn), clock=lambda: ticket.now)
    with TestClient(app) as client:
        path = f"/tickets/{ticket.card.ticket_id}/brief"
        assert client.get(path).status_code == 202
        recorded = store.record(check, (proposal,), now=ticket.now)
        assert client.get(path).status_code == 200
    result = recorded.card.commitments[0]
    assert result.state is CommitmentState.FULFILLED
    assert result.resolution.event_id == "e2"
    assert result.resolved_at == ticket.log[1][0].at
    assert store.replay(ticket.card.ticket_id) == recorded.card
    assert store.record(check, (proposal,), now=ticket.now).replayed


def test_quote_must_be_in_the_cited_message():
    ticket, check, proposal = earlier_proof()
    other = replace(check.messages[0], id="another", text="Thanks")
    check = replace(check, messages=(other, *check.messages))
    outcome = apply(ticket.card, check, (replace(proposal, evidence_id=other.id),), CAL)
    assert outcome.card == ticket.card
    assert outcome.rejections[0].reason is Reason.UNGROUNDED


def test_debtors_proof_remains_a_claim():
    ticket, check, proposal = earlier_proof(IVAN)
    result = apply(ticket.card, check, (proposal,), CAL).card.commitments[0]
    assert result.state is CommitmentState.CLAIMED
    assert result.claim_checked
    assert result.resolution.event_id == "e2"


def test_late_check_does_not_delay_an_alert(store):
    ticket = promise("late")
    store.schedule_checks = True
    persist(store, ticket)
    check_at = START + timedelta(minutes=105)
    assert store.wake(check_at) == 1
    assert store.next_check(store.card("late")) is not None
    deadline = START + timedelta(hours=2)
    assert store.wake(deadline) == 1
    assert "breached" in store.outbox.claim("worker", deadline)[-1].notice.key


def test_checks_are_scheduled_at_the_required_times():
    ticket = Ticket()
    text = f"{PROMISE} in 8 hours"
    ticket.say(IVAN, text, MakeCommitment(Party.AGENT, "send invoice", text))
    ticket.say(ANNA, "I will send the receipt", MakeCommitment(
        Party.CUSTOMER, "send receipt", "I will send the receipt"
    ))
    ticket.assign(IVAN.id, OLGA.id)
    assert [at for at, _ in deadline_checks(ticket.card, AlertPolicy())] == [
        START + timedelta(hours=3), START + timedelta(hours=7, minutes=45)
    ]
    assert [c.id for _, c in handoff_checks(ticket.card, ticket.log[-1][0])] == ["e1#0"]


def delivery(text, quote, *, action="send meeting link"):
    ticket = Ticket()
    ticket.say(IVAN, action, MakeCommitment(Party.AGENT, action, action))
    card = ticket.card
    ticket.say(IVAN, text)
    reader, verifier = Mock(), Mock()
    reader.extract_with_context.return_value = Extraction(
        (ResolveCommitment("e1#0", text, action),)
    )
    verifier.complete_json.return_value = Completion(
        {"verdict": "yes", "evidence_quote": text, "delivery_quote": quote}, Usage()
    )
    event = ticket.log[-1][0]
    proposals = read_event(verifying_reader(reader, verifier, CAL), card, event).proposals
    return apply(card, event, proposals, CAL).card.commitments[0]


def test_a_delivered_link_closes():
    link = "https://example.com/meeting"
    result = delivery(f"Here is the meeting link: {link}", link)
    assert result.state is CommitmentState.FULFILLED and result.resolution.quote == link


def test_a_report_of_work_elsewhere_stays_a_claim():
    assert delivery("I sent the meeting link separately.", "").state is CommitmentState.CLAIMED


def test_delivery_without_a_verbatim_quote_stays_a_claim():
    assert delivery("Here is the meeting link: https://example.com", "https://example.net"
                    ).state is CommitmentState.CLAIMED


def test_m0006_waybill_report_stays_a_claim():
    assert delivery("Накладная уже у вас — по ней отправлен VC-858", "",
                    action="Пришлём вам накладную").state is CommitmentState.CLAIMED
