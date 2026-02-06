"""Test that BaseConversation properly manages span state to prevent double-ending warnings."""  # noqa: E501

import logging
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.llm.llm import LLM
from openhands.sdk.tool.schema import Action, Observation


class MockConversation(BaseConversation):
    """Test implementation of BaseConversation for testing span management."""

    def __init__(self):
        super().__init__()

    # Implement abstract methods with minimal stubs
    def close(self) -> None:
        pass

    @property
    def conversation_stats(self) -> ConversationStats:
        return ConversationStats()

    def generate_title(self, llm: LLM | None = None, max_length: int = 50) -> str:
        return "Test"

    @property
    def id(self) -> UUID:
        return UUID("12345678-1234-5678-9abc-123456789abc")

    def pause(self) -> None:
        pass

    def reject_pending_actions(self, reason: str = "User rejected the action") -> None:
        pass

    def run(self) -> None:
        pass

    def send_message(self, message: Any, sender: str | None = None) -> None:
        pass

    def set_confirmation_policy(self, policy: Any) -> None:
        pass

    def set_security_analyzer(self, analyzer: Any) -> None:
        pass

    @property
    def state(self) -> Any:
        return MagicMock()

    def update_secrets(self, secrets: Any) -> None:
        pass

    def ask_agent(self, question: str) -> str:
        return "Mock response"

    def condense(self) -> None:
        """Mock implementation of condense method."""
        pass

    def execute_tool(self, tool_name: str, action: Action) -> Observation:
        """Mock implementation of execute_tool method."""
        raise NotImplementedError("Mock execute_tool not implemented")


def test_base_conversation_span_management():
    """Test that BaseConversation properly manages span state to prevent double-ending."""  # noqa: E501

    # Create a minimal BaseConversation instance for testing
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability"
        ) as mock_should_enable,
        patch("openhands.sdk.conversation.base.start_active_span") as mock_start_span,
        patch("openhands.sdk.conversation.base.end_active_span") as mock_end_span,
    ):
        # Test when observability is enabled
        mock_should_enable.return_value = True

        # Start span
        conversation._start_observability_span("test-session-id")
        mock_start_span.assert_called_once_with(
            "conversation", session_id="test-session-id"
        )
        assert conversation._span_ended is False

        # End span first time
        conversation._end_observability_span()
        mock_end_span.assert_called_once()
        assert conversation._span_ended is True

        # Try to end span again - should not call end_active_span again
        conversation._end_observability_span()
        assert mock_end_span.call_count == 1  # Still only called once
        assert conversation._span_ended is True


def test_base_conversation_span_management_disabled():
    """Test that BaseConversation doesn't perform span operations when observability is disabled."""  # noqa: E501

    # Create a minimal BaseConversation instance for testing
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability"
        ) as mock_should_enable,
        patch("openhands.sdk.conversation.base.start_active_span") as mock_start_span,
        patch("openhands.sdk.conversation.base.end_active_span") as mock_end_span,
    ):
        # Test when observability is disabled
        mock_should_enable.return_value = False

        # Try to start span - should not call start_active_span
        conversation._start_observability_span("test-session-id")
        mock_start_span.assert_not_called()
        assert conversation._span_ended is False

        # Try to end span - should not call end_active_span
        conversation._end_observability_span()
        mock_end_span.assert_not_called()
        assert conversation._span_ended is False


def test_base_conversation_no_span_warnings(caplog):
    """Test that BaseConversation doesn't produce span warnings during normal operation."""  # noqa: E501

    # Create a minimal BaseConversation instance for testing
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability",
            return_value=True,
        ),
        patch("openhands.sdk.conversation.base.start_active_span"),
        patch("openhands.sdk.conversation.base.end_active_span"),
    ):
        # Capture logs at WARNING level
        with caplog.at_level(logging.WARNING):
            # Start and end span normally
            conversation._start_observability_span("test-session-id")
            conversation._end_observability_span()

            # Try to end again (simulating __del__ calling close())
            conversation._end_observability_span()

        # Check that no span warnings were logged
        span_warnings = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and "span" in record.getMessage().lower()
        ]
        assert len(span_warnings) == 0, (
            f"Found span warnings: {[r.getMessage() for r in span_warnings]}"
        )
