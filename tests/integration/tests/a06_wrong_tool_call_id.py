"""
API Compliance Test: Wrong tool_call_id

Tests how different LLM APIs respond when a tool_result references the wrong
tool_call_id (swapped with another tool_use's ID).

Pattern:
    [assistant with tool_use id=A] → [assistant with tool_use id=B] →
    [tool_result id=B] → [tool_result id=A]  ← IDs swapped!
"""

from openhands.sdk.llm import Message, MessageToolCall, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "wrong_tool_call_id"
DESCRIPTION = """
Sends a conversation where tool_results are provided but with swapped IDs,
so each tool_result references the wrong tool_use.

This pattern might occur with:
- ID corruption during serialization
- Race conditions in parallel tool execution
- Manual event manipulation errors
"""


class WrongToolCallIdTest(BaseAPIComplianceTest):
    """Test API response to wrong/swapped tool_call_id."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with swapped tool_call_ids."""
        return [
            Message(
                role="system",
                content=[TextContent(text="You are a helpful assistant.")],
            ),
            Message(
                role="user",
                content=[TextContent(text="Run two commands: ls and pwd")],
            ),
            # First assistant message with tool_use (id=A)
            Message(
                role="assistant",
                content=[TextContent(text="I'll run ls first.")],
                tool_calls=[
                    MessageToolCall(
                        id="call_A_ls",
                        name="terminal",
                        arguments='{"command": "ls"}',
                        origin="completion",
                    )
                ],
            ),
            # First tool result - CORRECT
            Message(
                role="tool",
                content=[TextContent(text="file1.txt\nfile2.txt")],
                tool_call_id="call_A_ls",
                name="terminal",
            ),
            # Second assistant message with tool_use (id=B)
            Message(
                role="assistant",
                content=[TextContent(text="Now I'll run pwd.")],
                tool_calls=[
                    MessageToolCall(
                        id="call_B_pwd",
                        name="terminal",
                        arguments='{"command": "pwd"}',
                        origin="completion",
                    )
                ],
            ),
            # Second tool result - WRONG ID (references first tool_use)
            Message(
                role="tool",
                content=[TextContent(text="/home/user/project")],
                tool_call_id="call_A_ls",  # Wrong! Should be call_B_pwd
                name="terminal",
            ),
        ]
