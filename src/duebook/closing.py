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

A short reply can confirm a deed named in the preceding public turn. The
reducer uses both sources to check that the confirmation identifies one
promise, and that the question names nothing the promise does not
(``names_beyond``): two twin promises share every word but one. Legacy replay
alone retains the old weak confirmation rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .proposals import Verdict, Verification

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
#: THAT something happened, rarely WHAT, so they count for less than a word
#: that names a thing (see ``overlap``).
GENERIC = frozenset(
    {
        "получ", "пришл", "присл", "отпра", "вышл", "высл", "сдела", "напиш", "сообщ",
        "дошл", "посту", "появи", "вижу", "увиде", "готов", "нужн", "можно", "отмен",
        "сдел", "буде", "може", "обещ", "send", "sent", "recei", "arriv", "done",
        # Support's own vocabulary: every ticket is a "question" or a "request".
        "вопро", "пробл", "заявк", "обращ", "ситуа", "инфор",
        # How a renegotiation says it: the date moves, the promise does not.
        "срок", "сдвиг", "перен", "задер", "успев", "успее", "продл", "позже", "новый",
        # Regret and apology carry no content either.
        "сожал", "извин", "прост",
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


def content(text: str, generic: bool = False) -> list[Stem]:
    """The content words of a text: no function words, thanks or dates.

    Generic verbs are left out unless ``generic`` is set.
    """
    out = []
    for w in words(text):
        # Short numbers are days, hours and counts of days ("3 рабочих дня",
        # "до 25 января"); an order number or an amount has three digits or more.
        if w in STOPWORDS or len(w) < 3:
            continue
        s = stem(w)
        if not generic and _generic(s):
            continue
        out.append(s)
    return out


def _generic(s: Stem) -> bool:
    return s.head in GENERIC or s.head[:4] in GENERIC


#: What a shared word is worth: one that names the thing, and a generic verb.
NAMING, GENERIC_WEIGHT = 2, 1


def overlap(text: str, target: Iterable[str]) -> int:
    """How much of ``text`` occurs, in some form, in ``target``.

    Each distinct shared word counts once: two points when it names something
    ("деньги", "замену", "склад"), one when it is a generic verb ("напишу",
    "отправим"). A generic verb alone can still tie a close to a promise that
    was worded with nothing else — but never outweighs a word that names.
    """
    theirs = [s for t in target for s in content(t, generic=True)]
    seen: dict[str, int] = {}
    for s in content(text, generic=True):
        if s.head not in seen and any(s.meets(t) for t in theirs):
            seen[s.head] = GENERIC_WEIGHT if _generic(s) else NAMING
    return sum(seen.values())


def names_other(text: str, target: Iterable[str]) -> bool:
    """Does ``text`` name something ``target`` does not, while ``target`` names something
    ``text`` leaves out — "перезвоню насчёт рассрочки" against "перезвоню насчёт оплаты"?

    A renegotiation restates its promise with a new date: it may add a detail
    or drop one, but a promise about a different thing is a new promise.
    """
    added, dropped = _differences(text, target)
    return bool(added) and bool(dropped)


def restates(wording: Iterable[str], target: Iterable[str]) -> bool:
    """Do two wordings name the same things, neither naming anything the other does not?

    Promise identity (decision 0003): "доступ к пространству Альфа" and
    "доступ к пространству Бета" are two promises, however alike their actions.
    """
    text = " ".join(wording)
    added, dropped = _differences(text, target)
    # Wordings that name nothing ("напишу вам") cannot be told apart, so not joined.
    return bool(content(text)) and not added and not dropped


def _differences(text: str, target: Iterable[str]) -> tuple[list[Stem], list[Stem]]:
    """What ``text`` names that ``target`` does not, and what it leaves out of it."""
    mine = content(text)
    theirs = [s for t in target for s in content(t)]
    return _added(mine, theirs), _added(theirs, mine)


def names_beyond(question: str, target: Iterable[str]) -> bool:
    """Does the question a reply confirms name something ``target`` does not?

    "Поступило ли возмещение стоимости монтажа?" is not about "Возместим
    стоимость сборки". Only the sentences that ask are compared: a remark
    beside the question ("По другой позиции ещё уточняем") is not confirmed.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", question) if s.strip()]
    asking = [s for s in sentences if asks_to_confirm(s)] or [question]
    mine = [s for q in asking for s in content(q) if s.word not in _ASKING_WORDS]
    return bool(_added(mine, [s for t in target for s in content(t, generic=True)]))


def _added(mine: list[Stem], theirs: list[Stem]) -> list[Stem]:
    return [s for s in mine if not any(s.meets(t) for t in theirs)]


#: Words that negate a statement, for telling "пришла" from "так и не пришла".
_NEGATIONS = frozenset({"не", "нет", "ни", "not", "no", "never"})


def negated(text: str) -> bool:
    return bool(_NEGATIONS & set(words(text)))


def confirms(text: str) -> bool:
    """Does the text say that something was done (not merely thank)?"""
    return any(w.startswith(c) for w in words(text) for c in CONFIRMING)


def confirms_reply(text: str) -> bool:
    """A short reply can confirm a deed, including one named in the question."""
    tokens = set(words(text))
    if tokens & {"не", "нет", "жду", "одно", "один", "одна", "not", "no", "one", "waiting"}:
        return False
    return confirms(text) or "на месте" in _ws(text) or _ws(text).strip(".!?") in {"да", "yes"}


#: How a turn asks the other side to confirm something.
_ASKING = ("проверьте", "подтвердите", "скажите", "напишите", "confirm", "check whether",
           "let me know", "please check")  # fmt: skip
_ASKING_WORDS = frozenset(w for phrase in _ASKING for w in phrase.split())


def asks_to_confirm(text: str) -> bool:
    folded = _ws(text)
    return "?" in folded or any(word in folded for word in _ASKING)


def quote_identifies(target_quote: str, source_quote: str) -> bool:
    """Is ``target_quote`` a span of the promise's original wording, with a word in it?

    "вам" or "до пятницы" alone would match many promises and identify none.
    """
    span, whole = _norm(target_quote), _norm(source_quote)
    return bool(span) and span in whole and bool(content(span, generic=True) or _has_number(span))


def _has_number(text: str) -> bool:
    return any(w.isdigit() for w in words(text))


def _norm(value: str) -> str:
    folded = value.casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[«»\"“”„.,!?;:]", " ", folded)).strip()


#: Words that acknowledge or wait. Like thanks, they say nothing was done.
ACKNOWLEDGING = frozenset(
    {"жду", "ждем", "ждём", "ожидаю", "ожидаем", "понял", "поняла", "поняли", "принято",
     "принял", "приняла", "договорились", "waiting", "noted", "understood"}
)  # fmt: skip


def vouches(verification: Verification | None, message: str) -> bool:
    """Did a verifier say yes, quoting words that really are in the message?

    A quote the message does not contain is the verifier talking, not the
    ticket: that verdict counts as unsure. So does a quote of nothing but
    thanks, acknowledgement or waiting ("Спасибо!", "Ок, жду"): whatever the
    verifier says, bare thanks closes nothing.
    """
    if verification is None or verification.verdict is not Verdict.YES:
        return False
    evidence = _ws(verification.evidence_quote)
    # Whole words: "все получил" is not in "всё получила".
    if not evidence or not re.search(rf"(?<!\w){re.escape(evidence)}(?!\w)", _ws(message)):
        return False
    return any(s.word not in ACKNOWLEDGING for s in content(evidence, generic=True))


def _ws(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip()
