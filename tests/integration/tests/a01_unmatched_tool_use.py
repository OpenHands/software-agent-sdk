"""
API Compliance Test: Unmatched tool_use

Tests how different LLM APIs respond when a tool_use message is sent
without a corresponding tool_result.


Pattern:
    [system] → [user] → [assistant with tool_use] → [user message] → API CALL
                                                     ↑ No tool_result!
"""

from openhands.sdk.llm import Message, MessageToolCall, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "unmatched_tool_use"
DESCRIPTION = """
Sends a conversation where an assistant message contains a tool_use (tool_calls),
but no tool_result (tool message) follows before the next user message.

This pattern can occur when:
- ObservationEvent is delayed or lost
- User message arrives before observation is recorded
- Event sync issues during conversation resume
"""


class UnmatchedToolUseTest(BaseAPIComplianceTest):
    """Test API response to unmatched tool_use."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with unmatched tool_use."""
        return [
            Message(
                role="system",
                content=[TextContent(text="You are a helpful assistant.")],
            ),
            Message(
                role="user",
                content=[TextContent(text="List the files in the current directory.")],
            ),
            # Assistant message with tool_use
            Message(
                role="assistant",
                content=[TextContent(text="I'll list the files for you.")],
                tool_calls=[
                    MessageToolCall(
                        id="call_abc123",
                        name="terminal",
                        arguments='{"command": "ls -la"}',
                        origin="completion",
                    )
                ],
            ),
            # NOTE: No tool_result follows! Directly another user message.
            Message(
                role="user",
                content=[TextContent(text="What was the result?")],
            ),
        ]
