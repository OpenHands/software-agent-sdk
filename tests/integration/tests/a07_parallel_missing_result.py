"""
API Compliance Test: Parallel Tool Calls - Missing Result

Tests how different LLM APIs respond when an assistant message contains
multiple tool_calls but not all of them have corresponding tool_results.

Pattern:
    [assistant with tool_calls [A, B, C]] → [tool_result A] → [tool_result B]
                                                               ↑ Missing result for C!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A07_PARALLEL_MISSING_RESULT


class ParallelMissingResultTest(BaseAPIComplianceTest):
    """Test API response to parallel tool calls with missing results."""

    @property
    def pattern_name(self) -> str:
        return A07_PARALLEL_MISSING_RESULT.name

    @property
    def pattern_description(self) -> str:
        return A07_PARALLEL_MISSING_RESULT.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with parallel tool calls missing a result."""
        return A07_PARALLEL_MISSING_RESULT.messages
