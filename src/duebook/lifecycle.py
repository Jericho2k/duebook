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

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .card import Card, Commitment, CommitmentState, Confidence, Party


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


@dataclass(frozen=True, slots=True)
class Notice:
    key: str
    kind: NoticeKind
    ticket_id: str
    commitment_id: str
    due_at: datetime
    recipient: str | None


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
                recipient=card.assignee if c.owner is Party.CUSTOMER else c.responsible,
            )
        )
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
    future = [m for m in moments if m > now]
    return min(future) if future else None


def _alertable(c: Commitment, policy: AlertPolicy) -> bool:
    if c.state is not CommitmentState.OPEN or c.due is None:
        return False
    if c.due.confidence < policy.min_confidence:
        return False
    return policy.include_customer or c.owner is not Party.CUSTOMER


def _lead(c: Commitment, policy: AlertPolicy) -> timedelta:
    span = c.due.deadline.due_at - c.created_at
    return min(policy.due_soon_lead, span * policy.max_lead_fraction)
