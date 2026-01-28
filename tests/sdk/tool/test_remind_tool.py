"""Tests for the remind tool example."""

import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from pydantic import Field

from openhands.sdk import ImageContent, Observation, TextContent
from openhands.sdk.tool import ToolDefinition, ToolExecutor, register_tool
from openhands.sdk.tool.schema import Action


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class RemindAction(Action):
    """Action to schedule a reminder message."""

    message: str = Field(description="The reminder message to send")
    delay_seconds: float = Field(
        description="Number of seconds to wait before sending the reminder",
        gt=0,
    )


class RemindObservation(Observation):
    """Observation confirming the reminder was scheduled."""

    scheduled: bool = Field(default=True)
    message: str = Field(description="The scheduled message")
    delay_seconds: float = Field(description="The delay in seconds")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [
            TextContent(
                text=(
                    f"Reminder scheduled: '{self.message}' "
                    f"will be sent in {self.delay_seconds} seconds."
                )
            )
        ]


class RemindExecutor(ToolExecutor[RemindAction, RemindObservation]):
    """Executor that schedules reminders to be sent to the conversation."""

    def __call__(
        self, action: RemindAction, conversation: "LocalConversation | None" = None
    ) -> RemindObservation:
        if conversation is None:
            return RemindObservation(
                scheduled=False,
                message=action.message,
                delay_seconds=action.delay_seconds,
            )

        def send_reminder():
            time.sleep(action.delay_seconds)
            reminder_text = f"[REMINDER]: {action.message}"
            conversation.send_message(reminder_text)

        thread = threading.Thread(target=send_reminder, daemon=True)
        thread.start()

        return RemindObservation(
            scheduled=True,
            message=action.message,
            delay_seconds=action.delay_seconds,
        )


class RemindTool(ToolDefinition[RemindAction, RemindObservation]):
    """A tool that schedules reminder messages to be sent after a delay."""

    @classmethod
    def create(cls, conv_state=None, **kwargs) -> Sequence["RemindTool"]:  # noqa: ARG003
        return [
            cls(
                description="Schedule a reminder message",
                action_type=RemindAction,
                observation_type=RemindObservation,
                executor=RemindExecutor(),
            )
        ]


def test_remind_tool_name():
    """Test that RemindTool has the correct auto-generated name."""
    assert RemindTool.name == "remind"


def test_remind_action_validation():
    """Test RemindAction validation."""
    action = RemindAction(message="Test reminder", delay_seconds=5.0)
    assert action.message == "Test reminder"
    assert action.delay_seconds == 5.0


def test_remind_observation_to_llm_content():
    """Test RemindObservation to_llm_content property."""
    obs = RemindObservation(
        scheduled=True,
        message="Test message",
        delay_seconds=3.0,
    )
    content = obs.to_llm_content
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    assert "Test message" in content[0].text
    assert "3.0 seconds" in content[0].text


def test_remind_executor_without_conversation():
    """Test RemindExecutor returns scheduled=False when no conversation."""
    executor = RemindExecutor()
    action = RemindAction(message="Test", delay_seconds=1.0)

    result = executor(action, conversation=None)

    assert result.scheduled is False
    assert result.message == "Test"
    assert result.delay_seconds == 1.0


def test_remind_executor_with_conversation():
    """Test RemindExecutor schedules reminder with conversation."""
    executor = RemindExecutor()
    action = RemindAction(message="Test reminder", delay_seconds=0.1)

    mock_conversation = MagicMock()
    result = executor(action, conversation=mock_conversation)

    assert result.scheduled is True
    assert result.message == "Test reminder"
    assert result.delay_seconds == 0.1

    # Wait for the reminder to be sent
    time.sleep(0.2)

    # Verify send_message was called with the reminder
    mock_conversation.send_message.assert_called_once()
    call_args = mock_conversation.send_message.call_args[0][0]
    assert "[REMINDER]:" in call_args
    assert "Test reminder" in call_args


def test_remind_tool_create():
    """Test RemindTool.create() returns properly configured tool."""
    tools = RemindTool.create()
    assert len(tools) == 1

    tool = tools[0]
    assert tool.name == "remind"
    assert tool.action_type == RemindAction
    assert tool.observation_type == RemindObservation
    assert tool.executor is not None


def test_remind_tool_registration():
    """Test that RemindTool can be registered and resolved."""
    register_tool("test_remind", RemindTool)

    # Verify it was registered by checking the tool can be created
    tools = RemindTool.create()
    assert len(tools) == 1
    assert tools[0].name == "remind"


def test_remind_tool_execution():
    """Test full tool execution flow."""
    tools = RemindTool.create()
    tool = tools[0]

    action = RemindAction(message="Full test", delay_seconds=0.05)
    mock_conversation = MagicMock()

    result = tool(action, conversation=mock_conversation)

    assert isinstance(result, RemindObservation)
    assert result.scheduled is True

    # Wait for reminder
    time.sleep(0.1)
    mock_conversation.send_message.assert_called_once()
