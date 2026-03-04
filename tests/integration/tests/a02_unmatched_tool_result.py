"""
API Compliance Test: Unmatched tool_result

Tests how different LLM APIs respond when a tool_result message references
a tool_call_id that doesn't exist in any prior tool_use.

Pattern:
    [system] → [user] → [assistant (no tool_use)] → [tool with unknown id]
                                                     ↑ References non-existent ID!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A02_UNMATCHED_TOOL_RESULT


class UnmatchedToolResultTest(BaseAPIComplianceTest):
    """Test API response to unmatched tool_result."""

    @property
    def pattern_name(self) -> str:
        return A02_UNMATCHED_TOOL_RESULT.name

    @property
    def pattern_description(self) -> str:
        return A02_UNMATCHED_TOOL_RESULT.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with unmatched tool_result."""
        return A02_UNMATCHED_TOOL_RESULT.messages
