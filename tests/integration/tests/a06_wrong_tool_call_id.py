"""
API Compliance Test: Wrong tool_call_id

Tests how different LLM APIs respond when a tool_result references the wrong
tool_call_id (one that has already been completed).

Pattern:
    [assistant with tool_use id=A] → [tool_result id=A] →
    [assistant with tool_use id=B] → [tool_result id=A]  ← References completed ID!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A06_WRONG_TOOL_CALL_ID


class WrongToolCallIdTest(BaseAPIComplianceTest):
    """Test API response to wrong/swapped tool_call_id."""

    @property
    def pattern_name(self) -> str:
        return A06_WRONG_TOOL_CALL_ID.name

    @property
    def pattern_description(self) -> str:
        return A06_WRONG_TOOL_CALL_ID.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with swapped tool_call_ids."""
        return A06_WRONG_TOOL_CALL_ID.messages
