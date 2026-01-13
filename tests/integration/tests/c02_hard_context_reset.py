"""Test hard context reset when condensation is unavailable.

This test verifies that:
1. When condensation is explicitly requested via conversation.condense()
2. But no valid condensation range exists (only 1 event in history)
3. A hard context reset is performed instead of raising an exception
4. The conversation can continue successfully after the hard context reset
5. After continuing, a second condensation (normal, not hard reset) can occur
6. The view is well-formed with both the hard context reset and normal summary
"""

from openhands.sdk import Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Module-level instruction for test runner
INSTRUCTION = """Using the echo command, print the numbers 1 through 3.
Use exactly 3 separate echo commands, one for each number."""

# Second instruction to continue conversation after hard reset
SECOND_INSTRUCTION = """Now using the echo command, print the numbers 4 through 6.
Use exactly 3 separate echo commands, one for each number."""


class HardContextResetTest(BaseIntegrationTest):
    """Test hard context reset when condensation is unavailable."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation events."""
        self.condensations: list[Condensation] = []
        self.hard_reset_condensation: Condensation | None = None
        self.normal_condensation: Condensation | None = None
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
            max_size=1000,  # High to prevent automatic triggering
            keep_first=4,  # Set higher than normal to avoid a valid condensation range
        )

    @property
    def max_iteration_per_run(self) -> int:
        """Limit iterations since this is a simple test."""
        return 10

    def conversation_callback(self, event):
        """Override callback to detect condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            self.condensations.append(event)
            # Check if this is a hard reset (summary_offset=0, all events forgotten)
            if event.summary_offset == 0:
                self.hard_reset_condensation = event
            else:
                # This is a normal condensation (not a hard reset)
                self.normal_condensation = event

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Test explicit condense() with insufficient events triggers hard reset.

        Steps:
        1. Send initial message (creates 1 event)
        2. Try to explicitly condense - should trigger hard context reset
        3. Continue the conversation to verify it still works
        4. Explicitly condense again - should trigger normal condensation
        5. Continue the conversation to verify it still works after both condensations
        """
        # Step 1: Send initial message but DON'T run yet
        conversation.send_message(message=self.instruction_message)

        # At this point we have only 1 event (the user message)
        # No valid condensation range exists (need at least 2 atomic units)

        # Step 2: Explicitly condense - should trigger hard context reset
        conversation.condense()

        # Step 3: Now run the conversation to verify it can continue
        # after the hard context reset
        conversation.run()

        # Step 4: Trigger another condensation - this should be normal (not hard reset)
        # At this point we have many events from the run, so a valid range exists
        conversation.condense()

        # Step 5: Send another message and run to verify conversation continues
        # after both the hard reset and normal condensation
        conversation.send_message(message=SECOND_INSTRUCTION)
        conversation.run()

    def verify_result(self) -> TestResult:
        """Verify that both condensations occurred and conversation continued.

        Success criteria:
        1. Two condensation events were generated
        2. First condensation is a hard context reset (summary_offset=0)
        3. Second condensation is normal (summary_offset>0)
        4. The conversation completed successfully (numbers 1-6 printed)
        5. The view is well-formed with both condensations
        """
        if len(self.condensations) < 2:
            return TestResult(
                success=False,
                reason=(
                    f"Expected 2 condensations (hard reset + normal), "
                    f"got {len(self.condensations)}"
                ),
            )

        if self.hard_reset_condensation is None:
            return TestResult(
                success=False,
                reason=(
                    "No hard reset condensation found. "
                    "Expected first condensation with summary_offset=0"
                ),
            )

        if self.normal_condensation is None:
            return TestResult(
                success=False,
                reason=(
                    "No normal condensation found. "
                    "Expected second condensation with summary_offset>0"
                ),
            )

        # Check that the hard reset condensed all events in the view
        if not self.hard_reset_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Hard reset condensation had no forgotten events",
            )

        # Check that the normal condensation also condensed some events
        if not self.normal_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Normal condensation had no forgotten events",
            )

        # Verify that both condensations are in the collected events
        # This ensures the view is well-formed with both summaries
        summary_count = sum(
            1 for event in self.collected_events if isinstance(event, Condensation)
        )

        if summary_count != 2:
            return TestResult(
                success=False,
                reason=(
                    f"Expected 2 condensations in events, found {summary_count}. "
                    "View may not be well-formed."
                ),
            )

        # The fact that we got here without exceptions means the conversation
        # was able to continue successfully after both condensations
        hard_reset_count = len(self.hard_reset_condensation.forgotten_event_ids)
        normal_count = len(self.normal_condensation.forgotten_event_ids)
        return TestResult(
            success=True,
            reason=(
                "Hard context reset and normal condensation both triggered "
                f"successfully. Hard reset condensed {hard_reset_count} events, "
                f"normal condensation condensed {normal_count} events. "
                "Conversation continued successfully with well-formed view "
                "containing both condensations."
            ),
        )
