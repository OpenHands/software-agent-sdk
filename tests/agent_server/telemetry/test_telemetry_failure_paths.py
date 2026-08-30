"""Degradation paths: everything that can go wrong must end in a no-op.

Telemetry is never allowed to prevent a server from starting or to turn a
working request into a failing one, so each of these exercises a failure and
asserts the fallback rather than the success case.
"""

import sys
import types
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config, TelemetrySpec
from openhands.agent_server.telemetry import (
    NoOpTelemetrySink,
    build_telemetry_sink,
    get_telemetry_sink,
    notify_misc_settings_changed,
    shutdown_telemetry_sink,
)


SETTINGS_URL = "/api/settings"
_GRANT = {"misc_settings_diff": {"telemetry": {"consent": "granted"}}}


def _spec(**kw) -> TelemetrySpec:
    kw.setdefault("exporter", "posthog")
    kw.setdefault("posthog_api_key", SecretStr("phc_test"))
    return TelemetrySpec(**kw)


# ── missing / broken optional dependency ──────────────────────────────────


async def test_missing_posthog_extra_degrades_to_noop(
    temp_persistence_dir, monkeypatch
):
    """The whole point of the optional extra: absence must not break startup."""
    real_import = __import__

    def _no_posthog(name, *args, **kwargs):
        if name == "posthog" or name.startswith("posthog."):
            raise ImportError("No module named 'posthog'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_posthog)
    monkeypatch.delitem(sys.modules, "posthog", raising=False)
    monkeypatch.delitem(
        sys.modules, "openhands.agent_server.telemetry.posthog_exporter", raising=False
    )

    sink = await build_telemetry_sink(Config(static_files_path=None, telemetry=_spec()))
    assert isinstance(sink, NoOpTelemetrySink)
    assert sink.enabled is False


async def test_exporter_constructor_raising_degrades_to_noop(
    temp_persistence_dir, monkeypatch
):
    """A bad API key or client-side config must not abort startup."""
    module = types.ModuleType("posthog")

    def _boom(**kwargs):
        raise ValueError("invalid project api key")

    module.Posthog = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "posthog", module)

    sink = await build_telemetry_sink(Config(static_files_path=None, telemetry=_spec()))
    assert isinstance(sink, NoOpTelemetrySink)


async def test_unreadable_consent_is_treated_as_absent_consent(
    temp_persistence_dir, monkeypatch
):
    """A corrupt settings file must fail closed, never open."""
    module = types.ModuleType("posthog")
    module.Posthog = lambda **kw: Mock(spec=["capture", "flush", "shutdown"])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "posthog", module)

    import openhands.agent_server.telemetry.service as service_mod

    def _boom(_config):
        raise RuntimeError("settings file is corrupt")

    monkeypatch.setattr(service_mod, "_read_misc_settings_sync", _boom)

    sink = await build_telemetry_sink(Config(static_files_path=None, telemetry=_spec()))
    try:
        assert sink.enabled is False, "unreadable consent must not enable telemetry"
    finally:
        await shutdown_telemetry_sink()


async def test_exporter_aclose_raising_is_swallowed(temp_persistence_dir, monkeypatch):
    module = types.ModuleType("posthog")
    client = Mock(spec=["capture", "flush", "shutdown"])
    client.shutdown.side_effect = ConnectionError("cannot reach posthog")
    module.Posthog = lambda **kw: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "posthog", module)

    await build_telemetry_sink(Config(static_files_path=None, telemetry=_spec()))
    # Must not propagate.
    await shutdown_telemetry_sink()


# ── settings-store failures at the consent endpoint ───────────────────────


@pytest.fixture
def client(config_factory):
    return TestClient(create_app(config_factory()))


def test_corrupt_settings_file_yields_409(client, monkeypatch):
    from openhands.agent_server.persistence import store as store_mod

    def _boom(self, update_fn):
        raise RuntimeError("Cannot load settings")

    monkeypatch.setattr(store_mod.FileSettingsStore, "update", _boom)
    with client as c:
        assert c.patch(SETTINGS_URL, json=_GRANT).status_code == 409


def test_unwritable_settings_file_yields_500(client, monkeypatch):
    from openhands.agent_server.persistence import store as store_mod

    def _boom(self, update_fn):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(store_mod.FileSettingsStore, "update", _boom)
    with client as c:
        assert c.patch(SETTINGS_URL, json=_GRANT).status_code == 500


def test_consent_endpoint_survives_a_sink_that_raises(client, monkeypatch):
    """A broken sink must not turn a successful consent write into a 500."""
    import openhands.agent_server.telemetry.service as service_mod

    class _ExplodingSink:
        enabled = True

        def emit(self, event):
            pass

        def on_decision_changed(self, decision):
            raise RuntimeError("sink is broken")

        async def aclose(self):
            pass

    monkeypatch.setattr(service_mod, "_telemetry_sink", _ExplodingSink())
    with client as c:
        assert (
            c.patch(
                SETTINGS_URL,
                json={"misc_settings_diff": {"telemetry": {"consent": "denied"}}},
            ).status_code
            == 200
        )


# ── singleton / lifecycle edge cases ──────────────────────────────────────


def test_notify_consent_changed_before_any_sink_is_built_is_a_noop():
    notify_misc_settings_changed({})  # must not raise


async def test_shutdown_without_a_sink_is_a_noop():
    await shutdown_telemetry_sink()
    await shutdown_telemetry_sink()


def test_get_telemetry_sink_returns_noop_before_build():
    assert isinstance(get_telemetry_sink(), NoOpTelemetrySink)


async def test_shutdown_swallows_an_aclose_failure(temp_persistence_dir, monkeypatch):
    import openhands.agent_server.telemetry.service as service_mod

    class _BadSink:
        enabled = False

        def emit(self, event):
            pass

        def on_decision_changed(self, decision):
            pass

        async def aclose(self):
            raise RuntimeError("teardown failed")

    monkeypatch.setattr(service_mod, "_telemetry_sink", _BadSink())
    await shutdown_telemetry_sink()  # must not propagate
    assert isinstance(get_telemetry_sink(), NoOpTelemetrySink)
