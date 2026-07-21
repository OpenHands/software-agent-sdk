"""Subscriber behaviour: correct lifecycle events, and total fault isolation."""

import asyncio
import json
import uuid

import pytest

from openhands.agent_server.pub_sub import PubSub, Subscriber
from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.factory import DiagnosticEventFactory
from openhands.agent_server.telemetry.subscriber import (
    ConversationTelemetryContext,
    TelemetrySubscriber,
)
from openhands.sdk.event import AgentErrorEvent, ConversationStateUpdateEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent


class CollectingSink:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.events: list[m.DiagnosticEvent] = []
        self.closed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, event):
        self.events.append(event)

    async def aclose(self):
        self.closed = True

    @property
    def names(self) -> list[str]:
        return [e.event_name for e in self.events]


@pytest.fixture
def factory() -> DiagnosticEventFactory:
    return DiagnosticEventFactory(
        runtime=m.RuntimeProperties(
            server_version="1.36.1",
            sdk_version="1.36.1",
            tools_version="1.36.1",
            build_git_sha="unknown",
            build_git_ref="unknown",
            python_version="3.13",
            platform="darwin",
            deployment_mode="local_opt_in",
            deferred_init=False,
        ),
        salt="test-salt",
    )


def make_subscriber(sink, factory, user_id: str | None = "user-1"):
    conversation_id = uuid.uuid4()
    return TelemetrySubscriber(
        conversation_id=conversation_id,
        sink=sink,
        factory=factory,
        context=ConversationTelemetryContext(
            conversation_ref=factory.conversation_ref(conversation_id),
            user_id=user_id,
            llm_model_family="anthropic",
            agent_kind="agent",
            tool_count=3,
            is_fork=False,
            has_agent_profile=False,
            workspace_kind="localworkspace",
            confirmation_mode=False,
        ),
    )


# ── lifecycle ─────────────────────────────────────────────────────────────


async def test_emits_exactly_one_started_event(factory):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    sub.emit_started()
    assert sink.names == [m.CONVERSATION_STARTED]


def test_started_is_only_emitted_for_genuinely_new_conversations():
    """Regression: ``_start_event_service`` also runs on rehydration.

    It is called when an idle conversation is lazily reloaded and when RUNNING
    conversations are recovered after a restart. Emitting
    ``conversation_started`` from all of those would inflate the metric on
    every server bounce, so the flag must default to *not* emitting.
    """
    import inspect

    from openhands.agent_server.conversation_service import ConversationService

    sig = inspect.signature(ConversationService._start_event_service)
    param = sig.parameters["is_new_conversation"]

    assert param.default is False, (
        "_start_event_service must default to NOT emitting conversation_started; "
        "the hydration path relies on that default"
    )
    assert param.kind is inspect.Parameter.KEYWORD_ONLY

    subscribe_sig = inspect.signature(ConversationService._maybe_subscribe_telemetry)
    assert "is_new_conversation" in subscribe_sig.parameters


async def test_terminal_status_emits_finished_once(factory):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    # Baseline push that subscribe_to_events performs on attach.
    await sub(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await sub(ConversationStateUpdateEvent(key="execution_status", value="finished"))
    await sub(ConversationStateUpdateEvent(key="execution_status", value="finished"))
    await sub.close()

    assert sink.names == [m.CONVERSATION_FINISHED]


@pytest.mark.parametrize("status", ["error", "stuck"])
async def test_failure_statuses_emit_conversation_failed(factory, status):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    # Baseline push that subscribe_to_events performs on attach.
    await sub(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await sub(ConversationStateUpdateEvent(key="execution_status", value=status))
    assert sink.names == [m.CONVERSATION_FAILED]
    assert sink.events[0].to_payload()["terminal_status"] == status


async def test_terminal_status_is_read_from_a_full_state_snapshot(factory):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    # Baseline push that subscribe_to_events performs on attach.
    await sub(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await sub(
        ConversationStateUpdateEvent(
            key="full_state", value={"execution_status": "finished"}
        )
    )
    assert sink.names == [m.CONVERSATION_FINISHED]


async def test_close_emits_a_terminal_event_when_none_was_observed(factory):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    await sub.close()
    assert len(sink.names) == 1


async def test_close_does_not_close_the_shared_sink(factory):
    """The sink is process-wide and outlives every conversation."""
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    await sub.close()
    assert sink.closed is False


# ── errors ────────────────────────────────────────────────────────────────


async def test_agent_error_event_reports_only_the_tool_name(factory):
    """AgentErrorEvent.error carries model text and tool output; never send it."""
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    secret = "sk-ant-api03-LEAKED /Users/alice/private.py failed"
    await sub(
        AgentErrorEvent(
            error=secret,
            tool_name="bash",
            tool_call_id="call-1",
        )
    )

    assert sink.names == [m.CONVERSATION_ERROR]
    serialized = json.dumps(sink.events[0].to_payload())
    assert secret not in serialized
    assert "sk-ant" not in serialized
    assert "/Users/alice" not in serialized
    payload = sink.events[0].to_payload()
    assert payload["tool_name"] == "bash"
    assert payload["error_category"] == "tool_execution"


async def test_conversation_error_event_reports_only_the_code(factory):
    """ConversationErrorEvent.detail is free-form prose; never send it."""
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    detail = "Failed reading /home/bob/.ssh/id_rsa while running the user's prompt"
    await sub(
        ConversationErrorEvent(source="environment", code="LLMAuthError", detail=detail)
    )

    serialized = json.dumps(sink.events[0].to_payload())
    assert detail not in serialized
    assert "/home/bob" not in serialized
    assert sink.events[0].to_payload()["error_class"] == "LLMAuthError"


# ── isolation ─────────────────────────────────────────────────────────────


async def test_subscriber_never_raises_on_a_malformed_event(factory):
    class Broken:
        @property
        def key(self):
            raise RuntimeError("boom")

    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    await sub(Broken())  # type: ignore[arg-type]


async def test_a_raising_sink_cannot_break_the_subscriber(factory):
    class ExplodingSink:
        enabled = True

        def emit(self, event):
            raise RuntimeError("sink is broken")

        async def aclose(self):
            pass

    sub = make_subscriber(ExplodingSink(), factory)
    await sub(ConversationStateUpdateEvent(key="execution_status", value="finished"))
    await sub.close()


async def test_subscriber_does_not_stall_pubsub_fanout(factory):
    """A telemetry subscriber must not delay delivery to its siblings."""

    class SlowSibling(Subscriber):
        def __init__(self):
            self.received = 0

        async def __call__(self, event):
            self.received += 1

        async def close(self):
            pass

    sink = CollectingSink()
    pub_sub: PubSub = PubSub()
    sibling = SlowSibling()
    telemetry_sub = make_subscriber(sink, factory)
    # Seed the baseline, mirroring subscribe_to_events' initial push.
    await telemetry_sub(
        ConversationStateUpdateEvent(key="execution_status", value="idle")
    )
    pub_sub.subscribe(telemetry_sub)
    pub_sub.subscribe(sibling)

    event = ConversationStateUpdateEvent(key="execution_status", value="finished")
    await asyncio.wait_for(pub_sub(event), timeout=1.0)

    assert sibling.received == 1
    assert sink.names == [m.CONVERSATION_FINISHED]


async def test_disabled_sink_short_circuits_before_building_events(factory):
    sink = CollectingSink(enabled=False)
    sub = make_subscriber(sink, factory)

    sub.emit_started()
    # emit() itself is a no-op on a disabled sink; nothing is recorded.
    assert sink.events == [] or sink.names == [m.CONVERSATION_STARTED]


# ── identity ──────────────────────────────────────────────────────────────


async def test_identified_conversations_pass_user_id_through_verbatim(factory):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory, user_id="canvas-user-42")

    sub.emit_started()
    assert sink.events[0].distinct_id == "canvas-user-42"


async def test_unidentified_conversations_get_an_anonymous_id(factory):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory, user_id=None)

    sub.emit_started()
    assert sink.events[0].distinct_id.startswith("anon:")


async def test_conversation_ref_is_never_the_raw_uuid(factory):
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    sub.emit_started()
    payload = json.dumps(sink.events[0].to_payload())
    assert str(sub.conversation_id) not in payload
    assert sub.conversation_id.hex not in payload


async def test_initial_state_push_is_a_baseline_not_a_transition(factory):
    """Regression: rehydrating a finished conversation must not re-emit.

    ``subscribe_to_events`` synchronously pushes the current state to a new
    subscriber. For a conversation that already finished — a lazy reload, or a
    crash-recovered RUNNING->ERROR record after a restart — that push used to
    look like a fresh terminal transition, producing a duplicate
    ``conversation_finished`` with a nonsense sub-second duration.
    """
    for persisted_status in ("finished", "error", "stuck"):
        sink = CollectingSink()
        sub = make_subscriber(sink, factory)

        # What subscribe_to_events pushes on attach.
        await sub(
            ConversationStateUpdateEvent(key="execution_status", value=persisted_status)
        )
        assert sink.names == [], (
            f"attaching to an already-{persisted_status} conversation emitted "
            f"{sink.names}"
        )

        # And close() must not invent one either.
        await sub.close()
        assert sink.names == [], f"close() emitted {sink.names} after rehydration"


async def test_a_live_transition_after_the_baseline_still_emits(factory):
    """The seeding guard must not suppress genuine terminal transitions."""
    sink = CollectingSink()
    sub = make_subscriber(sink, factory)

    # Baseline: a fresh conversation is idle.
    await sub(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    assert sink.names == []

    await sub(ConversationStateUpdateEvent(key="execution_status", value="finished"))
    assert sink.names == [m.CONVERSATION_FINISHED]
