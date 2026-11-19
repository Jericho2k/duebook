"""Baseline (a): the whole conversation in the model's context.

At each checkpoint the model is shown every event so far — numbered, stamped
with its local time — and asked for the state of the ticket. It keeps no
memory between checkpoints; the transcript is the memory.

This is the strongest reader of the three baselines: nothing is lost before
the model sees it. Its weaknesses are the ones the event memory is built
against — the model computes deadlines itself, nothing checks that a promise
it reports was made by the side it names, and the cost of one answer grows
with the length of the ticket.

Asked only at checkpoints it is cheap, and knows nothing between them. Asked
live (``full_context_live``), it answers after every message, as the event
memory's card is current after every message, re-reading the transcript each
time. The instructions come first and the moment last, so each question
shares its prefix with the one before: the best case for its cost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ...events import MessagePosted
from ...extract.extractor import JsonModel
from ...temporal import WorkCalendar
from ..generate import SimTicket
from ..meter import Ledger, Meter
from ..methods import MethodRun
from ..result import Checkpoint, Moment, Snapshot, checkpoints
from .answer import ANSWER_SCHEMA, parse_answer, question, render_event


def moments(ticket: SimTicket) -> tuple[Checkpoint, ...]:
    """After every message, and at every checkpoint: when a live method answers."""
    stops = {cp.seq: cp for cp in checkpoints(ticket)}
    return tuple(
        stops.get(e.seq) or Checkpoint(e.seq, e.at, Moment.MESSAGE)
        for e in ticket.events
        if e.seq in stops or isinstance(e, MessagePosted)
    )


@dataclass(slots=True)
class FullContext:
    model: JsonModel
    ledger: Ledger
    cal: WorkCalendar = field(default_factory=WorkCalendar)
    name: str = "full_context"
    #: Answer after every message, not only at checkpoints.
    live: bool = False

    def run(self, ticket: SimTicket) -> MethodRun:
        meter = Meter(self.model, self.ledger, answering=True)
        lines = [render_event(e, self.cal) for e in ticket.events]
        stops = {cp.seq for cp in checkpoints(ticket)}
        snaps, malformed, answers, failure = [], [], [], None
        live, calls = [], []
        for cp in moments(ticket) if self.live else checkpoints(ticket):
            transcript = "\n".join(lines[: cp.seq])
            messages = question("TRANSCRIPT", transcript, cp, self.cal, now_last=self.live)
            started = time.perf_counter()
            try:
                completion = meter.complete_json(messages, ANSWER_SCHEMA, name="ticket_state")
            except Exception as error:
                failure = failure or f"{type(error).__name__}: {error}"
                snap = Snapshot(cp, error=f"{type(error).__name__}: {error}")
            else:
                read = parse_answer(completion.data, cp, self.cal)
                snap = read.snapshot
                malformed += read.malformed
                answers.append({"seq": cp.seq, "answer": completion.data})
                u = completion.usage
                # (seq, prompt, cached, completion tokens, seconds) per call.
                calls.append([cp.seq, u.prompt_tokens, u.cached_tokens, u.completion_tokens,
                              round(time.perf_counter() - started, 3)])  # fmt: skip
            if cp.seq in stops:
                snaps.append(snap)
            if self.live:
                live.append(snap)
        detail = {"malformed": malformed, "answers": answers}
        if self.live:
            # Every answer, so what it knew between checkpoints can be judged.
            detail |= {"live": live, "calls": calls}
        return MethodRun.of(self.name, ticket, tuple(snaps), meter.tally, failure, detail)
