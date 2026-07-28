"""Construction and lifecycle of the process-wide telemetry sink.

Mirrors the singleton pattern used by ``persistence/store.py`` so tests can
reset process state between cases.

Everything here degrades rather than fails: a missing optional dependency, an
unconfigured exporter, or a settings-store read error all resolve to the no-op
sink. Analytics must never be able to prevent a server from starting.
"""

import asyncio
from functools import partial
from typing import Any

from openhands.agent_server.config import Config
from openhands.agent_server.telemetry.factory import (
    DiagnosticEventFactory,
    build_runtime_properties,
)
from openhands.agent_server.telemetry.models import (
    EventName,
    ServerLifecycleProperties,
)
from openhands.agent_server.telemetry.policy import TelemetryDecision, resolve
from openhands.agent_server.telemetry.sink import (
    BufferedTelemetrySink,
    NoOpTelemetrySink,
    TelemetryExporter,
    TelemetrySink,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

_telemetry_sink: TelemetrySink | None = None
_event_factory: DiagnosticEventFactory | None = None
_server_started_emitted = False


def _read_misc_settings_sync(config: Config) -> dict[str, Any]:
    from openhands.agent_server.persistence.store import get_settings_store

    # Must pass config: the store singleton is fixed by the first call, and a
    # no-arg call here would disable the cipher process-wide.
    settings = get_settings_store(config).load()
    return dict(settings.misc_settings) if settings is not None else {}


async def _read_decision(config: Config) -> TelemetryDecision:
    """Resolve consent from persisted settings, off the event loop."""
    misc = await asyncio.to_thread(_read_misc_settings_sync, config)
    return resolve(misc)


def _build_exporter(config: Config) -> TelemetryExporter | None:
    """Select an exporter by kind, or ``None`` if it cannot be built."""
    spec = config.telemetry

    if spec.exporter == "posthog":
        api_key = (
            spec.posthog_api_key.get_secret_value()
            if spec.posthog_api_key is not None
            else None
        )
        if not api_key:
            logger.info("Telemetry exporter is 'posthog' but no API key is configured.")
            return None
        try:
            # Lazy: the only line that pulls in the optional vendor dependency.
            from openhands.agent_server.telemetry.posthog_exporter import (
                PostHogExporter,
            )

            return PostHogExporter(api_key, host=spec.posthog_host)
        except ImportError:
            logger.warning(
                "Telemetry exporter is 'posthog' but the extra is not installed; "
                "telemetry is inactive. Install openhands-agent-server[posthog]."
            )
            return None

    if spec.exporter == "http":
        if not spec.http_endpoint:
            logger.info("Telemetry exporter is 'http' but no endpoint is configured.")
            return None
        from openhands.agent_server.telemetry.http_exporter import HttpExporter

        return HttpExporter(
            spec.http_endpoint,
            token=(
                spec.http_token.get_secret_value()
                if spec.http_token is not None
                else None
            ),
        )

    return None


async def build_telemetry_sink(config: Config) -> TelemetrySink:
    """Build the sink for ``config``, or a no-op if telemetry cannot run."""
    global _telemetry_sink, _event_factory

    spec = config.telemetry
    _event_factory = DiagnosticEventFactory(
        runtime=build_runtime_properties(deferred_init=config.deferred_init),
        salt=(
            spec.salt.get_secret_value()
            if spec.salt is not None
            else (
                config.secret_key.get_secret_value()
                if config.secret_key is not None
                else None
            )
        ),
    )

    try:
        exporter = _build_exporter(config)
    except Exception as exc:
        logger.warning(
            "Telemetry exporter could not be constructed (%s); telemetry is inactive.",
            type(exc).__name__,
        )
        exporter = None

    if exporter is None:
        _telemetry_sink = NoOpTelemetrySink()
        return _telemetry_sink

    try:
        decision = await _read_decision(config)
    except Exception as exc:
        # Unreadable settings fail closed.
        logger.debug("Could not read telemetry consent: %s", type(exc).__name__)
        decision = resolve(None)

    sink = BufferedTelemetrySink(
        exporter,
        decision=decision,
        decision_reader=partial(_read_decision, config),
        max_queue_size=spec.max_queue_size,
        event_buffer_size=spec.event_buffer_size,
        flush_delay=spec.flush_delay,
        num_retries=spec.num_retries,
        retry_delay=spec.retry_delay,
    )
    sink.start()

    logger.info(
        "Telemetry initialised: exporter=%s enabled=%s (%s)",
        spec.exporter,
        decision.enabled,
        decision.reason,
    )
    _telemetry_sink = sink
    return sink


def get_telemetry_sink() -> TelemetrySink:
    """Return the process sink, or a no-op if one was never built."""
    if _telemetry_sink is None:
        return NoOpTelemetrySink()
    return _telemetry_sink


def get_event_factory() -> DiagnosticEventFactory | None:
    return _event_factory


async def shutdown_telemetry_sink() -> None:
    """Close the sink, draining what is still permitted to be sent."""
    global _telemetry_sink
    sink = _telemetry_sink
    _telemetry_sink = None
    if sink is not None:
        try:
            await sink.aclose()
        except Exception as exc:
            logger.debug("Telemetry shutdown failed: %s", type(exc).__name__)


def reset_telemetry_sink() -> None:
    """Drop process state without awaiting. For tests."""
    global _telemetry_sink, _event_factory, _server_started_emitted
    _telemetry_sink = None
    _event_factory = None
    _server_started_emitted = False


def emit_server_started() -> None:
    """Emit ``server_started`` once, if telemetry is active.

    Idempotent: a second call while a start is already outstanding is a no-op,
    so a caller that runs on a retry path cannot produce an unpaired second
    start.
    """
    global _server_started_emitted
    if _server_started_emitted:
        return
    if _emit_lifecycle(EventName.SERVER_STARTED):
        _server_started_emitted = True


def emit_server_stopped() -> None:
    """Emit ``server_stopped``, but only if a start was actually emitted."""
    global _server_started_emitted
    if not _server_started_emitted:
        return
    _server_started_emitted = False
    _emit_lifecycle(EventName.SERVER_STOPPED)


def _emit_lifecycle(event_name: EventName) -> bool:
    """Emit a server lifecycle event. Returns whether it was actually sent."""
    try:
        sink = get_telemetry_sink()
        if not sink.enabled:
            return False
        factory = get_event_factory()
        if factory is None:
            return False
        sink.emit(factory.build(event_name, ServerLifecycleProperties()))
        return True
    except Exception:
        logger.debug("Could not emit server lifecycle telemetry", exc_info=True)
        return False


def notify_misc_settings_changed(misc_settings: dict[str, Any] | None) -> None:
    """Re-resolve consent after a settings write.

    The settings-update path is now the only way consent changes. Revocation
    must take effect before the request returns and must discard the queue.
    """
    sink = _telemetry_sink
    if sink is None:
        return
    try:
        sink.on_decision_changed(resolve(misc_settings))
    except Exception as exc:
        logger.debug("Consent propagation failed: %s", type(exc).__name__)
