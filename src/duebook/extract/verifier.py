"""A second opinion on the few proposals that would close a promise.

    reader = VerifyingReader(Extractor(cheap), Verifier(careful))
    extraction = reader.extract(card, event)   # proposals carry their verdicts
    card = apply(card, event, extraction.proposals).card

Closing a promise by mistake is silent: it raises no alert and leaves the
brief. The reducer's lexical checks (``closing``) stop most such closes, but
they also stop some true ones — "Спасибо, всё получила" shares no word with
"вернём деньги за доставку". So every proposal that would resolve, cancel or
replace an open commitment — a few per ticket, never an ordinary message — is
put to a separate model call that sees only that commitment (who owes what,
in what words, by when) and the new message with its author, and answers
yes / no / unsure with the words of the message that show it.

The verdict does not decide anything here. It is attached to the proposal
(``Verification``) and the reducer weighs it: a yes whose evidence is really
in the message can stand in for the lexical relevance check, and for nothing
else. Because the verdict travels with the proposal and is stored with it, a
replay reproduces the card without calling any model; the reducer stays pure.

The verifier fails safe. A call that cannot be made, or an answer that cannot
be read, becomes an UNSURE verdict with the error noted — the close is then
judged by the deterministic checks alone, exactly as without a verifier.

The instructions come first and never change; the commitment and the message
follow, so a provider's prompt cache can serve the fixed part.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..card import Card, Commitment
from ..events import Event, MessagePosted
from ..proposals import (
    CancelCommitment,
    MakeCommitment,
    Proposal,
    ResolveCommitment,
    Verdict,
    Verification,
)
from ..temporal import WorkCalendar
from .client import ModelError
from .cost import Usage
from .extractor import Extraction, JsonModel, Reader

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
        "evidence_quote": {"type": "string"},
    },
    "required": ["verdict", "evidence_quote"],
    "additionalProperties": False,
}

VERIFIER_PROMPT = """\
You check ONE proposed change to the record of a customer support ticket. \
Earlier, a party made a promise (a "commitment"). Someone has proposed that a \
NEW MESSAGE closes that promise in one of three ways:
- done: the message says the promised action WAS DONE;
- cancelled: the message says the promise is no longer needed;
- replaced: the message renews THE SAME promise with a new date or new terms.

Decide from the new message alone whether it says that about THIS commitment.

"yes" — the message says so about this commitment. Its words name the \
promised deed or its object ("деньги за доставку пришли", "спасибо за \
звонок"), or confirm receiving exactly what this commitment would deliver \
("всё получила" when the commitment is to send or return something).
"no" — the message is about something else; or it says the action was NOT \
done or is still awaited; or it is only thanks, acknowledgement or waiting \
("спасибо", "ок, жду", "спасибо, что держите в курсе"); or, for "replaced", \
the new promise is about a different thing, even if worded alike.
"unsure" — it could be about this commitment or about something else, or \
you cannot tell.

"evidence_quote": when the verdict is "yes", the words of the new message \
that show it, copied character for character (never from the commitment, \
never paraphrased). Otherwise an empty string.

Judge only what the message says. Who is allowed to close the promise, and \
whether other promises are open, is checked elsewhere.\
"""

_KIND = {
    ResolveCommitment: "done",
    CancelCommitment: "cancelled",
    MakeCommitment: "replaced",
}


def target_of(proposal: Proposal) -> str | None:
    """The commitment a proposal would close, cancel or replace; None for any other."""
    match proposal:
        case ResolveCommitment() | CancelCommitment():
            return proposal.commitment_id
        case MakeCommitment(supersedes=str() as old):
            return old
    return None


def render_request(
    target: Commitment, proposal: Proposal, event: MessagePosted, cal: WorkCalendar
) -> str:
    """The per-call part: the one commitment, the change proposed, the new message."""
    due = "none stated"
    if target.due is not None:
        due = f"{target.due.deadline.due_at.astimezone(cal.tz):%Y-%m-%d %H:%M}"
        if target.due.quote:
            due += f' ("{target.due.quote}")'
    name = f" ({event.author.name})" if event.author.name else ""
    return "\n".join(
        [
            "COMMITMENT",
            f"Owed by: {target.owner.value}",
            f"Action: {target.action}",
            f'Said: "{target.source.quote}"',
            f"Due: {due}",
            "",
            f"PROPOSED CHANGE: {_KIND[type(proposal)]}",
            "",
            "NEW MESSAGE",
            f"From: {event.author.role.value}{name}",
            "Text:",
            event.text,
        ]
    )


@dataclass(slots=True)
class Verifier:
    """Asks a model whether one message closes one commitment."""

    model: JsonModel
    cal: WorkCalendar = field(default_factory=WorkCalendar)

    def verify(
        self, target: Commitment, proposal: Proposal, event: MessagePosted
    ) -> tuple[Verification, Usage]:
        """The verdict, and what asking for it cost (failed calls included)."""
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": render_request(target, proposal, event, self.cal)},
        ]
        name = _model_name(self.model)
        try:
            completion = self.model.complete_json(messages, VERIFY_SCHEMA, name="verdict")
        except ModelError as error:  # fail safe: as if no verifier had been asked
            spent = getattr(error, "usage", None) or Usage()
            return Verification(Verdict.UNSURE, model=name,
                                error=f"{type(error).__name__}: {error}"), spent  # fmt: skip
        try:
            verdict = Verdict(completion.data["verdict"])
            evidence = str(completion.data.get("evidence_quote") or "").strip()
        except (KeyError, ValueError, TypeError) as error:
            unreadable = Verification(Verdict.UNSURE, model=name,
                                      error=f"unreadable verdict: {error}")  # fmt: skip
            return unreadable, completion.usage
        return Verification(verdict, evidence, model=name), completion.usage


@dataclass(slots=True)
class VerifyingReader:
    """An extractor whose destructive proposals come back with a verdict attached.

    Only a proposal that would close, cancel or replace a commitment that is
    open on the card is put to the verifier; one aimed at an unknown or
    already closed commitment is refused by the reducer anyway. The verifier's
    cost is added to the extraction's.
    """

    reader: Reader
    verifier: Verifier

    def extract(self, card: Card, event: Event) -> Extraction:
        extraction = self.reader.extract(card, event)
        if not isinstance(event, MessagePosted):
            return extraction
        spent, proposals = Usage(), []
        for p in extraction.proposals:
            target = card.commitment(target_of(p) or "")
            if target is not None and target.unresolved and p.verification is None:
                verdict, cost = self.verifier.verify(target, p, event)
                p, spent = replace(p, verification=verdict), spent + cost
            proposals.append(p)
        return replace(extraction, proposals=tuple(proposals), usage=extraction.usage + spent)


def _model_name(model: object) -> str:
    """The model's name, through any wrapper that holds the client as ``model``."""
    for _ in range(4):
        model = getattr(model, "model", "")
        if isinstance(model, str):
            return model
    return ""
