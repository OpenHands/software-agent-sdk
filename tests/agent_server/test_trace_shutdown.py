import threading

import pytest
from opentelemetry.sdk.trace import TracerProvider

from openhands.agent_server.api import api_lifespan, create_app
from openhands.agent_server.config import Config


@pytest.mark.parametrize("outcome", [True, False, RuntimeError("exporter unavailable")])
@pytest.mark.parametrize("body_error", [False, True])
async def test_trace_flush_is_bounded_off_loop_and_best_effort(
    monkeypatch, tmp_path, caplog, outcome, body_error
):
    provider = TracerProvider(shutdown_on_exit=False)
    calls = []
    loop_thread = threading.get_ident()

    def flush(timeout_millis):
        calls.append((timeout_millis, threading.get_ident()))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(provider, "force_flush", flush)
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    monkeypatch.setattr("openhands.agent_server.api.get_vscode_service", lambda: None)
    monkeypatch.setattr(
        "openhands.agent_server.api.get_tool_preload_service", lambda: None
    )
    app = create_app(
        Config(
            deferred_init=True,
            conversations_path=tmp_path / "conversations",
            bash_events_dir=tmp_path / "bash",
        )
    )
    try:
        if body_error:
            with pytest.raises(RuntimeError, match="application failure"):
                async with api_lifespan(app):
                    raise RuntimeError("application failure")
        else:
            async with api_lifespan(app):
                pass
        assert len(calls) == 1
        assert calls[0][0] == 5000
        assert calls[0][1] != loop_thread
        if outcome is False:
            assert "trace flush timed out" in caplog.text
        elif isinstance(outcome, Exception):
            assert "trace flush failed" in caplog.text
    finally:
        provider.shutdown()


async def test_no_flush_when_tracing_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr("openhands.agent_server.api.get_vscode_service", lambda: None)
    monkeypatch.setattr(
        "openhands.agent_server.api.get_tool_preload_service", lambda: None
    )
    flushed = []
    monkeypatch.setattr(
        TracerProvider, "force_flush", lambda self, *a, **k: flushed.append(1)
    )
    app = create_app(
        Config(
            deferred_init=True,
            conversations_path=tmp_path / "conversations",
            bash_events_dir=tmp_path / "bash",
        )
    )
    async with api_lifespan(app):
        pass
    assert flushed == []
