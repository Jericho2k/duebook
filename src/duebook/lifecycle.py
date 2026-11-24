"""Commitment status and alerts as pure functions of the clock.

The card stores only what events established: open, claimed, fulfilled,
cancelled, superseded. "Due soon" and "breached" are not events — nothing
happens on the ticket at the moment a deadline passes — so they are computed:

    status = status_at(commitment, now)

Two things follow from that, and both matter for reliability:

* there is no background job whose failure leaves a status stale;
* an alert is identified by WHAT it is about (commitment, kind, deadline), not
  by when a worker happened to notice. ``Notice.key`` is that identity. A
  sender that records delivered keys under a unique constraint cannot send
  twice after a restart and cannot lose one, because the same notices are
  simply derived again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .card import Card, Closure, Commitment, CommitmentState, Conditional, Confidence, Party
from .events import AssigneeChanged, CheckKind, MessagePosted, PromiseCheck


class Status(StrEnum):
    OPEN = "open"
    DUE_SOON = "due_soon"
    BREACHED = "breached"
    CLAIMED = "claimed"
    FULFILLED = "fulfilled"
    FULFILLED_LATE = "fulfilled_late"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class NoticeKind(StrEnum):
    DUE_SOON = "due_soon"
    BREACHED = "breached"
    #: A message no attempt could read (decision 0002).
    UNREADABLE = "unreadable"
    #: A conditional offer's condition is due and nothing says whether it holds (0004).
    CONDITION_DUE = "condition_due"
    #: Claimed done by the side that owes it, not confirmed by the deadline (0006).
    CLAIM_UNCONFIRMED = "claim_unconfirmed"


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    #: How long before the deadline to warn.
    due_soon_lead: timedelta = timedelta(hours=4)
    #: ...but never earlier than this share of the commitment's whole span, so
    #: a promise to reply "in 10 minutes" does not warn the moment it is made.
    max_lead_fraction: float = 0.5
    #: Deadlines below this confidence are shown on the card and never alerted
    #: on. A false alarm costs more than a missed one: it teaches people to
    #: ignore the next.
    min_confidence: Confidence = Confidence.MEDIUM
    #: Whether to alert the assignee when the CUSTOMER misses their own deadline.
    include_customer: bool = False
    #: No alert is raised about a deadline that fell before this instant. Set it
    #: to the go-live moment: importing a ticket's history would otherwise
    #: queue a breach alert for every promise broken long ago, a flood that
    #: buries the alerts that can still be acted on. The commitment itself is
    #: unaffected — it is still breached on the card and in the brief; only
    #: the alert is withheld. The same holds for a message posted before it
    #: that could not be read. None alerts on every deadline.
    alerts_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class Notice:
    key: str
    kind: NoticeKind
    ticket_id: str
    #: None for an alert about a message or a conditional offer.
    commitment_id: str | None
    #: The deadline; for an unreadable message, when it was posted; for an unconfirmed
    #: claim, the deadline or the claim, whichever is later.
    due_at: datetime
    recipient: str | None
    #: The message an unreadable, condition-due or unconfirmed-claim alert is about.
    seq: int | None = None


def status_at(c: Commitment, now: datetime, policy: AlertPolicy | None = None) -> Status:
    policy = policy or AlertPolicy()
    due_at = c.due.deadline.due_at if c.due else None

    match c.state:
        case CommitmentState.CANCELLED:
            return Status.CANCELLED
        case CommitmentState.SUPERSEDED:
            return Status.SUPERSEDED
        case CommitmentState.CLAIMED:
            return Status.CLAIMED
        case CommitmentState.FULFILLED:
            # Lateness is judged at the moment the debtor delivered, not at the
            # moment the other side got round to confirming it.
            done_at = c.claimed_at or c.resolved_at
            late = due_at is not None and done_at is not None and done_at > due_at
            return Status.FULFILLED_LATE if late else Status.FULFILLED

    if due_at is None:
        return Status.OPEN
    if now >= due_at:
        return Status.BREACHED
    if now >= due_at - _lead(c, policy):
        return Status.DUE_SOON
    return Status.OPEN


def notices(card: Card, now: datetime, policy: AlertPolicy | None = None) -> list[Notice]:
    """Every alert that is due at ``now``. Idempotent: same inputs, same keys."""
    policy = policy or AlertPolicy()
    result = []
    for c in card.commitments:
        if not _alertable(c, policy):
            continue
        status = status_at(c, now, policy)
        if status is Status.BREACHED:
            kind = NoticeKind.BREACHED
        elif status is Status.DUE_SOON:
            kind = NoticeKind.DUE_SOON
        else:
            continue
        due_at = c.due.deadline.due_at
        result.append(
            Notice(
                key=f"{card.ticket_id}:{c.id}:{kind.value}:{due_at.isoformat()}",
                kind=kind,
                ticket_id=card.ticket_id,
                commitment_id=c.id,
                due_at=due_at,
                recipient=_recipient(card, c),
                seq=next((s.source.seq for s in reversed(c.suggestions)
                          if s.kind is Closure.RESOLVED), None),
            )
        )
    for c in card.commitments:
        at = claim_check_at(c, policy)
        if at is not None and now >= at:
            kind = NoticeKind.CLAIM_UNCONFIRMED
            result.append(Notice(f"{card.ticket_id}:{c.id}:{kind.value}:{at.isoformat()}", kind,
                                 card.ticket_id, c.id, at, _recipient(card, c),
                                 c.resolution.seq))  # fmt: skip
    for k in card.conditionals:
        if _checkable(k, policy) and now >= k.due.deadline.due_at:
            kind, due_at = NoticeKind.CONDITION_DUE, k.due.deadline.due_at
            result.append(Notice(f"{card.ticket_id}:{k.id}:{kind.value}:{due_at.isoformat()}",
                                 kind, card.ticket_id, None, due_at, card.assignee,
                                 k.source.seq))  # fmt: skip
    for u in card.unread:
        if policy.alerts_since is None or u.at >= policy.alerts_since:
            result.append(Notice(f"{card.ticket_id}:{u.id}:{NoticeKind.UNREADABLE.value}",
                                 NoticeKind.UNREADABLE, card.ticket_id, None, u.at,
                                 card.assignee, u.source.seq))  # fmt: skip
    return result


def next_wakeup(card: Card, now: datetime, policy: AlertPolicy | None = None) -> datetime | None:
    """The next instant at which ``notices`` will return something new.

    Lets a scheduler sleep until exactly then instead of polling every ticket.
    """
    policy = policy or AlertPolicy()
    moments = []
    for c in card.commitments:
        if _alertable(c, policy):
            due_at = c.due.deadline.due_at
            moments += [due_at - _lead(c, policy), due_at]
    moments += [k.due.deadline.due_at for k in card.conditionals if _checkable(k, policy)]
    moments += [m for c in card.commitments if (m := claim_check_at(c, policy)) is not None]
    future = [m for m in moments if m > now]
    return min(future) if future else None


def deadline_checks(card: Card, policy: AlertPolicy) -> Iterable[tuple[datetime, Commitment]]:
    for c in card.commitments:
        if _alertable(c, policy):
            due = c.due.deadline.due_at
            for at in sorted({due - _lead(c, policy) - timedelta(hours=1),
                              due - timedelta(minutes=15)}):
                if at > c.created_at:
                    yield at, c


def handoff_checks(card: Card, event: AssigneeChanged) -> Iterable[tuple[datetime, Commitment]]:
    return ((event.at, c) for c in card.commitments
            if c.state is CommitmentState.OPEN and c.owner is not Party.CUSTOMER)


def promise_check(card: Card, c: Commitment, kind: CheckKind, at: datetime,
                  messages: Iterable[MessagePosted]) -> PromiseCheck:
    # UTF-8 bytes bound the token count of the JSON evidence.
    remaining = 6400
    evidence = []
    for m in messages:  # newest first
        if not m.public or m.seq > card.last_seq or m.at > at:
            continue
        if m.seq <= c.source.seq:
            break
        row = json.dumps([m.id, m.author.side.value, m.text], ensure_ascii=False)
        remaining -= len(row.encode()) + 2
        if remaining < 0:
            break
        evidence.append(m)
    position = card.last_seq if kind is CheckKind.HANDOFF else at.isoformat()
    return PromiseCheck(id=f"check:{kind}:{c.id}:{position}",
                        ticket_id=card.ticket_id, seq=card.last_seq, at=at,
                        commitment_id=c.id, kind=kind, messages=tuple(evidence))


def claim_check_at(c: Commitment, policy: AlertPolicy | None = None) -> datetime | None:
    """When a claim the other side has not confirmed needs a person (decision 0006).

    The deadline, or the claim if it came later. It neither reopens the promise nor
    counts as a breach; a confirmation or a cancellation ends the claim, and with it this.
    """
    policy = policy or AlertPolicy()
    if c.state is not CommitmentState.CLAIMED or not c.claim_checked or not _trusted(c, policy):
        return None
    return max(c.due.deadline.due_at, c.claimed_at)


def _alertable(c: Commitment, policy: AlertPolicy) -> bool:
    return c.state is CommitmentState.OPEN and _trusted(c, policy)


def _trusted(c: Commitment, policy: AlertPolicy) -> bool:
    """A deadline to alert on: trusted, inside the alerting window, owed by whom the policy says."""
    if c.due is None:
        return False
    if c.due.confidence < policy.min_confidence:
        return False
    if policy.alerts_since is not None and c.due.deadline.due_at < policy.alerts_since:
        return False
    return policy.include_customer or c.owner is not Party.CUSTOMER


def _recipient(card: Card, c: Commitment) -> str | None:
    return card.assignee if c.owner is Party.CUSTOMER else c.responsible


def _checkable(k: Conditional, policy: AlertPolicy) -> bool:
    """A pending offer with a trusted condition deadline, inside the alerting window."""
    if not k.pending or k.due is None or k.due.confidence < policy.min_confidence:
        return False
    return policy.alerts_since is None or k.due.deadline.due_at >= policy.alerts_since


def _lead(c: Commitment, policy: AlertPolicy) -> timedelta:
    span = c.due.deadline.due_at - c.created_at
    return min(policy.due_soon_lead, span * policy.max_lead_fraction)
