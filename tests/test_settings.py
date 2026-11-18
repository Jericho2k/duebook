from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from duebook.extract import DEFAULT_MODEL
from duebook.settings import ConfigError, Settings


def test_every_setting_is_read_from_a_prefixed_environment_variable():
    s = Settings.from_env({
        "DUEBOOK_DATABASE_URL": "postgresql://db/duebook",
        "DUEBOOK_OPENROUTER_BASE_URL": "http://llm.local/v1",
        "DUEBOOK_OPENROUTER_MODEL": "some/model",
        "DUEBOOK_OPENROUTER_API_KEY": "k",
        "DUEBOOK_VERIFIER_ENABLED": "False",
        "DUEBOOK_VERIFIER_MODEL": "some/verifier",
        "DUEBOOK_VERIFIER_REASONING": "off",
        "DUEBOOK_EXTRACTION_BATCHING": "TRUE",
        "DUEBOOK_WEBHOOK_SECRET": "hook",
        "DUEBOOK_API_KEY": "api",
        "DUEBOOK_ALERT_CHANNEL": "Webhook",
        "DUEBOOK_ALERT_URL": "https://alerts.example",
        "DUEBOOK_ALERT_SECRET": "out",
        "DUEBOOK_TIMEZONE": "Asia/Yekaterinburg",
        "DUEBOOK_ALERTS_SINCE": "2027-01-01T00:00:00+03:00",
        "DUEBOOK_WORKER_NAME": "deliver-1",
        "DUEBOOK_PROCESS_IDLE_SECONDS": "0.5",
        "DUEBOOK_SCHEDULE_MAX_IDLE_SECONDS": "60",
        "DUEBOOK_DELIVER_IDLE_SECONDS": "2",
        "DUEBOOK_PORT": "9000",
    })  # fmt: skip
    assert s.database_url == "postgresql://db/duebook" and s.openrouter_model == "some/model"
    assert not s.verifier_enabled and s.verifier_model == "some/verifier"
    assert s.verifier_reasoning == "off" and s.extraction_batching
    assert s.alert_channel == "webhook" and s.alert_secret == "out"
    assert s.calendar().tz == ZoneInfo("Asia/Yekaterinburg")
    assert s.policy().alerts_since == datetime(2026, 12, 31, 21, 0, tzinfo=UTC)
    assert s.process_idle == timedelta(seconds=0.5) and s.port == 9000
    assert s.worker("deliver") == "deliver-1"


def test_nothing_set_gives_working_defaults_except_the_database():
    s = Settings.from_env({})
    assert s.alert_channel == "log" and s.policy().alerts_since is None
    assert s.webhook_secret == "" and s.api_key == ""
    assert s.verifier_enabled and s.verifier_model == DEFAULT_MODEL
    assert s.verifier_reasoning == "low" and not s.extraction_batching
    assert s.worker("deliver").startswith("deliver@")
    with pytest.raises(ConfigError, match="DUEBOOK_DATABASE_URL"):
        s.require_database()


@pytest.mark.parametrize(
    ("env", "names"),
    [
        ({"DUEBOOK_ALERTS_SINCE": "2027-01-01T00:00:00"}, "ALERTS_SINCE"),
        ({"DUEBOOK_ALERTS_SINCE": "January"}, "ALERTS_SINCE"),
        ({"DUEBOOK_TIMEZONE": "Mars/Olympus"}, "TIMEZONE"),
        ({"DUEBOOK_ALERT_CHANNEL": "pager"}, "ALERT_CHANNEL"),
        ({"DUEBOOK_ALERT_CHANNEL": "webhook"}, "ALERT_URL"),
        ({"DUEBOOK_PORT": "eighty"}, "PORT"),
        ({"DUEBOOK_VERIFIER_ENABLED": "perhaps"}, "VERIFIER_ENABLED"),
        ({"DUEBOOK_VERIFIER_REASONING": "high"}, "VERIFIER_REASONING"),
        ({"DUEBOOK_EXTRACTION_BATCHING": "sometimes"}, "EXTRACTION_BATCHING"),
    ],
)
def test_a_bad_setting_is_refused_at_startup_naming_the_variable(env, names):
    with pytest.raises(ConfigError, match=names):
        Settings.from_env(env)
