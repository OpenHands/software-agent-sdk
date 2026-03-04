"""
API Compliance Test: Interleaved User Message

Tests how different LLM APIs respond when a user message appears
between tool_use and tool_result.

Pattern:
    [assistant with tool_use] → [user message] → [tool_result]
                                 ↑ Inserted between tool_use and tool_result!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A03_INTERLEAVED_USER_MSG


class InterleavedUserMessageTest(BaseAPIComplianceTest):
    """Test API response to interleaved user message."""

    @property
    def pattern_name(self) -> str:
        return A03_INTERLEAVED_USER_MSG.name

    @property
    def pattern_description(self) -> str:
        return A03_INTERLEAVED_USER_MSG.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with interleaved user message."""
        return A03_INTERLEAVED_USER_MSG.messages
