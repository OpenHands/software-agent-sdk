"""server_started/stopped lifecycle across the deferred-init path.

Regression coverage for two review findings:

* a ``/api/init`` that fails after the telemetry sink is built must not emit a
  ``server_started``, and a retry must emit exactly one; and
* ``emit_server_started`` is idempotent, so no call path can produce an
  unpaired second start.
"""

from types import SimpleNamespace

import pytest

import openhands.agent_server.telemetry.service as service_mod
from openhands.agent_server.config import Config
from openhands.agent_server.init_router import InitRequest, InitService
from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.factory import (
    DiagnosticEventFactory,
    build_runtime_properties,
)


class _RecordingSink:
    enabled = True

    def __init__(self):
        self.events: list[str] = []

    def emit(self, event):
        self.events.append(str(event.event_name))

    def on_decision_changed(self, decision):
        pass

    async def aclose(self):
        pass


@pytest.fixture
def recording(monkeypatch):
    """Install a recording sink and a real factory, and reset lifecycle state."""
    service_mod.reset_telemetry_sink()
    sink = _RecordingSink()
    monkeypatch.setattr(service_mod, "_telemetry_sink", sink)
    monkeypatch.setattr(
        service_mod,
        "_event_factory",
        DiagnosticEventFactory(runtime=build_runtime_properties(deferred_init=True)),
    )
    yield sink
    service_mod.reset_telemetry_sink()


def _reset_singletons():
    from openhands.agent_server import bash_service, conversation_service

    conversation_service._conversation_service = None
    bash_service._bash_event_service = None


async def test_emit_server_started_is_idempotent(recording):
    service_mod.emit_server_started()
    service_mod.emit_server_started()
    service_mod.emit_server_started()
    assert recording.events == [m.EventName.SERVER_STARTED]


async def test_started_then_stopped_pairs(recording):
    service_mod.emit_server_started()
    service_mod.emit_server_stopped()
    service_mod.emit_server_stopped()  # no double stop
    assert recording.events == [m.EventName.SERVER_STARTED, m.EventName.SERVER_STOPPED]


async def test_failed_init_emits_no_start_and_retry_emits_exactly_one(
    recording, tmp_path, monkeypatch
):
    """The core regression: a start must not be emitted for a failed init.

    The sink is built before the conversation service, so a failure in
    get_instance() lands after telemetry setup. The old code emitted
    server_started there, leaving an unpaired start and letting the retry emit
    a second one.
    """
    _reset_singletons()
    base = Config(
        deferred_init=True,
        conversations_path=tmp_path / "convs",
        bash_events_dir=tmp_path / "bash",
    )
    app = SimpleNamespace(state=SimpleNamespace(config=base))
    svc = InitService(app, base_config=base)  # type: ignore[arg-type]

    # The sink is (re)built inside initialize(); keep our recording sink in
    # place so the emit path is observable regardless.
    monkeypatch.setattr(service_mod, "build_telemetry_sink", _keep_sink(recording))
    monkeypatch.setattr(service_mod, "shutdown_telemetry_sink", _noop_async)
    import openhands.agent_server.init_router as init_mod

    monkeypatch.setattr(init_mod, "build_telemetry_sink", _keep_sink(recording))
    monkeypatch.setattr(init_mod, "shutdown_telemetry_sink", _noop_async)

    # First attempt: force a failure after the sink is built.
    boom = {"raise": True}
    real_get_instance = init_mod.ConversationService.get_instance

    def _maybe_boom(config):
        if boom["raise"]:
            raise RuntimeError("get_instance failed")
        return real_get_instance(config)

    monkeypatch.setattr(init_mod.ConversationService, "get_instance", _maybe_boom)

    from fastapi import HTTPException

    try:
        with pytest.raises(HTTPException):
            await svc.initialize(InitRequest())
        assert svc.state == "dormant", "failed init should roll back to dormant"
        assert recording.events == [], "a failed init must not emit server_started"

        # Retry succeeds.
        boom["raise"] = False
        result = await svc.initialize(InitRequest())
        assert result.state == "ready"
        assert recording.events == [m.EventName.SERVER_STARTED], (
            "retry must emit exactly one server_started"
        )
    finally:
        await svc.teardown()
        _reset_singletons()


def _keep_sink(sink):
    async def _build(_config):
        service_mod._telemetry_sink = sink
        return sink

    return _build


async def _noop_async(*_args, **_kwargs):
    return None
