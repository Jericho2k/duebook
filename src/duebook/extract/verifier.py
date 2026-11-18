"""Verify proposed changes that would stop a promise from being tracked.

The verifier checks a named target for direct evidence. If a reply depends on
the preceding public turn, it compares every open commitment of the owing
side. The reducer makes the decision from the stored verdict and evidence.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from ..card import Card, Commitment
from ..closing import NAMING, asks_to_confirm, confirms_reply, overlap
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
from .client import DEFAULT_MODEL, ChatClient, ModelError
from .cost import Usage
from .extractor import (
    Extraction,
    JsonModel,
    Reader,
    ReadTurn,
    VerifierUsage,
    read_batch,
    read_event,
)

DEFAULT_VERIFIER_REASONING = "low"
log = logging.getLogger(__name__)

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
        "evidence_quote": {"type": "string"},
    },
    "required": ["verdict", "evidence_quote"],
    "additionalProperties": False,
}

CONTEXT_SCHEMA = {
    **VERIFY_SCHEMA,
    "properties": {**VERIFY_SCHEMA["properties"], **dict.fromkeys(
        ("id", "target_quote", "context_quote"), {"type": "string"}
    )},
    "required": [*VERIFY_SCHEMA["required"], "id", "target_quote", "context_quote"],
}

CONTEXT_PROMPT = """\
Check whether the NEW MESSAGE confirms that exactly one listed promise was fulfilled. \
Compare all candidates. The PRECEDING TURN can identify the subject of a short reply. \
If the reply confirms the action and the preceding question uniquely identifies the promise, \
return verdict yes, its id, an exact target_quote from its original wording, context_quote \
copied from the preceding question naming that subject, and evidence_quote copied from the reply. \
If the reply directly names the subject, use an empty context_quote. \
When the reply could refer to multiple promises, says only thanks, says one of several \
arrived without saying which, or fails to confirm completion, return unsure or no and use \
empty strings for id and all quotes. Never guess based on relative ages or deadlines.\
"""

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
    ) -> tuple[Verification, Usage, str | None]:
        """The verdict, its cost and the provider that answered, if known."""
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": render_request(target, proposal, event, self.cal)},
        ]
        verdict, usage, provider, _ = self._ask(messages, VERIFY_SCHEMA, "verdict")
        return verdict, usage, provider

    def verify_context(
        self, candidates: list[Commitment], proposal: ResolveCommitment, event: MessagePosted,
        previous: MessagePosted,
    ) -> tuple[ResolveCommitment, Usage, str | None]:
        ids = {f"C{i}": c for i, c in enumerate(candidates, 1)}
        lines = [f"{id_} [{c.owner.value}] {c.action} | said: {c.source.quote}"
                 for id_, c in ids.items()]  # fmt: skip
        lines += ["", "PRECEDING TURN", previous.text, "", "NEW MESSAGE", event.text]
        messages = [{"role": "system", "content": CONTEXT_PROMPT},
                    {"role": "user", "content": "\n".join(lines)}]  # fmt: skip
        verdict, usage, provider, data = self._ask(messages, CONTEXT_SCHEMA, "context_verdict")
        if verdict.verdict is Verdict.YES:
            target = ids.get(str(data.get("id", "")).upper())
            quote, context = data.get("target_quote"), data.get("context_quote")
            if target and isinstance(quote, str) and quote and isinstance(context, str):
                proposal = replace(proposal, commitment_id=target.id, target_quote=quote,
                                   context_quote=context, quote=verdict.evidence_quote)
            else:
                verdict = replace(verdict, verdict=Verdict.UNSURE, error="unreadable target")
        return replace(proposal, verification=verdict), usage, provider

    def _ask(self, messages, schema, output):
        name = _model_name(self.model)
        try:
            completion = self.model.complete_json(messages, schema, name=output)
        except ModelError as error:  # fail safe: as if no verifier had been asked
            failure = Verification(Verdict.UNSURE, model=name, error=f"ModelError: {error}")
            return failure, error.usage, error.provider, {}
        try:
            verdict = Verdict(completion.data["verdict"])
            evidence = completion.data["evidence_quote"]
            if not isinstance(evidence, str):
                raise TypeError("evidence_quote must be text")
        except (KeyError, ValueError, TypeError) as error:
            verification = Verification(Verdict.UNSURE, model=name,
                                        error=f"unreadable verdict: {error}")  # fmt: skip
        else:
            verification = Verification(verdict, evidence.strip(), model=name)
        return verification, completion.usage, completion.provider, completion.data


@dataclass(slots=True)
class VerifyingReader:
    """An extractor whose destructive proposals come back with a verdict attached.

    Only a proposal that would close, cancel or replace a commitment that is
    open on the card is put to the verifier; one aimed at an unknown or
    already closed commitment is refused by the reducer anyway.
    """

    reader: Reader
    verifier: Verifier

    def extract(self, card: Card, event: Event) -> Extraction:
        return self.extract_with_context(card, event, None)

    def extract_batch(self, card: Card, turns: list[ReadTurn]) -> dict[int, Extraction]:
        return read_batch(self.reader, card, turns)

    def extract_with_context(
        self, card: Card, event: Event, previous: MessagePosted | None,
    ) -> Extraction:
        return self.verify_extraction(card, event, previous,
                                      read_event(self.reader, card, event, previous))

    def verify_extraction(
        self, card: Card, event: Event, previous: MessagePosted | None, extraction: Extraction,
    ) -> Extraction:
        if not isinstance(event, MessagePosted):
            return extraction
        pending = extraction.proposals
        if previous is not None and not any(isinstance(p, ResolveCommitment) for p in pending):
            pending = (*pending, *_possible_reply(card, event, previous))
        spent, proposals = Usage(), []
        providers: Counter[str] = Counter()
        failures = 0
        for p in pending:
            target = card.commitment(target_of(p) or "")
            if target is not None and target.unresolved and p.verification is None:
                if (previous is not None and isinstance(p, ResolveCommitment)
                    and previous.author.side is target.owner.side
                    and (p.context_quote or overlap(p.quote, (target.source.quote,)) < NAMING)):
                    candidates = [c for c in card.commitments
                                  if c.unresolved and c.owner.side is target.owner.side]
                    p, cost, provider = self.verifier.verify_context(candidates, p, event, previous)
                    verdict = p.verification
                else:
                    verdict, cost, provider = self.verifier.verify(target, p, event)
                    p = replace(p, verification=verdict)
                spent += cost
                providers[provider or "unknown"] += 1
                if verdict.error:
                    failures += 1
                    log.warning("verifier failed for ticket %s event %s target %s (%s)",
                                event.ticket_id, event.seq, target.id, verdict.model)  # fmt: skip
            proposals.append(p)
        if not providers:
            return extraction
        usage = VerifierUsage(providers.total(), failures, spent, dict(providers))
        return replace(extraction, proposals=tuple(proposals), verifier_usage=usage)


def _possible_reply(
    card: Card, event: MessagePosted, previous: MessagePosted,
) -> tuple[Proposal, ...]:
    if not confirms_reply(event.text) or not asks_to_confirm(previous.text):
        return ()
    candidates = [c for c in card.commitments
                  if c.unresolved and c.owner.side is previous.author.side]
    if not candidates:
        return ()
    def fit(c: Commitment) -> int:
        return overlap(previous.text, (c.source.quote, c.action))

    target = max(candidates, key=fit)
    if fit(target) < NAMING:
        return ()
    return (ResolveCommitment(target.id, event.text.strip(), target.source.quote,
                              context_quote=previous.text.strip()),)


def _model_name(model: object) -> str:
    """The model's name, through any wrapper that holds the client as ``model``."""
    for _ in range(4):
        model = getattr(model, "model", "")
        if isinstance(model, str):
            return model
    return ""


def verifying_reader(
    reader: Reader,
    client: JsonModel,
    cal: WorkCalendar,
    *,
    model: str = DEFAULT_MODEL,
    reasoning: str = DEFAULT_VERIFIER_REASONING,
    wrap_model: Callable[[JsonModel], JsonModel] = lambda client: client,
) -> VerifyingReader:
    """Reuse the connection options and route the verifier by its own model preset."""
    if isinstance(client, ChatClient):
        client = replace(client, model=model, reasoning=reasoning, settings=None)
    return VerifyingReader(reader, Verifier(wrap_model(client), cal))
