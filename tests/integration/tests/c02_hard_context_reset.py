"""Test hard context reset when condensation range is invalid."""

from openhands.sdk import Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


class HardContextResetTest(BaseIntegrationTest):
    """Test hard context reset when condensation range is invalid.

    This test sets up a situation where an explicit condensation is requested but there
    isn't one available, which should trigger a hard context reset. Then we verify that
    we can continue the conversation normally afterward, that we can perform a normal
    condensation when sufficient events exist, and that both condensations are reflected
    correctly in the conversation state.
    """

    INSTRUCTION: str = "This test defines its own instructions in run_instructions()."

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
            max_size=100,  # High to prevent automatic triggering
            # keep_first=4 ensures that when we have sufficient events (5+),
            # a normal condensation can occur (keeping first 4, condensing the rest).
            # With fewer events, condensation will still trigger hard reset.
            # Validation requires: max_size // 2 - keep_first - 1 > 0
            # With max_size=100: 100 // 2 - 4 - 1 = 45 > 0 ✓
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
        """Test explicit condense() with insufficient events triggers hard reset."""
        conversation.send_message(message='Echo back "hello world".')
        conversation.run()

        # Trigger a condensation. Because we've set keep_first=4 and should only have a
        # few events so far, this will be a hard context reset.
        conversation.condense()

        # Send a follow-up command sequence to generate events. This sequence works
        # reliably in other integration tests to generate a valid condensation point.
        conversation.send_message(
            message=(
                "Using bc calculator, compute:\n"
                "1. Compound interest on $5000 at 6% annual rate for 10 years "
                "(compounded annually)\n"
                "   Formula: A = P(1 + r/n)^(nt) where n=1\n"
                "2. Simple interest on the same principal, rate, and time\n"
                "   Formula: I = P * r * t\n"
                "3. The difference between compound and simple interest\n"
                "\n"
                "Show your calculations step by step."
            )
        )
        conversation.run()

        conversation.send_message(
            message=(
                "Rerun the calculations, step by step, "
                "with a 7.5% annual rate instead of 6%."
            )
        )
        conversation.run()

        # Explicitly condense again - should trigger normal condensation now that we
        # have sufficient events.
        conversation.condense()

        # Send one last simple message to verify the conversation can continue without
        # issues.
        conversation.send_message(message='Echo back "hello world".')
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

        # Check 3: the second condensation is a normal condensation.
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

        # Check 4: the normal condensation does not forget the hard reset summary event.
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
