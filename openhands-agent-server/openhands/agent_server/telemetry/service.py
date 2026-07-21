"""Construction and lifecycle of the process-wide telemetry sink.

Mirrors the singleton pattern used by ``persistence/store.py`` so tests can
reset process state between cases.

Everything here degrades rather than fails: a missing optional dependency, an
absent API key, or a settings-store read error all resolve to the no-op sink.
Analytics must never be able to prevent a server from starting.
"""

from __future__ import annotations

import asyncio
from functools import partial

from openhands.agent_server.config import Config
from openhands.agent_server.telemetry.factory import (
    DiagnosticEventFactory,
    build_runtime_properties,
)
from openhands.agent_server.telemetry.models import (
    SERVER_STARTED,
    SERVER_STOPPED,
    EventNameLiteral,
    ServerLifecycleProperties,
)
from openhands.agent_server.telemetry.policy import (
    TelemetryConsent,
    kill_switch_engaged,
    resolve,
)
from openhands.agent_server.telemetry.sink import (
    BufferedTelemetrySink,
    NoOpTelemetrySink,
    TelemetrySink,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

_telemetry_sink: TelemetrySink | None = None
_event_factory: DiagnosticEventFactory | None = None
_server_started_emitted = False


def _read_consent_sync(config: Config) -> TelemetryConsent:
    from openhands.agent_server.persistence.store import get_settings_store

    # Must pass config: the store singleton is fixed by the first call, and a
    # no-arg call here would disable the cipher process-wide.
    settings = get_settings_store(config).load()
    if settings is None:
        return "unset"
    return settings.telemetry_consent


async def _read_consent(config: Config) -> TelemetryConsent:
    """Read persisted consent from the settings store.

    The store is a synchronous, lock-protected file read, so it runs on a
    worker thread. Called only from the drain task, never from ``emit``.
    """
    return await asyncio.to_thread(_read_consent_sync, config)


async def build_telemetry_sink(config: Config) -> TelemetrySink:
    """Build the sink for ``config``, or a no-op if telemetry can't run."""
    global _telemetry_sink, _event_factory

    spec = config.telemetry
    _event_factory = DiagnosticEventFactory(
        runtime=build_runtime_properties(
            mode=spec.mode, deferred_init=config.deferred_init
        ),
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

    if kill_switch_engaged():
        logger.info("Telemetry disabled by DO_NOT_TRACK / OH_TELEMETRY_DISABLED")
        _telemetry_sink = NoOpTelemetrySink()
        return _telemetry_sink

    if spec.mode == "disabled":
        _telemetry_sink = NoOpTelemetrySink()
        return _telemetry_sink

    api_key = (
        spec.posthog_api_key.get_secret_value()
        if spec.posthog_api_key is not None
        else None
    )
    if not api_key:
        logger.info(
            "Telemetry mode is %s but no PostHog API key is configured; "
            "telemetry is inactive.",
            spec.mode,
        )
        _telemetry_sink = NoOpTelemetrySink()
        return _telemetry_sink

    try:
        # Lazy: the only line that pulls in the optional vendor dependency.
        from openhands.agent_server.telemetry.posthog_exporter import PostHogExporter

        exporter = PostHogExporter(api_key, host=spec.posthog_host)
    except ImportError:
        logger.warning(
            "Telemetry mode is %s but the 'posthog' extra is not installed; "
            "telemetry is inactive. Install openhands-agent-server[posthog].",
            spec.mode,
        )
        _telemetry_sink = NoOpTelemetrySink()
        return _telemetry_sink
    except Exception as exc:
        logger.warning(
            "Telemetry exporter could not be constructed (%s); telemetry is inactive.",
            type(exc).__name__,
        )
        _telemetry_sink = NoOpTelemetrySink()
        return _telemetry_sink

    consent: TelemetryConsent = "unset"
    if spec.mode == "local_opt_in":
        try:
            consent = await _read_consent(config)
        except Exception as exc:
            logger.debug("Could not read telemetry consent: %s", type(exc).__name__)
            consent = "unset"

    sink = BufferedTelemetrySink(
        exporter,
        mode=spec.mode,
        consent=consent,
        consent_reader=(
            partial(_read_consent, config) if spec.mode == "local_opt_in" else None
        ),
        max_queue_size=spec.max_queue_size,
        event_buffer_size=spec.event_buffer_size,
        flush_delay=spec.flush_delay,
        num_retries=spec.num_retries,
        retry_delay=spec.retry_delay,
    )
    sink.start()

    decision = resolve(spec.mode, consent)
    logger.info(
        "Telemetry initialised: mode=%s enabled=%s (%s)",
        spec.mode,
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
    """Emit ``server_started``, once, if telemetry is active.

    Called after the sink is built. In deferred-init mode that is *after*
    ``POST /api/init`` rather than at boot, because a warm-pool pod boots with
    telemetry disabled and an event emitted then would be silently dropped.
    """
    global _server_started_emitted
    if _emit_lifecycle(SERVER_STARTED):
        _server_started_emitted = True


def emit_server_stopped() -> None:
    """Emit ``server_stopped``, but only if a start was actually emitted.

    A deferred pod that never received ``POST /api/init`` has no start event;
    an unpaired stop would corrupt uptime and session metrics.
    """
    global _server_started_emitted
    if not _server_started_emitted:
        return
    _server_started_emitted = False
    _emit_lifecycle(SERVER_STOPPED)


def _emit_lifecycle(event_name: EventNameLiteral) -> bool:
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


def notify_consent_changed(consent: TelemetryConsent) -> None:
    """Push a consent change into the live sink immediately.

    Revocation must not wait for the sink's refresh interval, and must discard
    anything already queued.
    """
    sink = _telemetry_sink
    if sink is None:
        return
    try:
        sink.on_consent_changed(consent)
    except Exception as exc:
        logger.debug("Consent propagation failed: %s", type(exc).__name__)
