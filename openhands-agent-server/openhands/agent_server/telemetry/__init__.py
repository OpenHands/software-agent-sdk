"""Sanitized product-analytics telemetry for the agent server.

This is **product analytics**, and is distinct from two other things in this
repository that are easy to confuse it with:

* **LLM completion logging** (``LLM.log_completions``) writes full prompts and
  responses to disk for debugging. It is high-fidelity and privacy-sensitive by
  design, and nothing it produces is ever forwarded here.
* **Laminar / OpenTelemetry tracing**
  (``openhands.sdk.observability``) produces distributed traces for latency and
  span analysis. Separate pipeline, separate destination.

What this package emits is a small, versioned, allowlisted set of lifecycle and
failure events whose properties are constrained scalars — never prompts,
messages, paths, secrets, request/response bodies, or tracebacks.

``posthog_exporter`` is deliberately **not** imported here: importing this
package must not pull in the optional vendor dependency.
"""

from openhands.agent_server.telemetry.factory import (
    DiagnosticEventFactory,
    build_runtime_properties,
)
from openhands.agent_server.telemetry.models import (
    TELEMETRY_SCHEMA_VERSION,
    DiagnosticEvent,
    RuntimeProperties,
)
from openhands.agent_server.telemetry.policy import (
    TelemetryConsent,
    TelemetryDecision,
    kill_switch_engaged,
    read_consent,
    resolve,
)
from openhands.agent_server.telemetry.service import (
    build_telemetry_sink,
    emit_server_started,
    emit_server_stopped,
    get_event_factory,
    get_telemetry_sink,
    notify_misc_settings_changed,
    reset_telemetry_sink,
    shutdown_telemetry_sink,
)
from openhands.agent_server.telemetry.sink import (
    BufferedTelemetrySink,
    NoOpTelemetrySink,
    TelemetryExporter,
    TelemetrySink,
)
from openhands.agent_server.telemetry.subscriber import (
    ConversationTelemetryContext,
    TelemetrySubscriber,
)


__all__ = [
    "TELEMETRY_SCHEMA_VERSION",
    "BufferedTelemetrySink",
    "ConversationTelemetryContext",
    "DiagnosticEvent",
    "DiagnosticEventFactory",
    "NoOpTelemetrySink",
    "RuntimeProperties",
    "TelemetryConsent",
    "TelemetryDecision",
    "TelemetryExporter",
    "read_consent",
    "TelemetrySink",
    "TelemetrySubscriber",
    "build_runtime_properties",
    "build_telemetry_sink",
    "emit_server_started",
    "emit_server_stopped",
    "get_event_factory",
    "get_telemetry_sink",
    "kill_switch_engaged",
    "notify_misc_settings_changed",
    "reset_telemetry_sink",
    "resolve",
    "shutdown_telemetry_sink",
]
