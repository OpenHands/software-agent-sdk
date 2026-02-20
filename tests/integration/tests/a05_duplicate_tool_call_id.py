"""
API Compliance Test: Duplicate tool_call_id

Tests how different LLM APIs respond when multiple tool_result messages
have the same tool_call_id.

Related Issues: #1782

Pattern:
    [assistant with tool_use id=X] → [tool_result id=X] → ... → [tool_result id=X]
                                                                 ↑ Duplicate!
"""

from openhands.sdk.llm import Message, MessageToolCall, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "duplicate_tool_call_id"
DESCRIPTION = """
Sends a conversation where two tool_result messages have the same tool_call_id,
meaning multiple results are provided for a single tool_use.

This pattern can occur when:
- Conversation is resumed and duplicate ObservationEvent is created
- Event sync issues during conversation restore
- get_unmatched_actions() incorrectly identifies action as unmatched
"""


class DuplicateToolCallIdTest(BaseAPIComplianceTest):
    """Test API response to duplicate tool_call_id."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with duplicate tool_call_id."""
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
            # First tool result (correct)
            Message(
                role="tool",
                content=[TextContent(text="file1.txt\nfile2.txt")],
                tool_call_id="call_abc123",
                name="terminal",
            ),
            # Some intervening messages (simulating conversation continuation)
            Message(
                role="user",
                content=[TextContent(text="Thanks! Now what?")],
            ),
            Message(
                role="assistant",
                content=[
                    TextContent(
                        text="You're welcome! Let me know if you need anything else."
                    )
                ],
            ),
            Message(
                role="user",
                content=[TextContent(text="Actually, show me the files again.")],
            ),
            # DUPLICATE: Second tool result with SAME tool_call_id
            Message(
                role="tool",
                content=[TextContent(text="file1.txt\nfile2.txt\nfile3.txt")],
                tool_call_id="call_abc123",  # Same ID as before!
                name="terminal",
            ),
        ]
