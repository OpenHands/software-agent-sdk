"""
API Compliance Test: Interleaved User Message

Tests how different LLM APIs respond when a user message appears
between tool_use and tool_result.

Related Issues: #1841

Pattern:
    [assistant with tool_use] → [user message] → [tool_result]
                                 ↑ Inserted between tool_use and tool_result!
"""

from openhands.sdk.llm import Message, MessageToolCall, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "interleaved_user_message"
DESCRIPTION = """
Sends a conversation where a user message appears between a tool_use
(in assistant message) and its corresponding tool_result (tool message).

This pattern can occur when:
- User sends message via send_message() during pending tool execution
- Events are appended to the event list in incorrect order
- Async message delivery causes race conditions
"""


class InterleavedUserMessageTest(BaseAPIComplianceTest):
    """Test API response to interleaved user message."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with interleaved user message."""
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
            # INTERLEAVED: User message before tool_result
            Message(
                role="user",
                content=[TextContent(text="Actually, can you also show hidden files?")],
            ),
            # Tool result comes AFTER the interleaved user message
            Message(
                role="tool",
                content=[TextContent(text="file1.txt\nfile2.txt")],
                tool_call_id="call_abc123",
                name="terminal",
            ),
        ]
