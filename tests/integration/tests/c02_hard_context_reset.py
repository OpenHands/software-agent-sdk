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
from openhands.sdk.context.view import View
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
INSTRUCTION = """Perform the following tasks. Execute EACH step as a SEPARATE
terminal command (do NOT combine them with && or ;). After each step,
verify it worked before proceeding:

1. Create a temporary directory called 'test_dir'
2. List the contents of the current directory to verify test_dir was created
3. Create a file called 'numbers.txt' in test_dir with the content '1'
4. Display the contents of numbers.txt to verify it contains '1'
5. Append '2' to numbers.txt
6. Display the contents again to verify it now has '1' and '2'
7. Append '3' to numbers.txt
8. Display the final contents to verify it has '1', '2', and '3'
9. Count the lines in numbers.txt using wc -l
10. Remove the test_dir directory and all its contents

Make sure to execute each step as a SEPARATE command and verify the
output after each step."""

# Second instruction to continue conversation after both condensations
SECOND_INSTRUCTION = """Now perform these additional tasks:
1. Echo 'Task completed successfully'
2. Print the current date using the date command"""


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
        # With the task requiring separate commands, we should have 10+ bash
        # tool calls plus other events. This ensures a valid condensation
        # range exists (need 5+ for keep_first=4)
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
        2. After first run, many events exist (sufficient for normal condensation)
        3. At least two condensation events were generated in correct order
        4. First condensation is a hard context reset
           (summary_offset=0, ALL events forgotten)
        5. Second condensation is normal (summary_offset>0, some events kept)
        6. Summaries are non-empty and meaningful
        7. The conversation completed successfully (task outputs verified)
        8. View is constructed successfully from conversation state
        9. View has correct structure with both condensations
        10. Forgotten events are excluded from the view
        11. CondensationSummaryEvent exists in the view
        12. Summary event is at correct position matching the summary_offset
        13. View events are accessible (can be used by LLM)
        """
        # 1. Verify initial state had insufficient events
        # For normal condensation with keep_first=4, we need at least 5 events
        # (keep first 4, condense at least 1). With fewer events, hard reset occurs.
        # At this point we should only have the initial user message event(s).
        MIN_EVENTS_FOR_NORMAL_CONDENSATION = 5
        if self.events_before_first_condense >= MIN_EVENTS_FOR_NORMAL_CONDENSATION:
            return TestResult(
                success=False,
                reason=(
                    f"Expected few events before first condense "
                    f"(<{MIN_EVENTS_FOR_NORMAL_CONDENSATION}), "
                    f"got {self.events_before_first_condense}. "
                    "Test setup may be invalid - should have insufficient events "
                    "to trigger normal condensation."
                ),
            )

        # 2. Verify after first run we have sufficient events
        # With keep_first=4, we need at least 5 events for normal condensation.
        # This check ensures the task generated enough events to trigger a
        # normal (non-hard-reset) condensation on the second condense() call.
        if self.events_after_first_run < MIN_EVENTS_FOR_NORMAL_CONDENSATION:
            return TestResult(
                success=False,
                reason=(
                    f"Expected many events after first run "
                    f"(>={MIN_EVENTS_FOR_NORMAL_CONDENSATION}), "
                    f"got {self.events_after_first_run}. "
                    "Task may be too simple to trigger normal condensation."
                ),
            )

        # 3. Verify we got at least 2 condensations and verify ordering
        # We expect at least 2: first should be hard reset, second should be normal.
        # Allow for more in case auto-condensation is triggered by large outputs.
        if len(self.condensations) < 2:
            return TestResult(
                success=False,
                reason=(
                    f"Expected at least 2 condensations, got {len(self.condensations)}"
                ),
            )

        # Verify ordering: first condensation should be hard reset (summary_offset=0)
        if self.condensations[0].summary_offset != 0:
            return TestResult(
                success=False,
                reason=(
                    f"First condensation should be hard reset (summary_offset=0), "
                    f"got summary_offset={self.condensations[0].summary_offset}"
                ),
            )

        # Second condensation should be normal (summary_offset>0)
        if (
            self.condensations[1].summary_offset is None
            or self.condensations[1].summary_offset <= 0
        ):
            return TestResult(
                success=False,
                reason=(
                    f"Second condensation should be normal (summary_offset>0), "
                    f"got summary_offset={self.condensations[1].summary_offset}"
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

        # Verify hard reset forgot ALL events
        # A true hard reset should forget every event in the history
        # before condensation.
        if not self.hard_reset_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Hard reset condensation had no forgotten events",
            )

        hard_reset_forgotten_count = len(
            self.hard_reset_condensation.forgotten_event_ids
        )
        if hard_reset_forgotten_count != self.events_before_first_condense:
            return TestResult(
                success=False,
                reason=(
                    f"Hard reset should forget ALL "
                    f"{self.events_before_first_condense} events, "
                    f"but only forgot {hard_reset_forgotten_count}. "
                    "This is not a true hard reset."
                ),
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
        # Check that SOME events were forgotten (basic sanity check).
        if not self.normal_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Normal condensation had no forgotten events",
            )

        # Note: We don't verify exact event IDs here because the condensation
        # algorithm may have complex logic for determining which events to keep.
        # The View verification below will ensure the final structure is correct.

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
        # For the first task, check that numbers 1, 2, 3 appeared
        task_indicators = ["1", "2", "3", "numbers.txt"]
        missing_indicators = [ind for ind in task_indicators if ind not in all_output]
        if missing_indicators:
            return TestResult(
                success=False,
                reason=(
                    f"Task verification failed: Missing indicators in outputs: "
                    f"{missing_indicators}"
                ),
            )

        # Check that wc -l was run (to count lines)
        if "wc" not in all_output and "3" not in all_output:
            return TestResult(
                success=False,
                reason=(
                    "Task verification failed: Line count check not found in outputs"
                ),
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

        # 8. Build and verify the View structure
        # This is the critical test - construct a View from the conversation
        # state and verify it's well-formed with both condensations.
        try:
            view = View.from_events(self.conversation.state.events)
        except Exception as e:
            return TestResult(
                success=False,
                reason=f"Failed to build View from conversation state: {e}",
            )

        # Verify the view has both condensations
        if len(view.condensations) < 2:
            return TestResult(
                success=False,
                reason=(
                    f"View should have at least 2 condensations, "
                    f"found {len(view.condensations)}"
                ),
            )

        # Verify first condensation in view is hard reset
        if view.condensations[0].summary_offset != 0:
            return TestResult(
                success=False,
                reason=(
                    f"First condensation in view should be hard reset "
                    f"(summary_offset=0), got {view.condensations[0].summary_offset}"
                ),
            )

        # Verify second condensation in view is normal
        if (
            view.condensations[1].summary_offset is None
            or view.condensations[1].summary_offset <= 0
        ):
            return TestResult(
                success=False,
                reason=(
                    f"Second condensation in view should be normal "
                    f"(summary_offset>0), got {view.condensations[1].summary_offset}"
                ),
            )

        # Verify forgotten events are excluded from the view
        event_ids_in_view = {event.id for event in view.events}
        for i, condensation in enumerate(view.condensations[:2]):
            for forgotten_id in condensation.forgotten_event_ids:
                if forgotten_id in event_ids_in_view:
                    return TestResult(
                        success=False,
                        reason=(
                            f"Condensation {i + 1}: Forgotten event {forgotten_id} "
                            "still appears in view.events"
                        ),
                    )

        # Verify summary event exists in the view
        from openhands.sdk.event.condenser import CondensationSummaryEvent

        summary_events = [
            (i, event)
            for i, event in enumerate(view.events)
            if isinstance(event, CondensationSummaryEvent)
        ]
        if not summary_events:
            return TestResult(
                success=False,
                reason="View should have a summary event but none found",
            )

        # Verify summary event is at the expected position
        # (should match the most recent condensation's summary_offset)
        if not view.condensations:
            return TestResult(
                success=False,
                reason="View should have condensations but none found",
            )

        most_recent_condensation = view.condensations[-1]
        if most_recent_condensation.summary_offset is None:
            return TestResult(
                success=False,
                reason="Most recent condensation should have a summary_offset",
            )

        # Find the summary event corresponding to the most recent condensation
        # The summary event ID should be "{condensation_id}-summary"
        expected_summary_id = f"{most_recent_condensation.id}-summary"
        summary_event_index = None
        for i, event in enumerate(view.events):
            if (
                isinstance(event, CondensationSummaryEvent)
                and event.id == expected_summary_id
            ):
                summary_event_index = i
                break

        if summary_event_index is None:
            return TestResult(
                success=False,
                reason=(
                    f"Could not find summary event with id {expected_summary_id} "
                    "in view.events"
                ),
            )

        if summary_event_index != most_recent_condensation.summary_offset:
            return TestResult(
                success=False,
                reason=(
                    f"Summary event index {summary_event_index} "
                    f"doesn't match most recent condensation's summary_offset "
                    f"{most_recent_condensation.summary_offset}"
                ),
            )

        # Verify view events are accessible and well-formed
        # (this ensures the view can be used by the LLM)
        if not view.events:
            return TestResult(
                success=False,
                reason="View should have events but none found",
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
                f"View is well-formed with {len(view.events)} events "
                f"and {len(view.condensations)} condensations. "
                f"Both summaries are meaningful and task completed successfully."
            ),
        )
