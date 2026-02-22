"""
API Compliance Test: Unmatched tool_result

Tests how different LLM APIs respond when a tool_result message references
a tool_call_id that doesn't exist in any prior tool_use.

Pattern:
    [system] → [user] → [assistant (no tool_use)] → [tool with unknown id]
                                                     ↑ References non-existent ID!
"""

from openhands.sdk.llm import Message, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "unmatched_tool_result"
DESCRIPTION = """
Sends a conversation where a tool_result message references a tool_call_id
that doesn't exist in any prior assistant message's tool_calls.

This pattern can occur when:
- tool_call_id is corrupted during serialization
- Tool results are sent for the wrong conversation
- Event ordering issues cause mismatched IDs
"""


class UnmatchedToolResultTest(BaseAPIComplianceTest):
    """Test API response to unmatched tool_result."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with unmatched tool_result."""
        return [
            Message(
                role="system",
                content=[TextContent(text="You are a helpful assistant.")],
            ),
            Message(
                role="user",
                content=[TextContent(text="List the files in the current directory.")],
            ),
            # Assistant message WITHOUT tool_use
            Message(
                role="assistant",
                content=[
                    TextContent(text="I can help you list files. What directory?")
                ],
            ),
            # Tool result that references a non-existent tool_call_id
            Message(
                role="tool",
                content=[TextContent(text="file1.txt\nfile2.txt\nfile3.txt")],
                tool_call_id="call_nonexistent_xyz",
                name="terminal",
            ),
        ]
