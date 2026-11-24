"""Verify proposed changes that would stop a promise from being tracked.

The verifier checks a named target for direct evidence. If a reply depends on
the preceding public turn, it compares every open commitment of the owing
side. The reducer makes the decision from the stored verdict and evidence.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from ..card import Card, Commitment
from ..closing import NAMING, asks_to_confirm, confirms_reply, overlap
from ..events import Event, MessagePosted, PromiseCheck, other_side
from ..proposals import (
    CancelCommitment,
    MakeCommitment,
    Proposal,
    ResolveCommitment,
    Unreadable,
    Verdict,
    Verification,
)
from ..reducer import identity_candidates
from ..temporal import WorkCalendar
from .client import DEFAULT_MODEL, ChatClient, ModelError, call_kind
from .cost import Usage
from .extractor import Extraction, JsonModel, Reader, VerifierUsage, read_event

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
        case MakeCommitment():
            return proposal.restates or proposal.supersedes
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
        if isinstance(proposal, MakeCommitment) and proposal.restates:
            messages[0]["content"] = REPEAT_VERIFIER_PROMPT
        if isinstance(proposal, ResolveCommitment) and proposal.partial:
            messages[0]["content"] += (" Here done means it reports concrete progress on this "
                                       "promise, even if part remains. Judge the progress only.")
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
            with call_kind("verify"):
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
    verifier: Verifier | None
    reconciler: JsonModel | None = None
    cal: WorkCalendar = field(default_factory=WorkCalendar)

    def extract(self, card: Card, event: Event) -> Extraction:
        return self.extract_with_context(card, event, None)

    def extract_with_context(
        self, card: Card, event: Event, previous: MessagePosted | None,
    ) -> Extraction:
        if isinstance(event, PromiseCheck):
            return self._check(card, event) if self.reconciler else Extraction()
        extraction = read_event(self.reader, card, event, previous)
        if self.reconciler and isinstance(event, MessagePosted):
            extraction = self._identities(card, event, extraction)
        return self.verify_extraction(card, event, previous, extraction)

    def verify_extraction(
        self, card: Card, event: Event, previous: MessagePosted | None, extraction: Extraction,
    ) -> Extraction:
        if self.verifier is None or not isinstance(event, MessagePosted) or any(
            isinstance(p, Unreadable) for p in extraction.proposals
        ):
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
                if _leans_on(previous, p, target):
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
            return replace(extraction, proposals=tuple(proposals))
        usage = VerifierUsage(providers.total(), failures, spent, dict(providers))
        return replace(extraction, proposals=tuple(proposals), verifier_usage=usage)


    def _identities(self, card: Card, event: MessagePosted, extraction: Extraction) -> Extraction:
        proposals = list(extraction.proposals)
        usage = extraction.usage
        for index, p in enumerate(proposals):
            if not isinstance(p, MakeCommitment):
                continue
            candidates = identity_candidates(card, event, p, self.cal)
            if not candidates:
                continue
            data, verification, cost = self._reconcile(
                "identity", IDENTITY_PROMPT, IDENTITY_SCHEMA,
                {"promise": [p.action, p.quote, p.due_quote], "message": event.text,
                 "candidates": {c.id: [*_description(c), date] for c, date in candidates}},
                index, p,
            )
            usage += cost
            target, date = next(((c, date) for c, date in candidates if c.id == data.get("id")),
                                (None, "unclear"))
            quote = data.get("target_quote")
            result = data.get("result")
            if target and isinstance(quote, str) and result in ("repeat", "reschedule"):
                # The model identifies the promise. The temporal resolver distinguishes its dates.
                p = replace(p, restates=target.id if date in ("same", "unstated") else None,
                            supersedes=target.id if date == "different" else None,
                            target_quote=quote, verification=None, reconciliation=verification)
            elif result == "new":
                p = replace(p, supersedes=None, target_quote="", verification=None,
                            reconciliation=replace(verification, verdict=Verdict.NO))
            else:
                p = replace(p, reconciliation=replace(verification, verdict=Verdict.UNSURE))
            proposals[index] = p
        return replace(extraction, proposals=tuple(proposals), usage=usage)

    def _check(self, card: Card, event: PromiseCheck) -> Extraction:
        target = card.commitment(event.commitment_id)
        if target is None or not target.unresolved or not event.messages:
            return Extraction()
        data, _, cost = self._reconcile(
            event.kind.value, CHECK_PROMPT, CHECK_SCHEMA,
            {"promise": _description(target),
             "messages": [[m.id, m.author.side.value, m.text] for m in reversed(event.messages)]},
        )
        result, quote, target_quote = (data.get(k) for k in ("result", "quote", "target_quote"))
        proof = next((m for m in event.messages if m.id == data.get("id")), None)
        if result not in ("kept", "partial") or not all(isinstance(v, str) for v in (
            quote, target_quote,
        )):
            return Extraction(usage=cost)
        proposal = ResolveCommitment(target.id, quote, target_quote,
                                     evidence_id=data.get("id") or "", partial=result == "partial")
        if proof is None or not quote.strip() or quote not in proof.text:
            return Extraction((proposal,), usage=cost)
        context = data.get("context_quote")
        if isinstance(context, str) and context and not proposal.partial:
            previous = max((m for m in event.messages if m.seq < proof.seq and m.text.strip()
                            and m.author.side is other_side(proof)), key=lambda m: m.seq,
                           default=None)
            if previous and context in previous.text:
                proposal = replace(proposal, context_quote=context)
        else:
            previous = None
        checked = self.verify_extraction(card, proof, previous, Extraction((proposal,), usage=cost))
        p = checked.proposals[0]
        if p.commitment_id != target.id:
            p = replace(proposal, verification=Verification(Verdict.NO))
        return replace(checked, proposals=(p,))

    def _reconcile(self, kind, prompt, schema, payload, index=None, original=None):
        name = _model_name(self.reconciler)
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        try:
            with call_kind(f"reconcile_{kind}", index, original):
                answer = self.reconciler.complete_json(messages, schema, name=f"reconcile_{kind}")
        except ModelError as error:
            return {}, Verification(Verdict.UNSURE, model=name, error=str(error)), error.usage
        return answer.data, Verification(Verdict.YES, model=name), answer.usage


IDENTITY_PROMPT = """
Decide whether a new support promise repeats one open promise, reschedules that same promise,
or is new. Focus on the new promise's quoted span, using the full message for context such as
a sentence saying its earlier deadline will be missed. A repeat adds no separate obligation.
Each candidate's last field compares deadlines: same, unstated, different, or unclear.
Use this comparison and never calculate a date.
Repeat includes an explicit reminder of the same deadline. Only different can be a reschedule.
Look at the object, action, recipient, qualifiers, and numbers. A different environment, order,
version, deliverable, or phase is a separate promise. Do not guess between possible targets.
Return repeat or reschedule with a candidate id and a verbatim identifying target_quote from its
original quote. Return new with empty id and target_quote if no unique candidate fits.
"""

REPEAT_VERIFIER_PROMPT = """
Does NEW MESSAGE repeat the same outstanding promise in COMMITMENT? Compare the action,
deliverable, recipient, and any order number, environment, or phase. A repeated date or
extra logistical detail can still be the same promise. Say no if it adds a separate
obligation or leaves the identity unclear. Return yes with a verbatim evidence_quote
from NEW MESSAGE if it is the same obligation, otherwise return no or unsure with an
empty evidence_quote.
"""


CHECK_PROMPT = """
Review one support promise and the public messages after it in chronological order.
Find a message showing that this exact promised action was completed. A delivered link, filename,
or artifact can itself show completion when it is clearly the promised one. A plan, repeated
promise, status enquiry, thanks, or a similar deliverable for a different order or environment
is insufficient. Do not guess based on time or if more than one obligation fits the evidence.
For kept or partial, give the evidence message id, its exact quote, and a target_quote copied
verbatim from the promise's original wording. Partial means concrete progress with work remaining.
Prefer the counterparty's confirmation when both sides mention the completion. For a short
confirmation that answers the other side's immediately preceding question, also quote that
question as context_quote. Otherwise leave it empty. If there is no evidence, return open
with all other fields empty. Only use the supplied messages.
"""


def _reconciliation_schema(results, *fields):
    return {"type": "object", "properties": {
        "result": {"type": "string", "enum": results},
        **{key: {"type": "string"} for key in fields},
    }, "required": ["result", *fields], "additionalProperties": False}


IDENTITY_SCHEMA = _reconciliation_schema(["repeat", "reschedule", "new"], "id", "target_quote")
CHECK_SCHEMA = _reconciliation_schema(
    ["kept", "partial", "open"], "id", "quote", "target_quote", "context_quote"
)


def _description(c: Commitment) -> list[str]:
    return [c.owner.side.value, c.action, c.source.quote,
            c.due.deadline.due_at.isoformat() if c.due else ""]


def _leans_on(previous: MessagePosted | None, p: Proposal, target: Commitment) -> bool:
    """Does a close rely on the preceding turn to name its target? Then every candidate
    of the owing side is compared, not just the one proposed."""
    if previous is None or not isinstance(p, ResolveCommitment):
        return False
    named = overlap(p.quote, (target.source.quote,)) >= NAMING
    return previous.author.side is target.owner.side and (bool(p.context_quote) or not named)


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
    reconciler: JsonModel | None = None,
    verify: bool = True,
) -> VerifyingReader:
    """Reuse the connection options and route the verifier by its own model preset."""
    if isinstance(client, ChatClient):
        client = replace(client, model=model, reasoning=reasoning, settings=None)
    return VerifyingReader(reader, Verifier(client, cal) if verify else None, reconciler, cal)
