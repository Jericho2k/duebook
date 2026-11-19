"""Service configuration, read from ``DUEBOOK_*`` environment variables.

Every process of a deployment — the API, the processor, the scheduler, the
delivery workers — reads the same variables, and some of them must agree
across all of them: the working calendar is part of what a replay depends on,
so a processor and an API configured with different time zones would build
and replay different cards. Reading them in one place, one way, is what makes
that agreement easy to keep.

Only the standard library is used, so the core and ``duebook migrate`` do not
need the web stack installed.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .extract.client import DEFAULT_MODEL, OPENROUTER_URL, REASONING
from .extract.verifier import DEFAULT_VERIFIER_REASONING
from .lifecycle import AlertPolicy
from .temporal import WorkCalendar

PREFIX = "DUEBOOK_"
CHANNELS = ("log", "webhook")


class ConfigError(ValueError):
    """A variable is missing or cannot be read. The message names it."""


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = ""
    #: The OpenAI-compatible endpoint the extractor calls.
    openrouter_base_url: str = OPENROUTER_URL
    openrouter_model: str = DEFAULT_MODEL
    openrouter_api_key: str = field(default="", repr=False)
    #: When set, ``POST /events`` requires a valid ``X-Duebook-Signature``.
    webhook_secret: str = ""
    #: When set, read endpoints require ``Authorization: Bearer <key>``.
    api_key: str = ""
    zammad_url: str = ""
    #: "log" writes alerts to stdout as JSON lines; "webhook" POSTs them to ``alert_url``.
    alert_channel: str = "log"
    alert_url: str = ""
    #: Signs outgoing alerts with the same scheme as incoming webhooks. Empty: unsigned.
    alert_secret: str = ""
    #: The working calendar's time zone: "by Friday" means the end of Friday here.
    timezone: str = "Europe/Moscow"
    #: Deadlines before this instant never alert. Set it to the go-live moment.
    alerts_since: datetime | None = None
    #: Names this process in outbox claims. Empty: role, host and pid.
    worker_name: str = ""
    process_idle: timedelta = timedelta(seconds=1)
    process_batch: int = 20
    schedule_max_idle: timedelta = timedelta(seconds=30)
    deliver_idle: timedelta = timedelta(seconds=5)
    deliver_batch: int = 10
    host: str = "0.0.0.0"
    port: int = 8000
    pool_min: int = 1
    pool_max: int = 10
    verifier_enabled: bool = True
    verifier_model: str = DEFAULT_MODEL
    verifier_reasoning: str = DEFAULT_VERIFIER_REASONING

    def __post_init__(self) -> None:
        if self.verifier_reasoning not in REASONING:
            raise ConfigError(f"{PREFIX}VERIFIER_REASONING must be one of: {', '.join(REASONING)}")
        if self.alert_channel not in CHANNELS:
            raise ConfigError(f"{PREFIX}ALERT_CHANNEL must be one of: {', '.join(CHANNELS)}")
        if self.alert_channel == "webhook" and not self.alert_url:
            raise ConfigError(f"{PREFIX}ALERT_URL is required when the alert channel is webhook")
        if self.alerts_since is not None and self.alerts_since.tzinfo is None:
            raise ConfigError(f"{PREFIX}ALERTS_SINCE must include a UTC offset")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ConfigError(f"{PREFIX}TIMEZONE: unknown time zone {self.timezone!r}") from error

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ

        def get(name: str, default: str = "") -> str:
            return env.get(PREFIX + name, "").strip() or default

        def number(name: str, default: float) -> float:
            raw = get(name)
            try:
                return float(raw) if raw else default
            except ValueError as error:
                raise ConfigError(f"{PREFIX}{name} must be a number, got {raw!r}") from error

        def seconds(name: str, default: float) -> timedelta:
            return timedelta(seconds=number(name, default))

        since = get("ALERTS_SINCE")
        try:
            alerts_since = datetime.fromisoformat(since) if since else None
        except ValueError as error:
            raise ConfigError(f"{PREFIX}ALERTS_SINCE is not an ISO 8601 time: {since!r}") from error

        def flag(name: str, default: str) -> bool:
            value = get(name, default).lower()
            if value not in ("true", "false"):
                raise ConfigError(f"{PREFIX}{name} must be true or false")
            return value == "true"

        return cls(
            database_url=get("DATABASE_URL"),
            openrouter_base_url=get("OPENROUTER_BASE_URL", OPENROUTER_URL),
            openrouter_model=get("OPENROUTER_MODEL", DEFAULT_MODEL),
            openrouter_api_key=get("OPENROUTER_API_KEY"),
            webhook_secret=get("WEBHOOK_SECRET"),
            api_key=get("API_KEY"),
            zammad_url=get("ZAMMAD_URL"),
            alert_channel=get("ALERT_CHANNEL", "log").lower(),
            alert_url=get("ALERT_URL"),
            alert_secret=get("ALERT_SECRET"),
            timezone=get("TIMEZONE", "Europe/Moscow"),
            alerts_since=alerts_since,
            worker_name=get("WORKER_NAME"),
            process_idle=seconds("PROCESS_IDLE_SECONDS", 1),
            process_batch=int(number("PROCESS_BATCH", 20)),
            schedule_max_idle=seconds("SCHEDULE_MAX_IDLE_SECONDS", 30),
            deliver_idle=seconds("DELIVER_IDLE_SECONDS", 5),
            deliver_batch=int(number("DELIVER_BATCH", 10)),
            host=get("HOST", "0.0.0.0"),
            port=int(number("PORT", 8000)),
            pool_min=int(number("POOL_MIN", 1)),
            pool_max=int(number("POOL_MAX", 10)),
            verifier_enabled=flag("VERIFIER_ENABLED", "true"),
            verifier_model=get("VERIFIER_MODEL", DEFAULT_MODEL),
            verifier_reasoning=get("VERIFIER_REASONING", DEFAULT_VERIFIER_REASONING),
        )

    def require_database(self) -> str:
        if not self.database_url:
            raise ConfigError(f"{PREFIX}DATABASE_URL is not set")
        return self.database_url

    def calendar(self) -> WorkCalendar:
        return WorkCalendar(tz=ZoneInfo(self.timezone))

    def policy(self) -> AlertPolicy:
        return AlertPolicy(alerts_since=self.alerts_since)

    def worker(self, role: str) -> str:
        """This process's name in outbox claims: as configured, or role, host and pid."""
        return self.worker_name or f"{role}@{socket.gethostname()}:{os.getpid()}"
