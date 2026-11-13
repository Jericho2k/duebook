from datetime import datetime, timedelta

from helpers import ANNA, IVAN, MSK, OLGA, Ticket

from duebook import AlertPolicy, NoticeKind, Party, Status, next_wakeup, notices, status_at
from duebook.proposals import CancelCommitment, MakeCommitment, ResolveCommitment
from duebook.temporal import Offset, Unit, WeekdayRef

FRIDAY_END = datetime(2027, 1, 15, 23, 59, 59, tzinfo=MSK)


def refund_ticket(**overrides) -> Ticket:
    t = Ticket()
    t.assign(None, IVAN.id)
    fields = dict(
        owner=Party.COMPANY,
        action="вернуть деньги",
        quote="вернём деньги до пятницы",
        due=WeekdayRef(4),
        due_quote="до пятницы",
    )
    t.say(IVAN, "Мы вернём деньги до пятницы.", MakeCommitment(**{**fields, **overrides}))
    return t


def test_status_moves_with_the_clock_and_nothing_else():
    t = refund_ticket()
    (c,) = t.card.commitments
    assert status_at(c, t.now) is Status.OPEN
    assert status_at(c, FRIDAY_END - timedelta(hours=5)) is Status.OPEN
    assert status_at(c, FRIDAY_END - timedelta(hours=4)) is Status.DUE_SOON
    assert status_at(c, FRIDAY_END) is Status.BREACHED


def test_a_commitment_without_a_deadline_never_breaches():
    t = refund_ticket(quote="вернём деньги", due=None, due_quote="")
    assert status_at(t.card.commitments[0], t.now + timedelta(days=365)) is Status.OPEN


def test_a_short_promise_does_not_warn_the_moment_it_is_made():
    t = Ticket()
    t.say(
        IVAN,
        "Отвечу через 10 минут.",
        MakeCommitment(
            Party.AGENT, "ответить", "Отвечу через 10 минут", due=Offset(10, Unit.MINUTE)
        ),
    )
    (c,) = t.card.commitments
    assert status_at(c, t.now) is Status.OPEN
    assert status_at(c, t.now + timedelta(minutes=5)) is Status.DUE_SOON


def test_lateness_is_judged_when_the_debtor_delivered_not_when_it_was_confirmed():
    t = refund_ticket()
    cid = t.card.commitments[0].id
    t.wait(days=3).say(IVAN, "Деньги отправили", ResolveCommitment(cid, "Деньги отправили"))
    assert status_at(t.card.commitment(cid), FRIDAY_END + timedelta(days=9)) is Status.CLAIMED

    t.wait(days=10).say(ANNA, "Деньги пришли", ResolveCommitment(cid, "Деньги пришли"))
    assert status_at(t.card.commitment(cid), t.now) is Status.FULFILLED


def test_fulfilled_after_the_deadline_is_fulfilled_late():
    t = refund_ticket()
    cid = t.card.commitments[0].id
    t.wait(days=7).say(ANNA, "Деньги пришли", ResolveCommitment(cid, "Деньги пришли"))
    assert status_at(t.card.commitment(cid), t.now) is Status.FULFILLED_LATE


def test_notices_are_derived_and_keyed_by_what_they_are_about():
    t = refund_ticket()
    assert notices(t.card, t.now) == []

    (soon,) = notices(t.card, FRIDAY_END - timedelta(hours=1))
    assert soon.kind is NoticeKind.DUE_SOON and soon.recipient == IVAN.id

    (breach,) = notices(t.card, FRIDAY_END + timedelta(minutes=1))
    assert breach.kind is NoticeKind.BREACHED

    # Asking again — after a restart, an hour later, a day later — yields the
    # same key, which is what lets a unique constraint make delivery exactly-once.
    (later,) = notices(t.card, FRIDAY_END + timedelta(days=1))
    assert later.key == breach.key != soon.key


def test_the_alert_goes_to_whoever_holds_the_promise_now():
    t = refund_ticket()
    t.assign(IVAN.id, OLGA.id)
    assert notices(t.card, FRIDAY_END)[0].recipient == OLGA.id

    t.assign(OLGA.id, None)
    assert notices(t.card, FRIDAY_END)[0].recipient is None


def test_a_disputed_deadline_is_shown_but_never_alerted_on():
    t = refund_ticket(due=WeekdayRef(3))  # the model said Thursday, the phrase says Friday
    (c,) = t.card.commitments
    thursday_end = FRIDAY_END - timedelta(days=1)
    assert status_at(c, thursday_end) is Status.BREACHED
    assert notices(t.card, thursday_end) == []
    assert next_wakeup(t.card, t.now) is None


def test_settled_commitments_are_silent():
    for proposal, author in ((ResolveCommitment, IVAN), (CancelCommitment, ANNA)):
        t = refund_ticket()
        cid = t.card.commitments[0].id
        t.say(author, "вопрос закрыт", proposal(cid, "вопрос закрыт"))
        assert notices(t.card, FRIDAY_END + timedelta(days=1)) == []


def test_a_customers_own_deadline_alerts_only_when_asked_to():
    t = Ticket()
    t.assign(None, IVAN.id)
    t.say(
        ANNA,
        "Пришлю чек завтра",
        MakeCommitment(Party.CUSTOMER, "прислать чек", "Пришлю чек завтра"),
    )
    later = t.now + timedelta(days=3)
    assert notices(t.card, later) == []
    (notice,) = notices(t.card, later, AlertPolicy(include_customer=True))
    assert notice.recipient == IVAN.id


def test_next_wakeup_names_each_moment_something_becomes_due():
    t = refund_ticket()
    first = next_wakeup(t.card, t.now)
    assert first == FRIDAY_END - timedelta(hours=4)
    assert next_wakeup(t.card, first) == FRIDAY_END
    assert next_wakeup(t.card, FRIDAY_END) is None
