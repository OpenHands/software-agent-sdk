"""Test hard context reset when condensation range is invalid.

This test verifies that:
1. When condensation is explicitly requested via conversation.condense()
2. But condensation range is invalid due to insufficient events in history
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
# This task is designed to generate sufficient events (7-8 bash tool calls)
# to ensure a valid condensation range exists after the first run.
# With keep_first=4, we need at least 5+ events for normal condensation.
INSTRUCTION = """Perform the following tasks in order:
1. Create a temporary directory called 'test_dir'
2. Create a file called 'numbers.txt' in test_dir with the content '1'
3. Append '2' to numbers.txt
4. Append '3' to numbers.txt
5. Display the contents of numbers.txt using cat
6. Count the lines in numbers.txt using wc -l
7. Remove the test_dir directory and all its contents
Make sure to complete all 7 steps."""

# Second instruction to continue conversation after both condensations
SECOND_INSTRUCTION = """Now perform these additional tasks:
1. Echo 'Task completed successfully'
2. Print the current date using the date command"""


class HardContextResetTest(BaseIntegrationTest):
    """Test hard context reset when condensation range is invalid.

    This test validates:
    - Hard reset occurs when condensation is requested but insufficient events exist
    - All events are forgotten during hard reset (summary_offset=0)
    - Normal condensation occurs when sufficient events exist
    - Only events outside keep_first range are forgotten in normal condensation
    - Task completion is verified through actual outputs
    - Summary content is meaningful and non-empty
    """

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation events."""
        self.condensations: list[Condensation] = []
        self.hard_reset_condensation: Condensation | None = None
        self.normal_condensation: Condensation | None = None
        self.events_before_first_condense: int = 0
        self.events_after_first_run: int = 0
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
            # keep_first=4 ensures that when we have sufficient events (5+),
            # a normal condensation can occur (keeping first 4, condensing the rest).
            # With fewer events, condensation will still trigger hard reset.
            keep_first=4,
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
            # Hard reset is identified by summary_offset=0
            # This means the summary starts from the beginning of conversation history
            if event.summary_offset == 0:
                # Store only the first hard reset condensation for verification
                if self.hard_reset_condensation is None:
                    self.hard_reset_condensation = event
            else:
                # Normal condensation has summary_offset > 0
                # Store only the first normal condensation for verification
                if self.normal_condensation is None:
                    self.normal_condensation = event

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
        # Step 1: Send initial message but DON'T run yet
        conversation.send_message(message=self.instruction_message)

        # Step 2: Verify we have very few events
        # (insufficient for normal condensation)
        # At this point we have only the user message event
        # No valid condensation range exists
        # (need sufficient atomic units for keep_first=4)
        self.events_before_first_condense = len(conversation.state.events)

        # Step 3: Explicitly condense - should trigger hard context reset
        # because insufficient events exist for a valid condensation range
        conversation.condense()

        # Step 4: Now run the conversation to verify it can continue
        # after the hard context reset
        conversation.run()

        # Step 5: Verify we now have many events from the run
        # With the complex task, we should have 7-8+ bash tool calls
        # plus other events. This ensures a valid condensation range exists
        # (need 5+ for keep_first=4)
        self.events_after_first_run = len(conversation.state.events)

        # Step 6: Trigger another condensation - this should be normal (not hard reset)
        # At this point we have many events from the run, so a valid range exists
        conversation.condense()

        # Step 7: Send another message and run to verify conversation continues
        # after both the hard reset and normal condensation
        conversation.send_message(message=SECOND_INSTRUCTION)
        conversation.run()

    def verify_result(self) -> TestResult:
        """Verify that both condensations occurred and conversation continued.

        Success criteria:
        1. Initial state had few events (insufficient for normal condensation)
        2. After first run, many events exist
           (sufficient for normal condensation)
        3. Two condensation events were generated
        4. First condensation is a hard context reset
           (summary_offset=0, all events forgotten)
        5. Second condensation is normal
           (summary_offset>0, some events kept)
        6. Summaries are non-empty and meaningful
        7. The conversation completed successfully (task outputs verified)
        8. The view is well-formed with both condensations
        """
        # 1. Verify initial state had insufficient events
        if self.events_before_first_condense > 2:
            return TestResult(
                success=False,
                reason=(
                    f"Expected few events before first condense (<=2), "
                    f"got {self.events_before_first_condense}. "
                    "Test setup may be invalid."
                ),
            )

        # 2. Verify after first run we have sufficient events
        if self.events_after_first_run < 5:
            return TestResult(
                success=False,
                reason=(
                    f"Expected many events after first run (>=5), "
                    f"got {self.events_after_first_run}. "
                    "Task may be too simple to trigger normal condensation."
                ),
            )

        # 3. Verify we got exactly 2 condensations
        if len(self.condensations) != 2:
            return TestResult(
                success=False,
                reason=(
                    f"Expected exactly 2 condensations, "
                    f"got {len(self.condensations)}"
                ),
            )

        # 4. Verify first condensation is a hard reset
        if self.hard_reset_condensation is None:
            return TestResult(
                success=False,
                reason="No hard reset condensation found (summary_offset=0)",
            )

        # Verify hard reset has summary_offset=0
        if self.hard_reset_condensation.summary_offset != 0:
            return TestResult(
                success=False,
                reason=(
                    f"Hard reset should have summary_offset=0, "
                    f"got {self.hard_reset_condensation.summary_offset}"
                ),
            )

        # Verify hard reset forgot events
        if not self.hard_reset_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Hard reset condensation had no forgotten events",
            )

        # Verify hard reset summary is non-empty
        if (
            not self.hard_reset_condensation.summary
            or not self.hard_reset_condensation.summary.strip()
        ):
            return TestResult(
                success=False,
                reason="Hard reset summary is empty or None",
            )

        # 5. Verify second condensation is normal (not hard reset)
        if self.normal_condensation is None:
            return TestResult(
                success=False,
                reason="No normal condensation found (summary_offset>0)",
            )

        # Verify normal condensation has summary_offset > 0
        if (
            self.normal_condensation.summary_offset is None
            or self.normal_condensation.summary_offset <= 0
        ):
            return TestResult(
                success=False,
                reason=(
                    f"Normal condensation should have summary_offset>0, "
                    f"got {self.normal_condensation.summary_offset}"
                ),
            )

        # Verify normal condensation forgot some events
        if not self.normal_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Normal condensation had no forgotten events",
            )

        # 6. Verify normal condensation summary is non-empty
        if (
            not self.normal_condensation.summary
            or not self.normal_condensation.summary.strip()
        ):
            return TestResult(
                success=False,
                reason="Normal condensation summary is empty or None",
            )

        # 7. Verify actual task completion by checking for expected outputs
        # First task: create file, write numbers, display, count lines, cleanup
        # Second task: echo "Task completed successfully" and date
        from openhands.sdk.event.llm_convertible import ObservationEvent
        from openhands.sdk.llm import content_to_str

        tool_outputs = [
            "".join(content_to_str(event.observation.to_llm_content))
            for event in self.collected_events
            if isinstance(event, ObservationEvent)
        ]
        all_output = " ".join(tool_outputs)

        # Check for key indicators of task completion
        # For the first task, check for file operations
        if "numbers.txt" not in all_output:
            return TestResult(
                success=False,
                reason="Task verification failed: 'numbers.txt' not found in outputs",
            )

        # Check for the second task completion message
        if "Task completed successfully" not in all_output:
            return TestResult(
                success=False,
                reason=(
                    "Task verification failed: "
                    "'Task completed successfully' not found in outputs"
                ),
            )

        # 8. Verify that both condensations are in the collected events
        summary_count = sum(
            1 for event in self.collected_events if isinstance(event, Condensation)
        )

        if summary_count != 2:
            return TestResult(
                success=False,
                reason=(
                    f"Expected 2 condensations in collected events, "
                    f"found {summary_count}. View may not be well-formed."
                ),
            )

        # All checks passed!
        hard_reset_count = len(self.hard_reset_condensation.forgotten_event_ids)
        normal_count = len(self.normal_condensation.forgotten_event_ids)
        return TestResult(
            success=True,
            reason=(
                f"All verifications passed. "
                f"Events before first condense: {self.events_before_first_condense}, "
                f"events after first run: {self.events_after_first_run}. "
                f"Hard reset condensed {hard_reset_count} events, "
                f"normal condensation condensed {normal_count} events. "
                f"Both summaries are meaningful and task completed successfully."
            ),
        )
