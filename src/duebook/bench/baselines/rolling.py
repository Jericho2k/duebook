"""Baseline (b): a rolling summary, updated after every event.

The traditional answer to a long ticket: keep a summary, and fold each new
message into it. The model sees the previous summary and one new event and
returns the next summary. At a checkpoint, the state of the ticket is read
from the summary alone — the transcript is gone, as it is for an operator who
only gets the summary.

The summary is told what an operator needs from it — every promise with its
message number and an absolute deadline, current facts and what changed,
open disagreements, fixes that failed — so that it fails, where it fails, for
being a summary and not for being asked the wrong thing. It is bounded in
length, as summaries are; the bound is generous for a 60-message ticket.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ...extract.extractor import JsonModel
from ...temporal import WorkCalendar
from ..generate import SimTicket
from ..meter import Ledger, Meter
from ..methods import MethodRun
from ..result import Snapshot, checkpoints
from .answer import ANSWER_SCHEMA, parse_answer, question, render_event

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

SUMMARY_PROMPT = """\
You maintain a running summary of a customer support ticket. An operator who \
takes the ticket over will rely on this summary alone: the transcript will not \
be available to anyone later. You get the summary so far and ONE new event. \
Return the updated summary.

The summary must keep:
- every promise: who promised what, in which message (#N), the deadline as \
said AND as an absolute local date or date and time, resolved against the \
time of that message ("by Friday" said on Mon 2027-01-11 -> by end of Fri \
2027-01-15), and whether it has been done, confirmed by the other side, or \
not. A customer saying "you promised" is not a new promise.
- the current facts of the case (order number, plan, address, amounts, \
product...). When one changes, keep the new value and note that the old one \
no longer holds.
- disagreements between the customer and the company, until one side \
concedes.
- fixes or steps that were tried, and whether they worked; never drop one \
that failed.
- who handles the ticket now.

Drop greetings, thanks and status pings. Keep message numbers (#N). At most \
{words} words.

Return JSON: {"summary": "..."}\
"""


@dataclass(slots=True)
class RollingSummary:
    model: JsonModel
    ledger: Ledger
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    words: int = 500
    name: str = "rolling_summary"

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger)
        stops = {cp.seq: cp for cp in checkpoints(ticket)}
        summary, snaps, malformed, kept, failure = "", [], [], [], None
        system = SUMMARY_PROMPT.replace("{words}", str(self.words))
        for event in ticket.events:
            if failure is None:
                user = (
                    f"SUMMARY SO FAR\n{summary or '(empty)'}\n\n"
                    f"NEW EVENT ({self.cal.tz.key} time)\n{render_event(event, self.cal)}"
                )
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
                meter.answering = False
                try:
                    completion = meter.complete_json(messages, SUMMARY_SCHEMA, name="summary")
                    text = completion.data.get("summary")
                    if not isinstance(text, str):
                        raise ValueError("summary is not text")
                    summary = text.strip()
                except Exception as error:
                    # The summary cannot skip a message any more than the card
                    # can: the ticket stops here and is judged on what it has.
                    failure = f"seq {event.seq}: {type(error).__name__}: {error}"
            cp = stops.get(event.seq)
            if cp is None:
                continue
            meter.answering = True
            try:
                completion = meter.complete_json(
                    question("SUMMARY (the transcript is not available)", summary, cp, self.cal),
                    ANSWER_SCHEMA,
                    name="ticket_state",
                )
            except Exception as error:
                snaps.append(Snapshot(cp, error=f"{type(error).__name__}: {error}"))
                failure = failure or f"answer at {cp.seq}: {type(error).__name__}: {error}"
                continue
            read = parse_answer(completion.data, cp, self.cal)
            snaps.append(replace(read.snapshot, error=failure))
            malformed += read.malformed
            kept.append({"seq": cp.seq, "summary": summary, "answer": completion.data})
        detail = {
            "malformed": malformed,
            "checkpoints": kept,
            "summary_words": len(summary.split()),
        }
        return MethodRun.of(self.name, ticket, tuple(snaps), meter.tally, failure, detail)
