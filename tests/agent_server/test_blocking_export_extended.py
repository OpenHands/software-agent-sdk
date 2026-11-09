import asyncio
import threading
import time

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from openhands.agent_server.api import api_lifespan, create_app
from openhands.agent_server.config import Config


@pytest.mark.parametrize("body_error", [False, True])
async def test_blocking_batch_export_does_not_hold_shutdown(
    monkeypatch, tmp_path, caplog, body_error
):
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    entered = threading.Event()
    release = threading.Event()
    export = exporter.export
    cleanup = []

    def blocked_export(spans):
        entered.set()
        release.wait(timeout=1.5)
        return export(spans)

    monkeypatch.setattr(exporter, "export", blocked_export)
    provider.add_span_processor(
        BatchSpanProcessor(exporter, schedule_delay_millis=60000)
    )
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    monkeypatch.setattr(
        "openhands.agent_server.api._TRACE_FLUSH_TIMEOUT_SECONDS", 0.05, raising=False
    )
    monkeypatch.setattr(
        "openhands.agent_server.api.get_vscode_service", lambda _config: None
    )
    monkeypatch.setattr(
        "openhands.agent_server.api.get_tool_preload_service", lambda _config: None
    )
    monkeypatch.setattr(
        "openhands.agent_server.api.unseal_tool_catalog", lambda: cleanup.append(True)
    )
    app = create_app(
        Config(
            deferred_init=True,
            conversations_path=tmp_path / "conversations",
            bash_events_dir=tmp_path / "bash",
        )
    )
    monkeypatch.setattr(
        "openhands.agent_server.api.TRACE_FLUSH_TIMEOUT_MILLIS", 50, raising=False
    )
    start = time.monotonic()
    try:
        if body_error:
            with pytest.raises(RuntimeError, match="application failure"):
                async with api_lifespan(app):
                    with provider.get_tracer(__name__).start_as_current_span("root"):
                        pass
                    raise RuntimeError("application failure")
        else:
            async with api_lifespan(app):
                with provider.get_tracer(__name__).start_as_current_span("root"):
                    pass
        assert entered.is_set()
        assert time.monotonic() - start < 0.75
        assert cleanup == [True]
        assert "trace flush timed out" in caplog.text
    finally:
        release.set()
        await asyncio.to_thread(provider.shutdown)
