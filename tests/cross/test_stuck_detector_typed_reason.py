"""Tests for SDK-2: typed StuckPatternDetected reason codes.

These tests exercise the new ``StuckDetector.detect_stuck_reason()`` API and
the conversation-level event emission that goes with it. The goal is that
downstream consumers (eval harnesses, dashboards, retry loops) can branch
on a stable string code instead of parsing free-form log lines.
"""

import uuid

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.stuck_detector import StuckDetector
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
)


def _make_state_with_detector():
    llm = LLM(model="gpt-4o-mini", usage_id="test-llm")
    agent = Agent(llm=llm)
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir="/tmp"),
    )
    return state, StuckDetector(state)


def _push_user(state, text="Please run ls"):
    state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text=text)]),
        )
    )


def _push_action(state, i, command="ls"):
    action = ActionEvent(
        source="agent",
        thought=[TextContent(text="run the command")],
        action=TerminalAction(command=command),
        tool_name="terminal",
        tool_call_id=f"call_{i}",
        tool_call=MessageToolCall(
            id=f"call_{i}",
            name="terminal",
            arguments=f'{{"command": "{command}"}}',
            origin="completion",
        ),
        llm_response_id=f"response_{i}",
    )
    state.events.append(action)
    return action


def _push_observation(state, action, text="file1.txt", command="ls"):
    state.events.append(
        ObservationEvent(
            source="environment",
            observation=TerminalObservation.from_text(
                text=text, command=command, exit_code=0
            ),
            action_id=action.id,
            tool_name="terminal",
            tool_call_id=action.tool_call_id,
        )
    )


def _push_error(state, action, message="boom"):
    state.events.append(
        AgentErrorEvent(
            source="agent",
            error=message,
            tool_name=action.tool_name,
            tool_call_id=action.tool_call_id,
        )
    )


# -- Backwards-compat: is_stuck() still works ------------------------------


def test_is_stuck_still_returns_bool():
    state, det = _make_state_with_detector()
    _push_user(state)
    assert det.is_stuck() is False
    assert isinstance(det.is_stuck(), bool)


# -- Typed reason codes -----------------------------------------------------


def test_detect_reason_none_when_not_stuck():
    state, det = _make_state_with_detector()
    _push_user(state)
    assert det.detect_stuck_reason() is None


def test_detect_reason_repeating_action_observation():
    state, det = _make_state_with_detector()
    _push_user(state)
    for i in range(4):
        a = _push_action(state, i)
        _push_observation(state, a)
    assert det.detect_stuck_reason() == "repeating_action_observation"
    # Backwards-compat boolean continues to work.
    assert det.is_stuck() is True


def test_detect_reason_repeating_action_error():
    """3 identical action-error pairs trigger the action-error pattern
    *before* hitting the action-observation threshold of 4."""
    state, det = _make_state_with_detector()
    _push_user(state)
    for i in range(3):
        a = _push_action(state, i, command="invalid_command")
        _push_error(state, a, message="same error every time")
    assert det.detect_stuck_reason() == "repeating_action_error"


def test_detect_reason_monologue():
    """Three+ repeated agent messages with no user input → monologue."""
    state, det = _make_state_with_detector()
    _push_user(state)
    for i in range(4):
        state.events.append(
            MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant",
                    content=[TextContent(text="I will now do the thing.")],
                ),
            )
        )
    assert det.detect_stuck_reason() == "monologue"


# -- LocalConversation emits typed event ------------------------------------


def test_local_conversation_emits_stuck_pattern_detected_event(monkeypatch):
    """When stuck, the conversation emits a ConversationErrorEvent with
    code='StuckPatternDetected' and the reason embedded in the detail."""
    import tempfile

    from pydantic import SecretStr

    from openhands.sdk.conversation import Conversation
    from openhands.sdk.conversation.state import ConversationExecutionStatus
    from openhands.sdk.event.conversation_error import ConversationErrorEvent

    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    seen: list[ConversationErrorEvent] = []

    def cb(event):
        if isinstance(event, ConversationErrorEvent):
            seen.append(event)

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            persistence_dir=tmpdir,
            workspace=tmpdir,
            callbacks=[cb],
        )
        # Drive the emission helper directly — this exercises the path used
        # by both the sync and async run loops without spinning up a real
        # LLM round-trip.
        conv._emit_stuck_pattern_detected("repeating_action_observation")

    assert conv._state.execution_status == ConversationExecutionStatus.STUCK
    assert len(seen) == 1
    evt = seen[0]
    assert evt.code == "StuckPatternDetected"
    assert "stuck_pattern=repeating_action_observation" in evt.detail
    # Make the "don't retry" guidance discoverable to harnesses that match
    # against the detail field.
    assert "definitive failure" in evt.detail
