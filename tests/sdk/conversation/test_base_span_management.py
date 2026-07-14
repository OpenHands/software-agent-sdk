"""Test that BaseConversation properly manages span state to prevent double-ending warnings."""  # noqa: E501

import logging
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.types import TraceMetadataValue
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

    def send_message(
        self,
        message: Any,
        sender: str | None = None,
        *,
        client_context: Any = None,
    ) -> None:
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

    def fork(self, **kwargs: Any) -> "MockConversation":
        """Mock implementation of fork method."""
        raise NotImplementedError("Mock fork not implemented")

    def navigate_to(self, event_id: Any) -> None:
        """Mock implementation of navigate_to method."""
        raise NotImplementedError("Mock navigate_to not implemented")


def test_base_conversation_load_plugin_default_not_supported():
    conversation = MockConversation()

    with pytest.raises(NotImplementedError, match="does not support loading plugins"):
        conversation.load_plugin("plugin@marketplace")


def test_base_conversation_span_management():
    """Test that BaseConversation properly manages span state to prevent double-ending."""  # noqa: E501

    # Create a minimal BaseConversation instance for testing
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability"
        ) as mock_should_enable,
        patch("openhands.sdk.conversation.base.start_root_span") as mock_start_span,
        patch("openhands.sdk.conversation.base.end_root_span") as mock_end_span,
    ):
        # Test when observability is enabled
        mock_should_enable.return_value = True
        fake_root = MagicMock(name="root-span")
        mock_start_span.return_value = fake_root

        # Start span
        conversation._start_observability_span("test-session-id")
        mock_start_span.assert_called_once_with(
            "conversation",
            session_id="test-session-id",
            user_id=None,
            metadata=None,
            tags=None,
            attributes=None,
        )
        assert conversation._span_ended is False
        assert conversation._observability_root_span is fake_root

        # Calling start again is idempotent (already-started conversations
        # must not produce a second root span).
        conversation._start_observability_span("test-session-id")
        assert mock_start_span.call_count == 1

        # End span first time
        conversation._end_observability_span()
        mock_end_span.assert_called_once_with(fake_root)
        assert conversation._span_ended is True
        assert conversation._observability_root_span is None

        # Try to end span again - should not call end_root_span again
        conversation._end_observability_span()
        assert mock_end_span.call_count == 1  # Still only called once
        assert conversation._span_ended is True


def test_base_conversation_passes_observability_metadata_and_tag_attributes():
    """Conversation metadata, span tags, and conversation tags reach the root span."""
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability",
            return_value=True,
        ),
        patch("openhands.sdk.conversation.base.start_root_span") as mock_start_span,
    ):
        metadata: dict[str, TraceMetadataValue] = {
            "repo_name": "OpenHands/software-agent-sdk"
        }
        span_tags = ["repo:OpenHands/software-agent-sdk"]
        conversation_tags = {"automationid": "auto-1", "automationrunid": "run-1"}

        conversation._start_observability_span(
            "test-session-id",
            user_id="user-42",
            metadata=metadata,
            tags=span_tags,
            conversation_tags=conversation_tags,
        )

        mock_start_span.assert_called_once_with(
            "conversation",
            session_id="test-session-id",
            user_id="user-42",
            metadata=metadata,
            tags=span_tags,
            attributes={
                "conversation.tags.automationid": "auto-1",
                "conversation.tags.automationrunid": "run-1",
            },
        )


def test_base_conversation_uses_custom_observability_span_name_as_child_span():
    """Custom span names are emitted as child spans under the conversation root."""
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability",
            return_value=True,
        ),
        patch("openhands.sdk.conversation.base.start_root_span") as mock_start_span,
        patch("openhands.sdk.conversation.base.start_child_span") as mock_child_span,
    ):
        conversation._start_observability_span(
            "test-session-id",
            span_name="pr_review_evaluation",
        )

        mock_start_span.assert_called_once_with(
            "conversation",
            session_id="test-session-id",
            user_id=None,
            metadata=None,
            tags=None,
            attributes=None,
        )
        mock_child_span.assert_called_once_with(
            mock_start_span.return_value,
            "pr_review_evaluation",
            tags=None,
        )


def test_base_conversation_span_management_disabled():
    """Test that BaseConversation doesn't perform span operations when observability is disabled."""  # noqa: E501

    # Create a minimal BaseConversation instance for testing
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability"
        ) as mock_should_enable,
        patch("openhands.sdk.conversation.base.start_root_span") as mock_start_span,
        patch("openhands.sdk.conversation.base.end_root_span") as mock_end_span,
    ):
        # Test when observability is disabled
        mock_should_enable.return_value = False

        # Try to start span - should not call start_root_span
        conversation._start_observability_span("test-session-id")
        mock_start_span.assert_not_called()
        assert conversation._span_ended is False
        assert conversation._observability_root_span is None

        # Ending without a started root span is a no-op and marks ended.
        conversation._end_observability_span()
        mock_end_span.assert_not_called()
        assert conversation._span_ended is True


def test_base_conversation_no_span_warnings(caplog):
    """Test that BaseConversation doesn't produce span warnings during normal operation."""  # noqa: E501

    # Create a minimal BaseConversation instance for testing
    conversation = MockConversation()

    with (
        patch(
            "openhands.sdk.conversation.base.should_enable_observability",
            return_value=True,
        ),
        patch("openhands.sdk.conversation.base.start_root_span"),
        patch("openhands.sdk.conversation.base.end_root_span"),
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
