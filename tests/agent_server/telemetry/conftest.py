"""Shared fixtures for telemetry tests.

Combines the persistence-dir isolation used by ``test_settings_router.py`` with
a reset of the telemetry sink singleton, so no test can observe another's
process state.
"""

from base64 import urlsafe_b64encode
from typing import Any

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config, TelemetryExporterKind, TelemetrySpec
from openhands.agent_server.persistence import ServerStores
from openhands.agent_server.telemetry import (
    TelemetrySink,
    build_telemetry_sink,
    reset_telemetry_sink,
)
from openhands.agent_server.telemetry.policy import (
    CONSENT_ENV,
    CONSENT_MODE_ENV,
    DO_NOT_TRACK_ENV,
)


@pytest.fixture(autouse=True)
def _clean_telemetry_env(monkeypatch):
    """Ensure a developer's own DO_NOT_TRACK cannot flip test outcomes."""
    monkeypatch.delenv(DO_NOT_TRACK_ENV, raising=False)
    monkeypatch.delenv(CONSENT_ENV, raising=False)
    monkeypatch.delenv(CONSENT_MODE_ENV, raising=False)
    monkeypatch.delenv("OH_TELEMETRY", raising=False)


@pytest.fixture(autouse=True)
def _reset_sink():
    reset_telemetry_sink()
    yield
    reset_telemetry_sink()


@pytest.fixture
def temp_persistence_dir(tmp_path, monkeypatch):
    persistence_dir = tmp_path / "persistence"
    persistence_dir.mkdir()
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(persistence_dir))
    return persistence_dir


async def build_sink(config: Config) -> TelemetrySink:
    """Build the sink as a server running ``config`` does.

    Consent is read from that server's own settings store.
    """
    return await build_telemetry_sink(config, ServerStores.from_config(config).settings)


@pytest.fixture
def secret_key() -> str:
    return urlsafe_b64encode(b"a" * 32).decode("ascii")


@pytest.fixture
def config_factory(temp_persistence_dir, secret_key):
    def _factory(
        exporter: TelemetryExporterKind = "none", **telemetry_kwargs: Any
    ) -> Config:
        return Config(
            static_files_path=None,
            session_api_keys=[],
            secret_key=SecretStr(secret_key),
            telemetry=TelemetrySpec(exporter=exporter, **telemetry_kwargs),
        )

    return _factory
