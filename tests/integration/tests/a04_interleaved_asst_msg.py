"""
API Compliance Test: Interleaved Assistant Message

Tests how different LLM APIs respond when an assistant message (without tool_calls)
appears between tool_use and tool_result.

Pattern:
    [assistant with tool_use] → [assistant message] → [tool_result]
                                 ↑ Another assistant turn before tool_result!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A04_INTERLEAVED_ASST_MSG


class InterleavedAssistantMessageTest(BaseAPIComplianceTest):
    """Test API response to interleaved assistant message."""

    @property
    def pattern_name(self) -> str:
        return A04_INTERLEAVED_ASST_MSG.name

    @property
    def pattern_description(self) -> str:
        return A04_INTERLEAVED_ASST_MSG.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with interleaved assistant message."""
        return A04_INTERLEAVED_ASST_MSG.messages
