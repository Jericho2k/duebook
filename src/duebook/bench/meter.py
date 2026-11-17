"""Counting what each method spends, and refusing to spend past a limit.

A comparison of memory methods is as much about cost as about accuracy, so
every model call a method makes goes through a ``Meter``: it adds the call's
tokens, charged dollars and wall-clock time to the method's tally for the
ticket, and to a ``Ledger`` shared by the whole run.

The ledger enforces the run's spending cap BEFORE a call is made, not after a
ticket is finished: an experiment over several methods running in parallel
could otherwise overshoot by a whole ticket per method. A refused call raises
``OverBudget``, which the caller records as a failure like any other.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from ..extract.client import Completion
from ..extract.cost import Pricing, Usage
from ..extract.extractor import JsonModel


class OverBudget(RuntimeError):
    """The run has spent its limit; no further model calls are made."""


@dataclass(slots=True)
class Ledger:
    """What the whole run has spent. Thread-safe."""

    #: Stop before a call once this many dollars were charged. None: no limit.
    cap_usd: float | None = None
    #: Used only when a provider does not report the charged amount.
    pricing: Pricing | None = None
    spent: Usage = field(default_factory=Usage)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        if self.cap_usd is not None and self.usd() >= self.cap_usd:
            raise OverBudget(f"spending cap of ${self.cap_usd:.2f} reached")

    def add(self, usage: Usage) -> None:
        with self._lock:
            self.spent += usage

    def usd(self) -> float:
        return self.spent.usd(self.pricing) or 0.0


@dataclass(slots=True)
class Tally:
    """One method's spending on one ticket, split by when it was spent."""

    #: While messages arrived (the event memory, rolling summary, Mem0).
    ingest: Usage = field(default_factory=Usage)
    ingest_seconds: float = 0.0
    #: When asked for an answer at a checkpoint.
    answer: Usage = field(default_factory=Usage)
    answer_seconds: float = 0.0

    @property
    def usage(self) -> Usage:
        return self.ingest + self.answer

    def record(self, usage: Usage, seconds: float, answering: bool) -> None:
        if answering:
            self.answer += usage
            self.answer_seconds += seconds
        else:
            self.ingest += usage
            self.ingest_seconds += seconds


@dataclass(slots=True)
class Meter:
    """A model that counts every call into a tally and the ledger."""

    model: JsonModel
    ledger: Ledger
    tally: Tally = field(default_factory=Tally)
    #: Whether calls now are answering a checkpoint or reading a message.
    answering: bool = False

    def complete_json(self, messages, schema, name: str = "output") -> Completion:
        self.ledger.check()
        started = time.perf_counter()
        try:
            completion = self.model.complete_json(messages, schema, name)
        except Exception as error:
            spent = getattr(error, "usage", None) or Usage()
            self.ledger.add(spent)
            self.tally.record(spent, time.perf_counter() - started, self.answering)
            raise
        self.ledger.add(completion.usage)
        self.tally.record(completion.usage, time.perf_counter() - started, self.answering)
        return completion

    def spend(self, usage: Usage, seconds: float) -> None:
        """Record a call made outside ``complete_json`` (Mem0's own client)."""
        self.ledger.add(usage)
        self.tally.record(usage, seconds, self.answering)
