"""The ``duebook`` command: one entrypoint per process of a deployment.

    duebook migrate    bring the database schema up to date, then exit
    duebook serve      the HTTP API (uvicorn)
    duebook process    apply stored events: extract, reduce, persist
    duebook schedule   queue alerts as deadlines come due
    duebook deliver    send queued alerts through the configured channel

Each is configured only by ``DUEBOOK_*`` environment variables (see
``duebook.settings``), so the same image runs every role.

The three loops stop gracefully on SIGTERM or SIGINT: the item in hand — a
ticket being advanced, a batch of wake-ups, an alert being sent — is finished
and the process exits 0. Nothing depends on that for correctness, since every
step is safe to interrupt, but an orchestrator that restarts a process should
not cost a model call that was already paid for. Sleeps wake as soon as a
signal arrives, so an idle loop exits at once.

A loop that loses its database connection logs the error, waits, reconnects
and carries on, rather than exiting and relying on a supervisor.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from .settings import ConfigError, Settings

log = logging.getLogger("duebook")

#: How long a loop waits before reconnecting after an unexpected error.
RESTART_DELAY = timedelta(seconds=5)


class Stopper:
    """Turns SIGTERM and SIGINT into a flag the loops check between items."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def install(self) -> Stopper:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle)
        return self

    def _handle(self, signum: int, _frame: object) -> None:
        if not self._event.is_set():
            log.info("received %s, finishing the current item", signal.Signals(signum).name)
        self._event.set()

    def set(self) -> None:
        self._event.set()

    def __call__(self) -> bool:
        return self._event.is_set()

    def sleep(self, seconds: float) -> None:
        """Sleep, but return early once a stop was requested."""
        self._event.wait(max(seconds, 0.0))


def _now() -> datetime:
    return datetime.now(UTC)


def supervise(
    settings: Settings,
    stopper: Stopper,
    body: Callable[[object], None],
    role: str,
    once: bool = False,
) -> None:
    """Run ``body(store)`` until stopped, reconnecting after a failure.

    With ``once`` a failure is raised instead: a single pass that failed
    should say so in the exit status.
    """
    from .store import Store, connect

    dsn = settings.require_database()
    log.info("%s started", role)
    while not stopper():
        try:
            with connect(dsn) as conn:
                body(Store(conn, settings.calendar(), settings.policy()))
        except Exception:
            if once:
                raise
            log.exception("%s failed; restarting in %ss", role, RESTART_DELAY.seconds)
            stopper.sleep(RESTART_DELAY.total_seconds())
    log.info("%s stopped", role)


# ---------------------------------------------------------------- commands ----


def migrate(settings: Settings, _: argparse.Namespace) -> int:
    from .store import connect
    from .store import migrate as apply_migrations

    with connect(settings.require_database()) as conn:
        applied = apply_migrations(conn)
    log.info("migrations applied: %s", ", ".join(applied) if applied else "none, up to date")
    return 0


def serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(
        create_app(settings),
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level="info",
        # uvicorn handles SIGTERM itself: it stops accepting, finishes the
        # requests in flight, then exits.
        timeout_graceful_shutdown=20,
    )
    return 0


def extractor(settings: Settings):
    from .extract import ChatClient, Extractor

    if not settings.openrouter_api_key:
        raise ConfigError("DUEBOOK_OPENROUTER_API_KEY is not set: the processor calls a model")
    client = ChatClient(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
    )
    return Extractor(client, settings.calendar())


def process(
    settings: Settings, args: argparse.Namespace, stopper: Stopper, reader=None
) -> int:
    from .pipeline import Pipeline

    reader = reader or extractor(settings)

    def body(store) -> None:
        pipeline = Pipeline(store, reader, clock=_now)
        if args.once:
            _report(pipeline.process(settings.process_batch, stopper))
            stopper.set()
            return
        while not stopper():
            done = pipeline.process(settings.process_batch, stopper)
            _report(done)
            if not done:
                stopper.sleep(settings.process_idle.total_seconds())

    supervise(settings, stopper, body, "processor", args.once)
    return 0


def _report(done) -> None:
    for handled in done:
        if handled.applied:
            log.info("%s: applied %s", handled.ticket_id, list(handled.applied))
        if handled.held is not None:
            log.warning(
                "%s: held at event %s until %s: %s",
                handled.ticket_id, handled.held.seq, handled.held.retry_at.isoformat(),
                handled.held.error,
            )  # fmt: skip


def schedule(settings: Settings, args: argparse.Namespace, stopper: Stopper) -> int:
    from .store.scheduler import run, run_due

    def body(store) -> None:
        if args.once:
            log.info("woke %s tickets", run_due(store, _now(), stop=stopper))
            stopper.set()
            return
        run(store, clock=_now, sleep=stopper.sleep, max_idle=settings.schedule_max_idle,
            stop=stopper)  # fmt: skip

    supervise(settings, stopper, body, "scheduler", args.once)
    return 0


def channel(settings: Settings):
    from .channels import LogChannel, WebhookChannel

    if settings.alert_channel == "webhook":
        return WebhookChannel(settings.alert_url, settings.alert_secret)
    return LogChannel()


def deliver(
    settings: Settings, args: argparse.Namespace, stopper: Stopper, sink=None
) -> int:
    from .store import Worker

    sink = sink or channel(settings)
    name = settings.worker("deliver")

    def body(store) -> None:
        worker = Worker(store, sink, name, batch=settings.deliver_batch)
        if args.once:
            log.info("delivery round: %s", worker.run_once(_now(), stopper))
            stopper.set()
            return
        worker.run(clock=_now, sleep=stopper.sleep, idle=settings.deliver_idle, stop=stopper)

    supervise(settings, stopper, body, f"deliverer {name}", args.once)
    return 0


# -------------------------------------------------------------------- main ----


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="duebook", description="duebook service processes")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="bring the database schema up to date")
    s = sub.add_parser("serve", help="run the HTTP API")
    s.add_argument("--host", help="overrides DUEBOOK_HOST")
    s.add_argument("--port", type=int, help="overrides DUEBOOK_PORT")
    for name, text in (
        ("process", "apply stored events"),
        ("schedule", "queue alerts as deadlines come due"),
        ("deliver", "send queued alerts"),
    ):
        loop = sub.add_parser(name, help=text)
        loop.add_argument("--once", action="store_true", help="do one pass and exit")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.from_env()
        if args.command == "migrate":
            return migrate(settings, args)
        if args.command == "serve":
            return serve(settings, args)
        stopper = Stopper().install()
        command = {"process": process, "schedule": schedule, "deliver": deliver}[args.command]
        return command(settings, args, stopper)
    except ConfigError as error:
        log.error("configuration: %s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
