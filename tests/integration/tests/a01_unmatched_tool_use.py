"""
API Compliance Test: Unmatched tool_use

Tests how different LLM APIs respond when a tool_use message is sent
without a corresponding tool_result.

Pattern:
    [system] → [user] → [assistant with tool_use] → [user message] → API CALL
                                                     ↑ No tool_result!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A01_UNMATCHED_TOOL_USE


class UnmatchedToolUseTest(BaseAPIComplianceTest):
    """Test API response to unmatched tool_use."""

    @property
    def pattern_name(self) -> str:
        return A01_UNMATCHED_TOOL_USE.name

    @property
    def pattern_description(self) -> str:
        return A01_UNMATCHED_TOOL_USE.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with unmatched tool_use."""
        return A01_UNMATCHED_TOOL_USE.messages
