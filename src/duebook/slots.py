"""Fact slots: one name for one thing.

The card keeps one current value per slot, and every rule about facts —
supersession, contradiction, settlement — is decided by comparing slots. That
only works if the same thing always gets the same slot. In live runs it did
not: the model filed a refund under ``order.refund`` in one message and under
``order.refund_status`` in the next, so an update looked like a second,
unrelated fact, and a disagreement about it between the two sides would have
gone unnoticed.

Asking the model to reuse slots helps and is not enough. So the name it
proposes is normalised here, deterministically, before the reducer compares
anything:

1. spelling — case, separators and camelCase are folded, so ``Order-Status``,
   ``orderStatus`` and ``order.status`` are one name;
2. vocabulary — a controlled list of canonical slots, each with the aliases
   known to drift into it;
3. the card — a name outside the vocabulary is matched against the slots the
   ticket already has, ignoring words that add no meaning (``status``,
   ``state``, ``current``...). ``order.gift_status`` joins an existing
   ``order.gift`` instead of standing next to it.

A name that matches nothing is kept, in its folded spelling. The vocabulary is
deliberately open: refusing every unlisted slot would throw away facts the
list's author did not foresee, and the third step already keeps an unlisted
slot from splitting in two.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: Words that say a slot holds a value without saying which.
FILLER = frozenset({"status", "state", "current", "info", "value", "details"})

#: Canonical slot -> the names that have been seen, or are likely, to mean it.
CANONICAL: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "order.number": ("order.id", "order.no", "order.num"),
        "order.status": (),
        "order.amount": ("order.total", "order.sum", "order.price", "order.cost"),
        "payment.status": ("order.payment", "order.payment_status"),
        "payment.date": ("order.payment_date", "order.paid_at", "payment.paid_at"),
        "payment.amount": ("charge", "charge.amount", "payment.charge", "payment.sum",
                           "payment.charged", "payment.charged_amount", "charged.amount",
                           "issue.amount"),
        "refund.status": ("refund", "order.refund", "order.refund_status", "payment.refund"),
        "refund.amount": ("order.refund_amount", "refund.sum"),
        "delivery.status": ("order.delivery", "order.delivery_status", "shipping.status"),
        "delivery.address": ("order.address", "order.delivery_address", "shipping.address",
                             "address"),
        "delivery.date": ("order.delivery_date", "shipping.date"),
        "account.plan": ("plan", "tariff", "account.tariff", "subscription.plan"),
        "account.email": ("email", "customer.email"),
        "customer.phone": ("phone", "account.phone"),
        "device.model": ("device", "device.name", "product.model", "item.model",
                         "goods.model"),
        "app.version": ("version", "app.build"),
        "issue.summary": ("issue", "problem", "issue.description", "problem.description"),
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """The controlled list a deployment uses. Replace it to fit another domain."""

    canonical: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: CANONICAL)
    filler: frozenset[str] = FILLER
    _alias: dict[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        table: dict[str, str] = {}
        for slot, aliases in self.canonical.items():
            if fold(slot) != slot:
                raise ValueError(f"canonical slot {slot!r} is not in folded spelling")
            for name in (slot, *aliases):
                if table.setdefault(fold(name), slot) != slot:
                    raise ValueError(f"{name!r} is listed under two canonical slots")
        object.__setattr__(self, "_alias", table)

    def slots(self) -> tuple[str, ...]:
        return tuple(self.canonical)

    def normalise(self, slot: str, existing: Iterable[str] = ()) -> str:
        """The slot a proposed name refers to, given the slots a card already has."""
        name = fold(slot)
        if not name:
            return ""
        if name in self._alias:
            return self._alias[name]
        key = self._key(name)
        matches = {s for s in existing if self._key(s) == key}
        if len(matches) == 1:
            return matches.pop()
        # No match, or an ambiguous one: guessing would merge different things.
        return name

    def _key(self, name: str) -> frozenset[str]:
        words = frozenset(re.split(r"[._]", name))
        return (words - self.filler) or words


def fold(slot: str) -> str:
    """One spelling per name: ``Order-Refund Status`` -> ``order.refund_status``."""
    spaced = re.sub(r"(?<=[a-zа-я0-9])(?=[A-ZА-Я])", " ", slot.strip())
    words = [w for w in re.split(r"[^0-9a-zа-яё]+", spaced.casefold()) if w]
    if not words:
        return ""
    return words[0] if len(words) == 1 else f"{words[0]}.{'_'.join(words[1:])}"


DEFAULT = Vocabulary()
