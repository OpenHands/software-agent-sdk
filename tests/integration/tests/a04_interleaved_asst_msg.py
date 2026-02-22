"""
API Compliance Test: Interleaved Assistant Message

Tests how different LLM APIs respond when an assistant message (without tool_calls)
appears between tool_use and tool_result.

Pattern:
    [assistant with tool_use] → [assistant message] → [tool_result]
                                 ↑ Another assistant turn before tool_result!
"""

from openhands.sdk.llm import Message, MessageToolCall, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "interleaved_assistant_message"
DESCRIPTION = """
Sends a conversation where an assistant message (without tool_calls) appears
between a tool_use and its corresponding tool_result.

This pattern might occur in edge cases with:
- Malformed condensation that inserts summary messages incorrectly
- Manual event manipulation
- Corrupted conversation history
"""


class InterleavedAssistantMessageTest(BaseAPIComplianceTest):
    """Test API response to interleaved assistant message."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with interleaved assistant message."""
        return [
            Message(
                role="system",
                content=[TextContent(text="You are a helpful assistant.")],
            ),
            Message(
                role="user",
                content=[TextContent(text="List the files in the current directory.")],
            ),
            # First assistant message with tool_use
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
            # INTERLEAVED: Another assistant message without tool_calls
            Message(
                role="assistant",
                content=[TextContent(text="The command is running...")],
            ),
            # Tool result comes AFTER the interleaved assistant message
            Message(
                role="tool",
                content=[TextContent(text="file1.txt\nfile2.txt")],
                tool_call_id="call_abc123",
                name="terminal",
            ),
        ]
