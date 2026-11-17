"""Recognising a planted value in somebody else's words.

Methods do not agree on names. The card files a plan under ``account.plan``; a
model asked for JSON writes "тариф клиента"; a summary writes "клиент на
тарифе Премиум". Comparing slots would measure naming, not memory. So the
scorer compares VALUES: a planted fact carries a key — the tokens that
identify it — and a method's item matches when every token of the key occurs
in the item's text.

* A token made of digits must occur exactly, after digit groups are joined:
  "3 200" and "3200" are one number, and "3200" does not match "13200".
* A word token is a stem and matches any word that starts with it, so
  "лесн" finds "Лесная", "Лесной" and "лесную". Russian inflects; a stem is
  the cheapest way to survive that without a morphology library.

Keys are chosen by the generator to be unambiguous within a ticket, and a test
checks that no planted value matches another's key.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_DIGIT_GROUPS = re.compile(r"(?<=\d)[\s  ](?=\d{3}\b)")
_TOKEN = re.compile(r"[0-9]+|[a-zа-я]+")


def tokens(text: str) -> list[str]:
    """Lowercase words and numbers, with ё folded and digit groups joined."""
    folded = _DIGIT_GROUPS.sub("", text.casefold().replace("ё", "е"))
    return _TOKEN.findall(folded)


def mentions(text: str, key: Iterable[str]) -> bool:
    """True when every token of ``key`` occurs in ``text`` (numbers exactly, words by stem)."""
    words = tokens(text)
    key = [k.casefold().replace("ё", "е") for k in key]
    if not key:
        return False
    for k in key:
        if k.isdigit():
            if k not in words:
                return False
        elif not any(w.startswith(k) for w in words):
            return False
    return True


def mentions_any(text: str, stems: Iterable[str]) -> bool:
    """True when ``text`` mentions at least one of ``stems``."""
    return any(mentions(text, (s,)) for s in stems)
