"""
API Compliance Test: Parallel Tool Calls - Wrong Order

Tests how different LLM APIs respond when tool_results appear BEFORE
the assistant message containing the corresponding tool_calls.

Pattern:
    [tool_result A] → [tool_result B] → [assistant with tool_calls [A, B]]
    ↑ Results before the tool_calls!
"""

from openhands.sdk.llm import Message, MessageToolCall, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "parallel_wrong_order"
DESCRIPTION = """
Sends a conversation where tool_results appear before the assistant message
that contains the corresponding tool_calls. This is a severe ordering violation.

This pattern might occur with:
- Severe event ordering bugs
- Manual conversation manipulation
- Corrupted event stream
"""


class ParallelWrongOrderTest(BaseAPIComplianceTest):
    """Test API response to tool results appearing before tool calls."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with tool results before tool calls."""
        return [
            Message(
                role="system",
                content=[TextContent(text="You are a helpful assistant.")],
            ),
            Message(
                role="user",
                content=[TextContent(text="Check the weather in SF and Tokyo.")],
            ),
            # Tool results appear FIRST (wrong!)
            Message(
                role="tool",
                content=[TextContent(text="San Francisco: 65°F, Sunny")],
                tool_call_id="call_sf",
                name="terminal",
            ),
            Message(
                role="tool",
                content=[TextContent(text="Tokyo: 72°F, Cloudy")],
                tool_call_id="call_tokyo",
                name="terminal",
            ),
            # Assistant message with tool_calls comes AFTER tool_results
            Message(
                role="assistant",
                content=[TextContent(text="I'll check both cities.")],
                tool_calls=[
                    MessageToolCall(
                        id="call_sf",
                        name="terminal",
                        arguments='{"command": "weather sf"}',
                        origin="completion",
                    ),
                    MessageToolCall(
                        id="call_tokyo",
                        name="terminal",
                        arguments='{"command": "weather tokyo"}',
                        origin="completion",
                    ),
                ],
            ),
        ]
