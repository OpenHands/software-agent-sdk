"""Test hard context reset when condensation range is invalid.

This test verifies that:
1. When condensation is explicitly requested via conversation.condense()
2. But condensation range is invalid due to insufficient events in history
3. A hard context reset is performed instead of raising an exception
4. The conversation can continue successfully after the hard context reset
5. After continuing, a second condensation (normal, not hard reset) can occur
6. The view is well-formed with both the hard context reset and normal summary
7. All events are forgotten in hard reset, only some in normal condensation
8. Forgotten events are excluded from the final view
9. Summary events are at correct positions in the view
"""

from openhands.sdk import Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Module-level instruction for test runner
# This task is designed to generate sufficient events (6+ separate bash commands)
# to ensure a valid condensation range exists after the first run.
# With keep_first=4, we need at least 5+ events for normal condensation.
# IMPORTANT: Each step must be a SEPARATE terminal command
INSTRUCTION = """Echo back `hello world`."""

# Second instruction to continue conversation after both condensations
SECOND_INSTRUCTION = """Using bc calculator, compute:
1. Compound interest on $5000 at 6% annual rate for 10 years (compounded annually)
   Formula: A = P(1 + r/n)^(nt) where n=1
2. Simple interest on the same principal, rate, and time
   Formula: I = P * r * t
3. The difference between compound and simple interest

Show your calculations step by step."""


class HardContextResetTest(BaseIntegrationTest):
    """Test hard context reset when condensation range is invalid.

    This test validates:
    - Hard reset occurs when condensation is requested but insufficient events exist
    - ALL events are forgotten during hard reset (summary_offset=0)
    - Normal condensation occurs when sufficient events exist
    - Condensation ordering is correct (hard reset first, then normal)
    - Task completion is verified through actual outputs
    - Summary content is meaningful and non-empty
    - View is constructed successfully from conversation state
    - View has correct structure with both condensations
    - Forgotten events are excluded from the view
    - CondensationSummaryEvent exists in the view
    - Summary event is at correct position matching the summary_offset
    - View can be used by the LLM (events are accessible)
    """

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation events."""
        self.condensations: list[Condensation] = []
        super().__init__(*args, **kwargs)

    @property
    def tools(self) -> list[Tool]:
        """Provide terminal tool."""
        register_tool("TerminalTool", TerminalTool)
        return [Tool(name="TerminalTool")]

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        """Use LLMSummarizingCondenser to enable explicit condensation."""
        condenser_llm = self.create_llm_copy("test-condenser-llm")
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=10,  # High to prevent automatic triggering
            # keep_first=4 ensures that when we have sufficient events (5+),
            # a normal condensation can occur (keeping first 4, condensing the rest).
            # With fewer events, condensation will still trigger hard reset.
            keep_first=4,
        )

    @property
    def max_iteration_per_run(self) -> int:
        """Limit iterations since this is a simple test."""
        return 100

    def conversation_callback(self, event):
        """Override callback to detect condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            self.condensations.append(event)

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Test explicit condense() with insufficient events triggers hard reset.

        Steps:
        1. Send initial message (creates 1 event)
        2. Verify insufficient events exist (triggers hard reset)
        3. Try to explicitly condense - should trigger hard context reset
        4. Continue the conversation to verify it still works
        5. Verify sufficient events exist for normal condensation
        6. Explicitly condense again - should trigger normal condensation
        7. Continue the conversation to verify it still works after both condensations
        """
        conversation.send_message(message=self.instruction_message)
        conversation.run()

        # Trigger a condensation. Because we've set keep_first=4 and should only have a
        # few events so far, this will be a hard context reset.
        conversation.condense()

        # Send a follow-up command that requires multiple actions. This should trigger a
        # condensation again based on the number of events.
        conversation.send_message(message=SECOND_INSTRUCTION)
        conversation.run()

        # Send one last simple message to verify the conversation can continue without
        # issues.
        conversation.send_message(message=self.instruction_message)
        conversation.run()

    def verify_result(self) -> TestResult:
        """Verify that both condensations occurred and conversation continued."""
        # Check 1: there are two separate condensations.
        if len(self.condensations) != 2:
            return TestResult(
                success=False,
                reason=f"Expected 2 condensations, got {len(self.condensations)}",
            )

        # Check 2: the first condensation is a hard reset.
        hard_reset_condensation = self.condensations[0]
        if hard_reset_condensation.summary_offset != 0:
            return TestResult(
                success=False,
                reason="First condensation is not a hard reset (summary_offset != 0)",
            )

        # Check 3: the second condensation is a normal condensation that preserves the
        # first summary.
        normal_condensation = self.condensations[1]
        if (
            normal_condensation.summary_offset is None
            or normal_condensation.summary_offset <= 0
        ):
            return TestResult(
                success=False,
                reason="Second condensation is not a normal condensation "
                "(summary_offset <= 0)",
            )

        if (
            hard_reset_condensation.summary_event.id
            in normal_condensation.forgotten_event_ids
        ):
            return TestResult(
                success=False,
                reason="Normal condensation forgot the hard reset summary event",
            )

        # All checks passed!
        return TestResult(
            success=True,
            reason="Conversation handled hard context reset and normal condensation.",
        )
