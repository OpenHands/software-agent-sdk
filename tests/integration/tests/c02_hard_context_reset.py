"""Test hard context reset when condensation is unavailable.

This test verifies that:
1. When condensation is explicitly requested via conversation.condense()
2. But no valid condensation range exists (only 1 event in history)
3. A hard context reset is performed instead of raising an exception
4. The conversation can continue successfully after the hard context reset
5. After continuing, a second condensation (normal, not hard reset) can occur
6. The view is well-formed with both the hard context reset and normal summary

REVIEW: The module docstring is inaccurate. "Condensation is unavailable" is
misleading - condensation IS available, it's just that there's an insufficient
number of events to perform a normal condensation. Better wording: "when
condensation range is invalid due to insufficient events in history".
"""

from openhands.sdk import Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Module-level instruction for test runner
# REVIEW: These instructions are too simple and don't guarantee enough events
# will be generated for a "normal" condensation. With max_iteration_per_run=10
# and keep_first=4, the agent might complete this in 3-4 events total, which
# would still trigger a hard reset on the second condense() call. Consider
# making the task more complex to ensure sufficient event generation.
INSTRUCTION = """Using the echo command, print the numbers 1 through 3.
Use exactly 3 separate echo commands, one for each number."""

# Second instruction to continue conversation after hard reset
SECOND_INSTRUCTION = """Now using the echo command, print the numbers 4 through 6.
Use exactly 3 separate echo commands, one for each number."""


class HardContextResetTest(BaseIntegrationTest):
    """Test hard context reset when condensation is unavailable.

    REVIEW: OVERALL TEST QUALITY ASSESSMENT
    ========================================
    This test has several fundamental issues that compromise its reliability:

    1. **Unverified Assumptions**: The test assumes specific event counts at
       critical points but never validates them. This makes the test fragile
       and potentially invalid if the underlying implementation changes.

    2. **Weak Verification**: The test doesn't actually verify what it claims:
       - Claims to test task completion (numbers 1-6) but never checks output
       - Claims to test "normal" vs "hard" condensation but uses weak heuristics
       - Doesn't verify summary content is meaningful (could be empty)

    3. **Flaky Test Design**: The simple task may not generate enough events
       for a guaranteed normal condensation, making the test potentially flaky.

    4. **Missing Edge Cases**: Doesn't test:
       - What happens if hard reset fails
       - Whether the summary actually helps in context
       - Order preservation of events after condensation
       - Multiple consecutive hard resets

    5. **Poor Separation of Concerns**: The test conflates testing the hard
       reset mechanism with testing conversation continuation. These should
       be separate tests.

    Recommendation: Rewrite with explicit assertions at each step, verify
    actual task outputs, and split into multiple focused tests.
    """

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation events.

        REVIEW: What this test ACTUALLY tests vs what it CLAIMS to test:

        ACTUALLY TESTS:
        - That calling condense() twice doesn't raise an exception
        - That two Condensation events are created with summary_offset==0 and >0
        - That the conversation doesn't crash after condensation

        CLAIMS TO TEST (but doesn't):
        - That hard reset occurs "when condensation is unavailable"
          (never verifies why it occurred or the state that triggered it)
        - That the conversation "continues successfully"
          (never checks if the task was actually completed)
        - That the view is "well-formed"
          (only checks count, not structure or content quality)
        - That hard reset vs normal condensation work differently
          (only checks one numeric field, not actual behavior difference)

        The gap between claims and reality is significant.
        """
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
            # REVIEW: This comment is misleading. keep_first=4 doesn't prevent
            # a valid condensation range - it's the lack of events that does.
            # If there are only 1-2 events total, even keep_first=1 would
            # prevent condensation. This comment should clarify that
            # keep_first=4 ensures that IF we have more events later, a normal
            # condensation can occur (for the second condense call).
            keep_first=4,  # Set higher than normal
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
            # REVIEW: This detection logic is fragile. summary_offset==0 might
            # not be the definitive marker for hard reset. Should verify against
            # actual implementation or use a more explicit marker. Also, this
            # logic assumes condensations arrive in order and doesn't handle
            # multiple hard resets correctly (would overwrite the first).
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

        # REVIEW: CRITICAL - This test doesn't verify its core assumption!
        # The comment claims "we have only 1 event" but this is never checked.
        # If send_message creates more than 1 event, the entire test premise
        # is invalid. Should add: assert len(conversation.get_events()) == 1
        # At this point we have only 1 event (the user message)
        # No valid condensation range exists (need at least 2 atomic units)

        # Step 2: Explicitly condense - should trigger hard context reset
        conversation.condense()

        # Step 3: Now run the conversation to verify it can continue
        # after the hard context reset
        conversation.run()

        # Step 4: Trigger another condensation - this should be normal (not hard reset)
        # REVIEW: This assumption is not guaranteed! The comment says "we have many
        # events from the run, so a valid range exists" but:
        # 1. "Many events" is vague and unverified
        # 2. With keep_first=4, you need at least 5+ events for valid range
        # 3. If the agent completes the task in very few iterations, this could
        #    still trigger another hard reset, making the test flaky
        # Should verify event count here or make task more complex to guarantee
        # sufficient events are generated.
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

        REVIEW: Criterion #4 claims to verify "numbers 1-6 printed" but this
        verification never actually checks that! The test should search through
        collected_events or agent outputs to verify the task was completed.
        As written, this test could pass even if the agent completely failed
        the actual task, as long as condensations occurred.
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
        # REVIEW: This check is too weak. Simply having forgotten_event_ids
        # doesn't verify this was actually a "hard reset" that condensed ALL
        # events. Should verify that:
        # 1. summary_offset == 0 (already checked earlier, redundantly)
        # 2. The number of forgotten events equals the total events at that time
        # 3. The summary actually contains meaningful content (not empty)
        if not self.hard_reset_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Hard reset condensation had no forgotten events",
            )

        # Check that the normal condensation also condensed some events
        # REVIEW: Same issue here - doesn't verify this is actually "normal"
        # (i.e., summary_offset > 0 and some events kept). This could pass
        # even if the second condensation was also a hard reset.
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
        # REVIEW: This is a false sense of success. "Got here without exceptions"
        # doesn't mean the conversation did what it was supposed to do. The test
        # should verify:
        # 1. The actual task outputs (numbers 1-6 were printed)
        # 2. The summaries contain the right information
        # 3. The hard reset actually cleared ALL prior context
        # 4. The normal condensation kept some events (summary_offset > 0)
        # Currently this test could pass even if the agent completely failed
        # the task and both condensations were broken, as long as no exception
        # was raised.
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
