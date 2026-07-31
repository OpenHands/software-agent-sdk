import json

import pytest
from pydantic import ValidationError

from openhands.agent_server.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONVERSATION_IDLE_TTL_SECONDS,
    Config,
    load_config,
)


def test_load_config_reads_registered_marketplaces_from_env(monkeypatch, tmp_path):
    config_path = tmp_path / "missing.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setenv(
        "OH_REGISTERED_MARKETPLACES",
        json.dumps(
            [
                {
                    "name": "team",
                    "source": "https://github.com/org/marketplace",
                    "ref": "main",
                    "repo_path": "marketplace",
                    "auto_load": True,
                }
            ]
        ),
    )

    config = load_config()

    assert len(config.registered_marketplaces) == 1
    registration = config.registered_marketplaces[0]
    assert registration.name == "team"
    assert registration.source == "https://github.com/org/marketplace"
    assert registration.ref == "main"
    assert registration.repo_path == "marketplace"
    assert registration.auto_load is True


def test_conversation_idle_ttl_defaults_to_twenty_minutes():
    assert DEFAULT_CONVERSATION_IDLE_TTL_SECONDS == 1200.0
    assert Config().conversation_idle_ttl_seconds == 1200.0


def test_conversation_idle_ttl_can_be_disabled_and_overridden(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"conversation_idle_ttl_seconds": None}))
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))

    assert load_config().conversation_idle_ttl_seconds is None

    monkeypatch.setenv("OH_CONVERSATION_IDLE_TTL_SECONDS", "300")
    assert load_config().conversation_idle_ttl_seconds == 300.0


def test_conversation_idle_ttl_rejects_non_positive_values():
    with pytest.raises(ValidationError):
        Config(conversation_idle_ttl_seconds=0)
