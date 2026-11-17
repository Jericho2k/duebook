"""Baseline (a): the whole conversation in the model's context.

At each checkpoint the model is shown every event so far — numbered, stamped
with its local time — and asked for the state of the ticket. It keeps no
memory between checkpoints; the transcript is the memory.

This is the strongest reader of the three baselines: nothing is lost before
the model sees it. Its weaknesses are the ones the event memory is built
against — the model computes deadlines itself, nothing checks that a promise
it reports was made by the side it names, and the cost of one answer grows
with the length of the ticket. Asked only at checkpoints, as here, it is also
cheap; asked after every message, which is what an always-current view or a
timely alert needs, it would re-read the transcript each time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...extract.extractor import JsonModel
from ...temporal import WorkCalendar
from ..generate import SimTicket
from ..meter import Ledger, Meter
from ..methods import MethodRun
from ..result import Snapshot, checkpoints
from .answer import ANSWER_SCHEMA, parse_answer, question, render_event


@dataclass(slots=True)
class FullContext:
    model: JsonModel
    ledger: Ledger
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    name: str = "full_context"

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger, answering=True)
        lines = [render_event(e, self.cal) for e in ticket.events]
        snaps, malformed, answers, failure = [], [], [], None
        for cp in checkpoints(ticket):
            transcript = "\n".join(lines[: cp.seq])
            messages = question("TRANSCRIPT", transcript, cp, self.cal)
            try:
                completion = meter.complete_json(messages, ANSWER_SCHEMA, name="ticket_state")
            except Exception as error:
                failure = failure or f"{type(error).__name__}: {error}"
                snaps.append(Snapshot(cp, error=f"{type(error).__name__}: {error}"))
                continue
            read = parse_answer(completion.data, cp, self.cal)
            snaps.append(read.snapshot)
            malformed += read.malformed
            answers.append({"seq": cp.seq, "answer": completion.data})
        detail = {"malformed": malformed, "answers": answers}
        return MethodRun.of(self.name, ticket, tuple(snaps), meter.tally, failure, detail)
