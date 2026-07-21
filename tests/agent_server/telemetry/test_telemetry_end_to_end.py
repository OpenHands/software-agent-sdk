"""End-to-end validation of the three scenarios the feature must demonstrate.

1. An opted-in local session produces sanitized lifecycle and error events.
2. An opted-out session produces none.
3. An exporter that always fails leaves conversation execution unaffected.

These drive the real sink and subscriber wiring rather than mocks, and assert
on the *serialized payloads* — the bytes that would actually leave the process.
"""

import asyncio
import json
import uuid

import pytest

from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.factory import (
    DiagnosticEventFactory,
    build_runtime_properties,
)
from openhands.agent_server.telemetry.policy import (
    TelemetryConsent,
    TelemetryDecision,
)
from openhands.agent_server.telemetry.sink import BufferedTelemetrySink
from openhands.agent_server.telemetry.subscriber import (
    ConversationTelemetryContext,
    TelemetrySubscriber,
)
from openhands.sdk.event import AgentErrorEvent, ConversationStateUpdateEvent


# Values that must never appear in any emitted payload.
PROMPT = "Refactor the billing module in /Users/alice/acme/billing.py"
SECRET = "sk-ant-api03-DO-NOT-LEAK-THIS"
WORKSPACE_PATH = "/Users/alice/acme"
FORBIDDEN = [PROMPT, SECRET, WORKSPACE_PATH, "Traceback", "billing.py"]


class CapturingExporter:
    def __init__(self):
        self.payloads: list[dict] = []

    async def send(self, events):
        for event in events:
            self.payloads.append(
                {
                    "event": event.event_name,
                    "distinct_id": event.distinct_id,
                    "properties": event.to_payload(),
                }
            )

    async def aclose(self):
        pass


class AlwaysFailingExporter:
    def __init__(self):
        self.attempts = 0

    async def send(self, events):
        self.attempts += 1
        raise ConnectionError("posthog is unreachable")

    async def aclose(self):
        raise ConnectionError("still unreachable")


def build_subscriber(sink, user_id: str | None = "canvas-user-7"):
    factory = DiagnosticEventFactory(
        runtime=build_runtime_properties(deferred_init=False),
        salt="deployment-salt",
    )
    conversation_id = uuid.uuid4()
    return conversation_id, TelemetrySubscriber(
        conversation_id=conversation_id,
        sink=sink,
        factory=factory,
        context=ConversationTelemetryContext(
            conversation_ref=factory.conversation_ref(conversation_id),
            user_id=user_id,
            llm_model_family="anthropic",
            agent_kind="agent",
            tool_count=4,
            is_fork=False,
            has_agent_profile=False,
            workspace_kind="localworkspace",
            confirmation_policy="neverconfirm",
        ),
    )


async def run_session(subscriber: TelemetrySubscriber) -> None:
    """Simulate a conversation that does some work, errors, then finishes."""
    subscriber.emit_started()
    # subscribe_to_events pushes the current state on attach; for a new
    # conversation that baseline is non-terminal.
    await subscriber(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await subscriber(
        AgentErrorEvent(
            error=f"Command failed while running: {PROMPT} (key={SECRET})",
            tool_name="bash",
            tool_call_id="call-1",
        )
    )
    await subscriber(
        ConversationStateUpdateEvent(key="execution_status", value="finished")
    )
    await subscriber.close()


# ── 1. opted in ───────────────────────────────────────────────────────────


async def test_opted_in_session_emits_sanitized_lifecycle_and_error_events():
    exporter = CapturingExporter()
    sink = BufferedTelemetrySink(
        exporter,
        decision=TelemetryDecision(
            consent=TelemetryConsent.GRANTED, enabled=True, reason="settings"
        ),
        flush_delay=0.05,
        shutdown_flush_timeout=1.0,
    )
    sink.start()

    conversation_id, subscriber = build_subscriber(sink)
    await run_session(subscriber)
    await asyncio.wait_for(sink.aclose(), timeout=10)

    names = [p["event"] for p in exporter.payloads]
    assert m.EventName.CONVERSATION_STARTED in names
    assert m.EventName.CONVERSATION_ERROR in names
    assert m.EventName.CONVERSATION_FINISHED in names

    serialized = json.dumps(exporter.payloads)

    # Nothing sensitive survived.
    for forbidden in FORBIDDEN:
        assert forbidden not in serialized, f"leaked {forbidden!r}"

    # The raw conversation id is never shipped.
    assert str(conversation_id) not in serialized
    assert conversation_id.hex not in serialized

    # Correlation identity is the host's, verbatim.
    assert all(p["distinct_id"] == "canvas-user-7" for p in exporter.payloads)

    # Only allowlisted properties, and every event carries the schema version.
    for payload in exporter.payloads:
        assert set(payload["properties"]) <= set(m.EXPECTED_PROPERTY_NAMES)
        assert payload["properties"]["schema_version"] == m.TELEMETRY_SCHEMA_VERSION
        assert payload["properties"]["source"] == "openhands-agent-server"


async def test_opted_in_session_reports_useful_diagnostics():
    """Sanitization must not render the events useless."""
    exporter = CapturingExporter()
    sink = BufferedTelemetrySink(
        exporter,
        decision=TelemetryDecision(
            consent=TelemetryConsent.GRANTED, enabled=True, reason="settings"
        ),
        flush_delay=0.05,
        shutdown_flush_timeout=1.0,
    )
    sink.start()
    _, subscriber = build_subscriber(sink)
    await run_session(subscriber)
    await asyncio.wait_for(sink.aclose(), timeout=10)

    by_name = {p["event"]: p["properties"] for p in exporter.payloads}

    started = by_name[m.EventName.CONVERSATION_STARTED]
    assert started["llm_model_family"] == "anthropic"
    assert started["tool_count"] == 4

    error = by_name[m.EventName.CONVERSATION_ERROR]
    assert error["error_category"] == "tool_execution"
    assert error["tool_name"] == "bash"
    assert len(error["error_fingerprint"]) >= 16

    finished = by_name[m.EventName.CONVERSATION_FINISHED]
    assert finished["terminal_status"] == "finished"
    # Bucketed, not exact.
    assert "-" in finished["duration_bucket"] or finished["duration_bucket"].startswith(
        ("lt-", "unknown")
    )


# ── 2. opted out ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("consent", ["denied", "unset"])
async def test_opted_out_session_emits_nothing(consent):
    exporter = CapturingExporter()
    sink = BufferedTelemetrySink(
        exporter,
        decision=TelemetryDecision(
            consent=consent, enabled=consent == "granted", reason="settings"
        ),
        flush_delay=0.05,
        shutdown_flush_timeout=1.0,
    )
    sink.start()

    _, subscriber = build_subscriber(sink)
    await run_session(subscriber)
    await asyncio.sleep(0.2)
    await asyncio.wait_for(sink.aclose(), timeout=10)

    assert exporter.payloads == []


async def test_revocation_mid_session_stops_delivery_and_drops_the_backlog():
    exporter = CapturingExporter()
    sink = BufferedTelemetrySink(
        exporter,
        decision=TelemetryDecision(
            consent=TelemetryConsent.GRANTED, enabled=True, reason="settings"
        ),
        flush_delay=3600,  # nothing flushes on its own
        shutdown_flush_timeout=1.0,
    )
    sink.start()

    _, subscriber = build_subscriber(sink)
    subscriber.emit_started()
    await subscriber(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await subscriber(AgentErrorEvent(error=SECRET, tool_name="bash", tool_call_id="c1"))

    sink.on_decision_changed(
        TelemetryDecision(
            consent=TelemetryConsent.DENIED, enabled=False, reason="settings"
        )
    )

    await subscriber(
        ConversationStateUpdateEvent(key="execution_status", value="finished")
    )
    await subscriber.close()
    await asyncio.wait_for(sink.aclose(), timeout=10)

    assert exporter.payloads == [], "events survived a revocation"


# ── 3. exporter failure ───────────────────────────────────────────────────


async def test_exporter_failure_leaves_conversation_execution_unaffected():
    """A broken analytics backend must be invisible to the conversation."""
    exporter = AlwaysFailingExporter()
    sink = BufferedTelemetrySink(
        exporter,
        decision=TelemetryDecision(
            consent=TelemetryConsent.GRANTED, enabled=True, reason="settings"
        ),
        flush_delay=0.02,
        num_retries=1,
        retry_delay=0.01,
        shutdown_flush_timeout=0.5,
    )
    sink.start()

    _, subscriber = build_subscriber(sink)

    # The session runs to completion despite every send raising.
    await asyncio.wait_for(run_session(subscriber), timeout=5)
    await asyncio.sleep(0.3)

    assert exporter.attempts >= 1, "exporter was never exercised"
    assert sink._drain_task is not None
    assert not sink._drain_task.done(), "drain task died"

    # Even aclose(), whose exporter.aclose() also raises, must not propagate.
    await asyncio.wait_for(sink.aclose(), timeout=10)


async def test_a_hanging_exporter_does_not_delay_the_conversation():
    class HangingExporter:
        async def send(self, events):
            await asyncio.sleep(3600)

        async def aclose(self):
            pass

    sink = BufferedTelemetrySink(
        HangingExporter(),
        decision=TelemetryDecision(
            consent=TelemetryConsent.GRANTED, enabled=True, reason="settings"
        ),
        flush_delay=0.01,
        shutdown_flush_timeout=0.5,
    )
    sink.start()
    _, subscriber = build_subscriber(sink)

    # If emission were synchronous with delivery, this would hang for an hour.
    await asyncio.wait_for(run_session(subscriber), timeout=2)
    await asyncio.wait_for(sink.aclose(), timeout=10)
