"""
API Compliance Test: Duplicate tool_call_id

Tests how different LLM APIs respond when multiple tool_result messages
have the same tool_call_id.

Pattern:
    [assistant with tool_use id=X] → [tool_result id=X] → ... → [tool_result id=X]
                                                                 ↑ Duplicate!
"""

from openhands.sdk.llm import Message
from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.patterns import A05_DUPLICATE_TOOL_CALL_ID


class DuplicateToolCallIdTest(BaseAPIComplianceTest):
    """Test API response to duplicate tool_call_id."""

    @property
    def pattern_name(self) -> str:
        return A05_DUPLICATE_TOOL_CALL_ID.name

    @property
    def pattern_description(self) -> str:
        return A05_DUPLICATE_TOOL_CALL_ID.description

    def build_malformed_messages(self) -> list[Message]:
        """Build message sequence with duplicate tool_call_id."""
        return A05_DUPLICATE_TOOL_CALL_ID.messages
