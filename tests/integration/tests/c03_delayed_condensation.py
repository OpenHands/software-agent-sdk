"""Test delayed condensation with soft requirements.

This test verifies that:
1. When a soft condensation requirement is triggered (via max_size)
2. But condensation cannot be performed (no valid range)
3. The system gracefully continues without raising an exception
4. Once sufficient events exist, condensation succeeds
"""

from openhands.sdk import Message, TextContent, Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Module-level instruction for test runner
INSTRUCTION = """Using the echo command, print the numbers 1 through 10.
Use exactly 10 separate echo commands, one for each number."""


class DelayedCondensationTest(BaseIntegrationTest):
    """Test that soft requirements allow delayed condensation."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation."""
        self.condensations: list[Condensation] = []
        super().__init__(*args, **kwargs)

    @property
    def tools(self) -> list[Tool]:
        """Provide terminal tool."""
        register_tool("TerminalTool", TerminalTool)
        return [Tool(name="TerminalTool")]

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        """Use LLMSummarizingCondenser with low max_size for soft requirements."""
        condenser_llm = self.create_llm_copy("test-condenser-llm")
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=6,  # Low enough to trigger even with very efficient agents
            keep_first=1,
        )

    @property
    def max_iteration_per_run(self) -> int:
        """Allow sufficient iterations."""
        return 30

    def conversation_callback(self, event):
        """Track condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            self.condensations.append(event)

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Test soft condensation requirements.

        Steps:
        1. Execute task that creates multiple tool loops
        2. Let soft condensation requirements trigger naturally
        3. Verify system continues even if condensation can't be satisfied immediately
        4. Verify condensation eventually succeeds once valid ranges exist
        """
        # Execute the main task
        conversation.send_message(message=self.instruction_message)
        conversation.run()

        # Add more messages to ensure we build up enough events
        # This creates more atomic units for potential condensation
        conversation.send_message(
            message=Message(
                role="user",
                content=[TextContent(text="Now print the numbers 11 through 15.")],
            )
        )
        conversation.run()

    def verify_result(self) -> TestResult:
        """Verify soft requirement behavior.

        Success criteria:
        1. Conversation completed successfully (didn't crash on soft requirement)
        2. At least one condensation occurred (once valid ranges existed)
        """
        if len(self.condensations) == 0:
            return TestResult(
                success=False,
                reason="Expected at least one condensation to occur during the test",
            )

        return TestResult(
            success=True,
            reason=(
                f"Soft requirements handled correctly: {len(self.condensations)} "
                "condensation(s) occurred without crashing"
            ),
        )
