"""Tests for critic implementations and registry."""

import json

import pytest

from openhands.sdk.critic import (
    AgentFinishedCritic,
    CriticBase,
    CriticResult,
    EmptyPatchCritic,
    PassCritic,
)
from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.tool.schema import Action


# Define a dummy action class once to avoid duplicate kind errors
class DummyAction(Action):
    """A simple dummy action for testing purposes."""

    pass


def test_critic_result_success_threshold():
    """Test that CriticResult determines success based on threshold."""
    # Score above threshold should be success
    result_success = CriticResult(score=0.8, message="Success")
    assert result_success.success is True

    # Score at threshold should be success
    result_at_threshold = CriticResult(score=0.5, message="At threshold")
    assert result_at_threshold.success is True

    # Score below threshold should not be success
    result_fail = CriticResult(score=0.3, message="Fail")
    assert result_fail.success is False


def test_critic_result_validation():
    """Test that CriticResult validates score bounds."""
    # Valid scores
    CriticResult(score=0.0, message="Min")
    CriticResult(score=1.0, message="Max")

    # Invalid scores should raise validation error
    with pytest.raises(Exception):  # Pydantic ValidationError
        CriticResult(score=-0.1, message="Below min")

    with pytest.raises(Exception):  # Pydantic ValidationError
        CriticResult(score=1.1, message="Above max")


def test_critic_result_message_defaults_to_none():
    """message is optional; omitting it must yield None, not a required-field error.

    Regression: the event store persists events with ``model_dump_json(
    exclude_none=True)``, which drops ``message: null``.  On resume the event is
    validated from ``{"score": 1.0}``; if ``message`` is a required field that
    reload fails (``critic_result.message Field required``) and the session
    cannot be resumed.
    """
    # Direct construction without message.
    result = CriticResult(score=1.0)
    assert result.message is None

    # Round-trip through the exact persistence shape: exclude_none drops the
    # null message, then validation must accept the message-less payload.
    dumped = result.model_dump_json(exclude_none=True)
    assert json.loads(dumped) == {"score": 1.0}
    reloaded = CriticResult.model_validate_json(dumped)
    assert reloaded.score == 1.0
    assert reloaded.message is None

    # A non-null message still round-trips unchanged.
    msg = CriticResult(score=0.0, message="blocker")
    assert json.loads(msg.model_dump_json(exclude_none=True))["message"] == "blocker"
    assert CriticResult.model_validate_json(
        msg.model_dump_json(exclude_none=True)
    ).message == "blocker"


def test_pass_critic_always_succeeds():
    """Test that PassCritic always returns success."""
    critic = PassCritic()

    # Empty events and no patch
    result = critic.evaluate([], None)
    assert result.score == 1.0
    assert result.success is True

    # With events but no patch
    events = [
        ActionEvent(
            thought=[TextContent(text="thinking")],
            tool_name="test",
            tool_call_id="test_id",
            tool_call=MessageToolCall(
                id="test_id",
                name="test",
                arguments=json.dumps({}),
                origin="completion",
            ),
            llm_response_id="resp_123",
        )
    ]
    result = critic.evaluate(events, None)
    assert result.score == 1.0
    assert result.success is True

    # With events and patch
    result = critic.evaluate(events, "some patch")
    assert result.score == 1.0
    assert result.success is True


def test_empty_patch_critic_with_empty_patch():
    """Test EmptyPatchCritic returns failure for empty patches."""
    critic = EmptyPatchCritic()

    # None patch
    result = critic.evaluate([], None)
    assert result.score == 0.0
    assert result.success is False
    assert result.message is not None
    assert "empty" in result.message.lower()

    # Empty string patch
    result = critic.evaluate([], "")
    assert result.score == 0.0
    assert result.success is False

    # Whitespace-only patch
    result = critic.evaluate([], "   \n\t  ")
    assert result.score == 0.0
    assert result.success is False


def test_empty_patch_critic_with_non_empty_patch():
    """Test EmptyPatchCritic returns success for non-empty patches."""
    critic = EmptyPatchCritic()

    patch = """
    diff --git a/file.py b/file.py
    index abc123..def456 100644
    --- a/file.py
    +++ b/file.py
    @@ -1,3 +1,4 @@
    +# New line
     print("hello")
    """

    result = critic.evaluate([], patch)
    assert result.score == 1.0
    assert result.success is True
    assert result.message is not None
    assert "non-empty" in result.message.lower()


def test_agent_finished_critic_with_empty_patch():
    """Test AgentFinishedCritic fails when patch is empty."""
    critic = AgentFinishedCritic()

    # Create events with FinishAction
    finish_action = FinishAction(message="Task completed")
    events = [
        ActionEvent(
            thought=[TextContent(text="I finished the task")],
            action=finish_action,
            tool_name="finish",
            tool_call_id="finish_id",
            tool_call=MessageToolCall(
                id="finish_id",
                name="finish",
                arguments=json.dumps({"message": "Task completed"}),
                origin="completion",
            ),
            llm_response_id="resp_finish",
        )
    ]

    # Should fail with empty patch even though agent finished
    result = critic.evaluate(events, None)
    assert result.score == 0.0
    assert result.success is False
    assert result.message is not None
    assert "empty" in result.message.lower()


def test_agent_finished_critic_without_finish_action():
    """Test AgentFinishedCritic fails when no FinishAction present."""
    critic = AgentFinishedCritic()

    patch = "diff --git a/file.py"

    # Empty events
    result = critic.evaluate([], patch)
    assert result.score == 0.0
    assert result.success is False

    # Events without FinishAction
    other_action = DummyAction()
    events = [
        ActionEvent(
            thought=[TextContent(text="doing something")],
            action=other_action,
            tool_name="other",
            tool_call_id="other_id",
            tool_call=MessageToolCall(
                id="other_id",
                name="other",
                arguments=json.dumps({}),
                origin="completion",
            ),
            llm_response_id="resp_other",
        )
    ]

    result = critic.evaluate(events, patch)
    assert result.score == 0.0
    assert result.success is False
    assert result.message is not None
    assert "finish" in result.message.lower()


def test_agent_finished_critic_success():
    """Test AgentFinishedCritic succeeds with FinishAction and non-empty patch."""
    critic = AgentFinishedCritic()

    patch = """
    diff --git a/file.py b/file.py
    --- a/file.py
    +++ b/file.py
    @@ -1 +1,2 @@
     original line
    +new line
    """

    finish_action = FinishAction(message="Task completed successfully")
    events = [
        ActionEvent(
            thought=[TextContent(text="Starting task")],
            action=None,
            tool_name="read",
            tool_call_id="read_id",
            tool_call=MessageToolCall(
                id="read_id",
                name="read",
                arguments=json.dumps({}),
                origin="completion",
            ),
            llm_response_id="resp_read",
        ),
        ActionEvent(
            thought=[TextContent(text="Finishing task")],
            action=finish_action,
            tool_name="finish",
            tool_call_id="finish_id",
            tool_call=MessageToolCall(
                id="finish_id",
                name="finish",
                arguments=json.dumps({"message": "Task completed successfully"}),
                origin="completion",
            ),
            llm_response_id="resp_finish_success",
        ),
    ]

    result = critic.evaluate(events, patch)
    assert result.score == 1.0
    assert result.success is True


def test_agent_finished_critic_last_action_not_finish():
    """Test AgentFinishedCritic fails when last action is not FinishAction."""
    critic = AgentFinishedCritic()

    patch = "diff --git a/file.py"

    finish_action = FinishAction(message="Task completed")
    other_action = DummyAction()

    # FinishAction is not the last action
    events = [
        ActionEvent(
            thought=[TextContent(text="Finishing")],
            action=finish_action,
            tool_name="finish",
            tool_call_id="finish_id",
            tool_call=MessageToolCall(
                id="finish_id",
                name="finish",
                arguments=json.dumps({"message": "Task completed"}),
                origin="completion",
            ),
            llm_response_id="resp_finish_mid",
        ),
        ActionEvent(
            thought=[TextContent(text="Doing more")],
            action=other_action,
            tool_name="other",
            tool_call_id="other_id",
            tool_call=MessageToolCall(
                id="other_id",
                name="other",
                arguments=json.dumps({}),
                origin="completion",
            ),
            llm_response_id="resp_other_last",
        ),
    ]

    result = critic.evaluate(events, patch)
    assert result.score == 0.0
    assert result.success is False


def test_critic_base_is_abstract():
    """Test that CriticBase cannot be instantiated directly."""
    with pytest.raises(TypeError):
        CriticBase()  # type: ignore
