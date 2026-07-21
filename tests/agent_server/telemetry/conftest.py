"""Shared fixtures for telemetry tests.

Combines the persistence-dir isolation used by ``test_settings_router.py`` with
a reset of the telemetry sink singleton, so no test can observe another's
process state.
"""

import os
import tempfile
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config, TelemetrySpec
from openhands.agent_server.persistence.store import reset_stores
from openhands.agent_server.telemetry import reset_telemetry_sink
from openhands.agent_server.telemetry.policy import (
    DO_NOT_TRACK_ENV,
    TELEMETRY_DISABLED_ENV,
    TelemetryMode,
)


@pytest.fixture(autouse=True)
def _clean_telemetry_env(monkeypatch):
    """Ensure a developer's own DO_NOT_TRACK cannot flip test outcomes."""
    monkeypatch.delenv(DO_NOT_TRACK_ENV, raising=False)
    monkeypatch.delenv(TELEMETRY_DISABLED_ENV, raising=False)
    monkeypatch.delenv("OH_TELEMETRY_MODE", raising=False)
    monkeypatch.delenv("OH_TELEMETRY", raising=False)


@pytest.fixture(autouse=True)
def _reset_sink():
    reset_telemetry_sink()
    yield
    reset_telemetry_sink()


@pytest.fixture
def temp_persistence_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        reset_stores()
        old_val = os.environ.get("OH_PERSISTENCE_DIR")
        os.environ["OH_PERSISTENCE_DIR"] = tmpdir
        yield Path(tmpdir)
        reset_stores()
        if old_val is not None:
            os.environ["OH_PERSISTENCE_DIR"] = old_val
        else:
            os.environ.pop("OH_PERSISTENCE_DIR", None)


@pytest.fixture
def secret_key() -> str:
    return urlsafe_b64encode(b"a" * 32).decode("ascii")


@pytest.fixture
def config_factory(temp_persistence_dir, secret_key):
    def _factory(mode: TelemetryMode = "disabled", **telemetry_kwargs: Any) -> Config:
        return Config(
            static_files_path=None,
            session_api_keys=[],
            secret_key=SecretStr(secret_key),
            telemetry=TelemetrySpec(mode=mode, **telemetry_kwargs),
        )

    return _factory
