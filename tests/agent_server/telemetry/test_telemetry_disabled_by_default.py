"""Default-off behaviour, including that the vendor SDK is never imported."""

import subprocess
import sys

from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.telemetry import (
    NoOpTelemetrySink,
    build_telemetry_sink,
    get_telemetry_sink,
)


def test_default_config_yields_a_noop_sink(temp_persistence_dir):
    assert isinstance(get_telemetry_sink(), NoOpTelemetrySink)
    assert get_telemetry_sink().enabled is False


async def test_building_from_a_default_config_stays_a_noop(temp_persistence_dir):
    sink = await build_telemetry_sink(Config(static_files_path=None))
    assert isinstance(sink, NoOpTelemetrySink)
    assert sink.enabled is False


async def test_opt_in_without_an_api_key_stays_inactive(config_factory):
    """No key means no exporter, regardless of mode."""
    sink = await build_telemetry_sink(config_factory("cloud_locked"))
    assert isinstance(sink, NoOpTelemetrySink)


async def test_kill_switch_short_circuits_before_any_exporter(
    config_factory, monkeypatch
):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    sink = await build_telemetry_sink(
        config_factory("cloud_locked", posthog_api_key="phc_test")
    )
    assert isinstance(sink, NoOpTelemetrySink)


def test_importing_the_telemetry_package_does_not_import_posthog():
    """The optional dependency must stay off the import path by default.

    Note on scope: this asserts that *this package* does not import posthog,
    not that ``posthog`` is absent from ``sys.modules`` after a full server
    startup. It cannot assert the latter, because the unrelated pre-existing
    ``browser-use`` dependency imports posthog transitively when the tool
    preload service starts. The claim under test — that a telemetry-disabled
    server never pulls the vendor SDK in *on telemetry's behalf* — is captured
    precisely by checking our own exporter module stays unimported.

    Run in a subprocess so an import elsewhere in the test session cannot mask
    a regression.
    """
    script = """
import sys

import openhands.agent_server.telemetry as t
from openhands.agent_server.config import Config
from openhands.agent_server.api import create_app

assert "posthog" not in sys.modules, "importing telemetry pulled in posthog"

create_app(Config(static_files_path=None, session_api_keys=[]))
assert "openhands.agent_server.telemetry.posthog_exporter" not in sys.modules, (
    "the exporter module was imported with telemetry disabled"
)
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert "OK" in result.stdout, (
        f"subprocess failed\nstdout:{result.stdout}\nstderr:{result.stderr}"
    )


async def test_disabled_server_never_constructs_the_exporter(temp_persistence_dir):
    """Complements the import test: no exporter object is built either."""
    import openhands.agent_server.telemetry.service as service_mod

    built: list[object] = []

    class _Tripwire:
        def __init__(self, *a, **k):
            built.append(self)

    monkeypatched = getattr(service_mod, "PostHogExporter", None)
    assert monkeypatched is None, "exporter must not be a module-level attribute"

    await build_telemetry_sink(Config(static_files_path=None))
    assert built == []


def test_conversation_service_defaults_to_a_noop_sink(temp_persistence_dir):
    from openhands.agent_server.conversation_service import ConversationService

    service = ConversationService(conversations_dir=temp_persistence_dir)
    assert isinstance(service.telemetry_sink, NoOpTelemetrySink)
    assert service.telemetry_sink.enabled is False


def test_app_exposes_a_sink_on_state_after_startup(temp_persistence_dir):
    with TestClient(create_app(Config(static_files_path=None))) as client:
        sink = client.app.state.telemetry_sink  # type: ignore[attr-defined]
        assert isinstance(sink, NoOpTelemetrySink)


def test_server_lifecycle_events_are_emitted_when_enabled(
    temp_persistence_dir, monkeypatch
):
    """server_started/stopped bracket the lifespan when telemetry is active."""
    import openhands.agent_server.telemetry.service as service_mod
    from openhands.agent_server.telemetry import models as m

    emitted: list[str] = []

    class _Sink:
        enabled = True

        def emit(self, event):
            emitted.append(event.event_name)

        def on_consent_changed(self, consent):
            pass

        async def aclose(self):
            pass

    monkeypatch.setattr(service_mod, "get_telemetry_sink", lambda: _Sink())
    monkeypatch.setattr(
        service_mod,
        "_event_factory",
        service_mod.DiagnosticEventFactory(
            runtime=service_mod.build_runtime_properties(
                mode="cloud_locked", deferred_init=False
            )
        ),
    )

    with TestClient(create_app(Config(static_files_path=None))):
        pass

    assert emitted == [m.EventName.SERVER_STARTED, m.EventName.SERVER_STOPPED]


def test_deferred_pod_does_not_emit_an_unpaired_server_stopped(
    temp_persistence_dir, monkeypatch
):
    """Regression: a warm-pool pod boots with telemetry disabled.

    ``server_started`` is emitted by InitService after POST /api/init rebuilds
    the sink, not at boot. A pod that is never initialised must therefore emit
    neither event — previously it emitted a lone ``server_stopped``, which
    corrupts uptime and session metrics.
    """
    import openhands.agent_server.telemetry.service as service_mod

    emitted: list[str] = []

    class _Sink:
        enabled = True

        def emit(self, event):
            emitted.append(event.event_name)

        def on_consent_changed(self, consent):
            pass

        async def aclose(self):
            pass

    monkeypatch.setattr(service_mod, "get_telemetry_sink", lambda: _Sink())
    monkeypatch.setattr(
        service_mod,
        "_event_factory",
        service_mod.DiagnosticEventFactory(
            runtime=service_mod.build_runtime_properties(
                mode="cloud_locked", deferred_init=True
            )
        ),
    )

    with TestClient(create_app(Config(static_files_path=None, deferred_init=True))):
        pass

    assert emitted == [], f"deferred pod emitted unpaired lifecycle events: {emitted}"


async def test_telemetry_init_does_not_hijack_the_settings_store_singleton(
    temp_persistence_dir, monkeypatch
):
    """Regression: telemetry must prime the settings store WITH the config.

    ``get_settings_store`` is a singleton whose persistence directory and
    cipher are fixed by the first call. Telemetry initialises during lifespan
    startup, before ``ConversationService.get_instance()`` makes its own
    priming call, so a no-arg ``get_settings_store()`` here previously won the
    race and left the whole process writing settings *and secrets* to the
    default relative directory with encryption disabled.
    """
    from base64 import urlsafe_b64encode

    from pydantic import SecretStr

    import openhands.agent_server.telemetry.posthog_exporter as pe
    from openhands.agent_server.config import Config, TelemetrySpec
    from openhands.agent_server.persistence.store import get_settings_store

    class _FakeExporter:
        async def send(self, events):
            pass

        async def aclose(self):
            pass

    monkeypatch.setattr(pe, "PostHogExporter", lambda *a, **k: _FakeExporter())
    monkeypatch.delenv("OH_PERSISTENCE_DIR", raising=False)

    config = Config(
        static_files_path=None,
        conversations_path=temp_persistence_dir / "workspace/conversations",
        secret_key=SecretStr(urlsafe_b64encode(b"a" * 32).decode("ascii")),
        telemetry=TelemetrySpec(
            mode="local_opt_in", posthog_api_key=SecretStr("phc_test")
        ),
    )

    sink = await build_telemetry_sink(config)
    try:
        store = get_settings_store(config)
        assert store.cipher is not None, (
            "telemetry primed the settings store without a cipher; secrets "
            "would be persisted unencrypted process-wide"
        )
        assert temp_persistence_dir in store.persistence_dir.parents or (
            store.persistence_dir.is_relative_to(temp_persistence_dir)
        ), f"settings store landed outside the configured dir: {store.persistence_dir}"
    finally:
        await sink.aclose()
