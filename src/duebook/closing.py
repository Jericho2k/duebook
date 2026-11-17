"""Is a closing message about the promise it closes? A deterministic second opinion.

Closing a promise — "done", "cancelled", "replaced by a new date" — is the
most expensive thing the extractor can get wrong. A promise closed by mistake
is silent: it raises no alert, the brief stops listing it, and the customer is
owed something nobody will be reminded of. A close that is missed is the
opposite: the promise stays on the card, an alert fires, somebody looks. So
closing fails safe — when in doubt, the promise stays open — and this module
supplies the doubt.

The model names the promise twice, independently: by its handle (C2) and by a
span copied from that promise's original wording. ``quote_identifies`` checks
the second against the first. Then the closing words are compared with the
promise's own words (``relevance``): a confirmation that shares no content
word with the promise ("спасибо", "хорошо, жду") is not evidence about it.
When the closing words fit another open promise as well as the chosen one
(two refunds open, "деньги пришли"), it is evidence about neither
(``ambiguous`` in the reducer).

Words are compared by crude stems, not a morphology library: lowercase, ``ё``
folded, the first five letters, and — for verbs with a Russian prefix — the
first four letters of the rest, so "перезвоню", "перезвонили" and "звонок"
meet. Function words, thanks, deadline words and the verbs every promise uses
("получила", "пришлём", "отправим") are not content: they would make every
confirmation look relevant to every promise.

Numbers count only from three digits up: an order number or an amount names
a thing, "3 дня" or "до 25 января" only a deadline.

A few nouns and verbs of one deed have different roots ("возврат" and
"вернуть"); ``SYNONYMS`` joins them. It is short on purpose and specific to
support tickets: every entry makes some pair of promises harder to tell apart.

A message with no content word in common can still close a promise when it
plainly confirms that something was done ("всё получила", "пришло") and only
one promise of that side is open: then there is nothing else it could mean.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_WORD = re.compile(r"[0-9]+|[a-zа-я]+")

#: Prefixes stripped to find a verb's root, longest first.
_PREFIXES = ("пере", "пред", "при", "про", "под", "раз", "рас", "вос", "вы", "за", "от", "до",
             "по", "на", "об", "со")  # fmt: skip

#: Words that carry no content: function words, politeness, deadline phrasing.
_STOP_TEXT = """
    и а но или да нет не ни же ли бы то это этот эта эти тот та те так там тут
    вот уже ещё еще всё все весь вся всего очень просто только тоже также
    я мы вы ты он она оно они меня мне мной нас нам нами вас вам вами его ему
    её ее ей их им ими себя свой свою своё свои мой моя моё мои наш наша наше
    наши ваш ваша ваше ваши
    в во на за по под над о об от до из к ко с со у при про для без через
    после перед между около насчёт насчет поводу
    что как когда где куда чтобы если раз который которая которое которые
    спасибо благодарю благодарим спасибочки пожалуйста здравствуйте добрый
    день доброе утро вечер привет хорошо ладно ок окей понятно ясно отлично
    супер угу ага да
    сегодня завтра послезавтра течение конца конец часа часов часа минут дня
    дней дня рабочих рабочий рабочего недели неделю неделя месяца числа
    понедельника вторника среды четверга пятницы субботы воскресенья
    понедельник вторник среду среда четверг пятницу пятница субботу воскресенье
    января февраля марта апреля мая июня июля августа сентября октября ноября
    декабря
    the a an and or but not no to of in on at by for with from about as is
    are was were be been it this that these those i we you he she they me us
    my our your his her their thanks thank ok okay please hi hello today
    tomorrow within hours hour days day business week end monday tuesday
    wednesday thursday friday saturday sunday
"""
STOPWORDS = frozenset(_STOP_TEXT.split())

#: Stems of verbs that nearly every promise or confirmation uses. They say
#: THAT something happened, never WHAT, so they do not count as shared content.
GENERIC = frozenset(
    {
        "получ", "пришл", "присл", "отпра", "вышл", "высл", "сдела", "напиш", "сообщ",
        "дошл", "посту", "появи", "вижу", "увиде", "готов", "нужн", "можно", "отмен",
        "сдел", "буде", "може", "обещ", "send", "sent", "recei", "arriv", "done",
        # Support's own vocabulary: every ticket is a "question" or a "request".
        "вопро", "пробл", "заявк", "обращ", "ситуа", "инфор",
    }
)  # fmt: skip

#: Different roots for one deed, as support tickets name it: "возврат" is the
#: noun of "вернуть", "звонок" of "перезвонить". Stems in one group meet.
SYNONYMS = (
    frozenset({"возвр", "верну", "верне", "верни", "refun"}),
    frozenset({"звон", "зван", "перез", "call"}),
    frozenset({"оплат", "плат", "списа", "charg", "payme"}),
)

#: Stems that confirm SOMETHING was done, without saying what.
CONFIRMING = frozenset(
    {
        "получ", "пришл", "пришё", "приш", "дошл", "посту", "появи", "вижу", "сдела",
        "готов", "верну", "перез", "звони", "заработ", "работа", "recei", "arriv",
        "done", "works", "worke", "got", "settl", "compl", "paid", "issue",
    }
)  # fmt: skip


def words(text: str) -> list[str]:
    return _WORD.findall(text.casefold().replace("ё", "е"))


@dataclass(frozen=True, slots=True)
class Stem:
    """A content word reduced to what two forms of it share."""

    word: str
    #: The first five letters (the whole word when shorter).
    head: str
    #: For a prefixed verb, the first four letters after the prefix.
    root: str | None

    def meets(self, other: Stem) -> bool:
        if self.head == other.head:
            return True
        # A four-letter word is its own head: "счёт" meets "счёта".
        short, long_ = sorted((self, other), key=lambda s: len(s.head))
        if len(short.head) == 4 and long_.word.startswith(short.head):
            return True
        mine = {self.root, self.word[:4]} - {None}
        theirs = {other.root, other.word[:4]} - {None}
        if any(_in(self, group) and _in(other, group) for group in SYNONYMS):
            return True
        # Only a stripped prefix may bridge two words, so "доставку" and
        # "доступ" (both "дост") stay apart while "перезвоню" and "звонок" meet.
        return bool((self.root and self.root in theirs) or (other.root and other.root in mine))


def _in(s: Stem, group: frozenset[str]) -> bool:
    return any(s.word.startswith(g) or (s.root or "").startswith(g) for g in group)


def stem(word: str) -> Stem:
    root = None
    for prefix in _PREFIXES:
        rest = word[len(prefix) :]
        if word.startswith(prefix) and len(rest) >= 4:
            root = rest[:4]
            break
    return Stem(word, word[:5], root)


def content(text: str) -> list[Stem]:
    """The content words of a text: no function words, thanks, dates or generic verbs."""
    out = []
    for w in words(text):
        # Short numbers are days, hours and counts of days ("3 рабочих дня",
        # "до 25 января"); an order number or an amount has three digits or more.
        if w in STOPWORDS or len(w) < 3:
            continue
        s = stem(w)
        if s.head in GENERIC or s.head[:4] in GENERIC:
            continue
        out.append(s)
    return out


def overlap(text: str, target: Iterable[str]) -> int:
    """How many distinct content words of ``text`` occur, in some form, in ``target``."""
    theirs = [s for t in target for s in content(t)]
    seen: set[str] = set()
    for s in content(text):
        if s.head not in seen and any(s.meets(t) for t in theirs):
            seen.add(s.head)
    return len(seen)


def confirms(text: str) -> bool:
    """Does the text say that something was done (not merely thank)?"""
    return any(w.startswith(c) for w in words(text) for c in CONFIRMING)


def quote_identifies(target_quote: str, source_quote: str) -> bool:
    """Is ``target_quote`` a span of the promise's original wording, with content in it?"""
    span, whole = _norm(target_quote), _norm(source_quote)
    return bool(span) and span in whole and bool(content(span) or _has_number(span))


def _has_number(text: str) -> bool:
    return any(w.isdigit() for w in words(text))


def _norm(value: str) -> str:
    folded = value.casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[«»\"“”„.,!?;:]", " ", folded)).strip()
