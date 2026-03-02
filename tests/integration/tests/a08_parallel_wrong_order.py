"""
API Compliance Test: Parallel Tool Calls - Wrong Order

Tests how different LLM APIs respond when tool_results appear BEFORE
the assistant message containing the corresponding tool_calls.

Pattern:
    [tool_result A] → [tool_result B] → [assistant with tool_calls [A, B]]
    ↑ Results before the tool_calls!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A08_PARALLEL_WRONG_ORDER


class ParallelWrongOrderTest(BaseAPIComplianceTest):
    """Test API response to tool results appearing before tool calls."""

    @property
    def pattern_name(self) -> str:
        return A08_PARALLEL_WRONG_ORDER.name

    @property
    def pattern_description(self) -> str:
        return A08_PARALLEL_WRONG_ORDER.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with tool results before tool calls."""
        return A08_PARALLEL_WRONG_ORDER.messages
