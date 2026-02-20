"""
API Compliance Test: Parallel Tool Calls - Missing Result

Tests how different LLM APIs respond when an assistant message contains
multiple tool_calls but not all of them have corresponding tool_results.

Pattern:
    [assistant with tool_calls [A, B, C]] → [tool_result A] → [tool_result B]
                                                               ↑ Missing result for C!
"""

from openhands.sdk.llm import Message, MessageToolCall, TextContent
from tests.integration.api_compliance.base import BaseAPIComplianceTest


PATTERN_NAME = "parallel_missing_result"
DESCRIPTION = """
Sends a conversation where an assistant message contains multiple parallel
tool_calls, but only some of them have corresponding tool_results.

This pattern can occur when:
- Partial tool execution failure
- Event loss for some observations
- Timeout causes some results to be missing
"""


class ParallelMissingResultTest(BaseAPIComplianceTest):
    """Test API response to parallel tool calls with missing results."""

    @property
    def pattern_name(self) -> str:
        return PATTERN_NAME

    @property
    def pattern_description(self) -> str:
        return DESCRIPTION

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with parallel tool calls missing a result."""
        return [
            Message(
                role="system",
                content=[TextContent(text="You are a helpful assistant.")],
            ),
            Message(
                role="user",
                content=[
                    TextContent(
                        text="Get the weather in San Francisco, Tokyo, and Paris."
                    )
                ],
            ),
            # Assistant message with THREE parallel tool_calls
            Message(
                role="assistant",
                content=[
                    TextContent(text="I'll check the weather in all three cities.")
                ],
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
                    MessageToolCall(
                        id="call_paris",
                        name="terminal",
                        arguments='{"command": "weather paris"}',
                        origin="completion",
                    ),
                ],
            ),
            # Tool result for SF - provided
            Message(
                role="tool",
                content=[TextContent(text="San Francisco: 65°F, Sunny")],
                tool_call_id="call_sf",
                name="terminal",
            ),
            # Tool result for Tokyo - provided
            Message(
                role="tool",
                content=[TextContent(text="Tokyo: 72°F, Cloudy")],
                tool_call_id="call_tokyo",
                name="terminal",
            ),
            # NOTE: Tool result for Paris is MISSING!
            # Next user message arrives before Paris result
            Message(
                role="user",
                content=[TextContent(text="What about Paris?")],
            ),
        ]
