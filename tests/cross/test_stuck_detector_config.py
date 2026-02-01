"""Tests for configurable stuck detection thresholds."""

from pydantic import SecretStr

from openhands.sdk import Agent, LocalConversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import LLM, MessageToolCall, TextContent
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
)


def test_custom_action_observation_threshold():
    """Test that custom thresholds work correctly for action-observation loops."""
    # Create conversation with higher threshold
    conv = LocalConversation(
        Agent(llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test"))),
        workspace="/tmp",
        stuck_detection=True,
        stuck_detection_thresholds={"action_observation": 6},
    )

    # Add a user message first
    conv.send_message("start")

    # Create identical action-observation pairs
    def create_action_obs():
        action = ActionEvent(
            source="agent",
            thought=[TextContent(text="I need to run ls command")],
            action=TerminalAction(command="ls"),
            tool_name="execute_bash",
            tool_call_id="call_1",
            tool_call=MessageToolCall(
                id="call_1",
                name="execute_bash",
                arguments='{"command": "ls"}',
                origin="completion",
            ),
            llm_response_id="response_1",
        )
        observation = ObservationEvent(
            source="environment",
            observation=TerminalObservation(
                content=[TextContent(text="file1.txt")], command="ls", exit_code=0
            ),
            action_id=action.id,
            tool_name="execute_bash",
            tool_call_id="call_1",
        )
        return action, observation

    # Add 4 pairs (would trigger default threshold of 4)
    for _ in range(4):
        action, observation = create_action_obs()
        conv._state.events.append(action)
        conv._state.events.append(observation)

    # Should NOT be stuck with threshold=6
    assert conv._stuck_detector is not None
    assert not conv._stuck_detector.is_stuck()

    # Add 2 more pairs to reach threshold of 6
    for _ in range(2):
        action, observation = create_action_obs()
        conv._state.events.append(action)
        conv._state.events.append(observation)

    # Now should be stuck
    assert conv._stuck_detector.is_stuck()


def test_mixed_custom_thresholds():
    """Test setting multiple custom thresholds at once."""
    conv = LocalConversation(
        Agent(llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test"))),
        workspace="/tmp",
        stuck_detection=True,
        stuck_detection_thresholds={
            "action_observation": 8,
            "action_error": 6,
            "monologue": 10,
        },
    )

    assert conv._stuck_detector is not None
    assert conv._stuck_detector.action_observation_threshold == 8
    assert conv._stuck_detector.action_error_threshold == 6
    assert conv._stuck_detector.monologue_threshold == 10
    # alternating_pattern should use default
    assert conv._stuck_detector.alternating_pattern_threshold == 6
