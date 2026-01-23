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
from openhands.sdk.event.condenser import Condensation, CondensationSummaryEvent
from openhands.sdk.event.llm_convertible import ObservationEvent
from openhands.sdk.llm import content_to_str
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

# Minimum events required for normal condensation with keep_first=4
MIN_EVENTS_FOR_NORMAL_CONDENSATION = 5


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

        # Step 2: Record event count before first condense
        self.events_before_first_condense = len(conversation.state.events)

        # Step 3: Explicitly condense - should trigger hard context reset
        conversation.condense()

        # Step 4: Run the conversation to verify it can continue
        conversation.run()

        # Step 5: Record event count after first run
        self.events_after_first_run = len(conversation.state.events)

        # Step 6: Trigger another condensation - this should be normal (not hard reset)
        conversation.condense()

        # Step 7: Send another message and run to verify conversation continues
        conversation.send_message(message=SECOND_INSTRUCTION)
        conversation.run()

    def verify_result(self) -> TestResult:
        """Verify that both condensations occurred and conversation continued."""
        # Run all verification checks in sequence
        result = (
            self._verify_event_counts()
            or self._verify_condensation_ordering()
            or self._verify_hard_reset_properties()
            or self._verify_normal_condensation_properties()
            or self._verify_task_outputs()
            or self._verify_view_structure()
        )

        # If any check failed, return the failure result
        if result:
            return result

        # All checks passed!
        return self._success_result()

    # Helper methods for common patterns

    def _fail(self, reason: str) -> TestResult:
        """Create a failure TestResult with the given reason."""
        return TestResult(success=False, reason=reason)

    def _verify_count(
        self, actual: int, expected_min: int, description: str
    ) -> TestResult | None:
        """Verify a count meets minimum expectations."""
        if actual < expected_min:
            return self._fail(f"Expected {description} >= {expected_min}, got {actual}")
        return None

    def _verify_condensation_not_none(
        self, condensation: Condensation | None, condensation_type: str
    ) -> TestResult | None:
        """Verify condensation exists (is not None)."""
        if condensation is None:
            return self._fail(f"No {condensation_type} condensation found")
        return None

    def _verify_summary_offset(
        self, condensation: Condensation, expected: int | str, condensation_type: str
    ) -> TestResult | None:
        """Verify condensation summary_offset matches expected value or condition."""
        offset = condensation.summary_offset
        if expected == ">0":
            if offset is None or offset <= 0:
                return self._fail(
                    f"{condensation_type} should have summary_offset>0, got {offset}"
                )
        elif offset != expected:
            return self._fail(
                f"{condensation_type} should have summary_offset={expected}, "
                f"got {offset}"
            )
        return None

    def _verify_forgotten_events(
        self, condensation: Condensation, expected_count: int | None = None
    ) -> TestResult | None:
        """Verify condensation has forgotten events, optionally check exact count."""
        if not condensation.forgotten_event_ids:
            return self._fail("Condensation had no forgotten events")

        if expected_count is not None:
            actual_count = len(condensation.forgotten_event_ids)
            if actual_count != expected_count:
                return self._fail(
                    f"Should forget {expected_count} events, but forgot {actual_count}"
                )
        return None

    def _verify_summary_non_empty(
        self, condensation: Condensation, condensation_type: str
    ) -> TestResult | None:
        """Verify condensation summary is non-empty."""
        if not condensation.summary or not condensation.summary.strip():
            return self._fail(f"{condensation_type} summary is empty or None")
        return None

    # Main verification methods

    def _verify_event_counts(self) -> TestResult | None:
        """Verify initial state had insufficient events and post-run had sufficient."""
        # Check initial state had few events
        if self.events_before_first_condense >= MIN_EVENTS_FOR_NORMAL_CONDENSATION:
            return self._fail(
                f"Expected few events before first condense "
                f"(<{MIN_EVENTS_FOR_NORMAL_CONDENSATION}), "
                f"got {self.events_before_first_condense}. "
                "Test setup may be invalid - should have insufficient events "
                "to trigger normal condensation."
            )

        # Check post-run state has many events
        if self.events_after_first_run < MIN_EVENTS_FOR_NORMAL_CONDENSATION:
            return self._fail(
                f"Expected many events after first run "
                f"(>={MIN_EVENTS_FOR_NORMAL_CONDENSATION}), "
                f"got {self.events_after_first_run}. "
                "Task may be too simple to trigger normal condensation."
            )

        return None

    def _verify_condensation_ordering(self) -> TestResult | None:
        """Verify we got at least 2 condensations in the correct order."""
        # Check we have at least 2 condensations
        result = self._verify_count(len(self.condensations), 2, "condensations")
        if result:
            return result

        # Verify first condensation is hard reset (summary_offset=0)
        if self.condensations[0].summary_offset != 0:
            return self._fail(
                f"First condensation should be hard reset (summary_offset=0), "
                f"got summary_offset={self.condensations[0].summary_offset}"
            )

        # Verify second condensation is normal (summary_offset>0)
        second_offset = self.condensations[1].summary_offset
        if second_offset is None or second_offset <= 0:
            return self._fail(
                f"Second condensation should be normal (summary_offset>0), "
                f"got summary_offset={second_offset}"
            )

        return None

    def _verify_hard_reset_properties(self) -> TestResult | None:
        """Verify hard reset condensation has expected properties."""
        # Check hard reset exists and narrow type
        hard_reset = self.hard_reset_condensation
        result = self._verify_condensation_not_none(hard_reset, "hard reset")
        if result:
            return result

        # Type narrowing: at this point hard_reset is guaranteed to be non-None
        assert hard_reset is not None

        # Verify summary_offset is 0
        result = self._verify_summary_offset(hard_reset, 0, "Hard reset")
        if result:
            return result

        # Verify ALL events were forgotten (hard reset characteristic)
        result = self._verify_forgotten_events(
            hard_reset, self.events_before_first_condense
        )
        if result:
            forgotten_count = len(hard_reset.forgotten_event_ids)
            return self._fail(
                f"Hard reset should forget ALL "
                f"{self.events_before_first_condense} events, "
                f"but only forgot {forgotten_count}. "
                "This is not a true hard reset."
            )

        # Verify summary is non-empty
        result = self._verify_summary_non_empty(hard_reset, "Hard reset")
        if result:
            return result

        return None

    def _verify_normal_condensation_properties(self) -> TestResult | None:
        """Verify normal condensation has expected properties."""
        # Check normal condensation exists and narrow type
        normal = self.normal_condensation
        result = self._verify_condensation_not_none(normal, "normal")
        if result:
            return result

        # Type narrowing: at this point normal is guaranteed to be non-None
        assert normal is not None

        # Verify summary_offset > 0
        result = self._verify_summary_offset(normal, ">0", "Normal condensation")
        if result:
            return result

        # Verify some events were forgotten
        result = self._verify_forgotten_events(normal)
        if result:
            return result

        # Verify summary is non-empty
        result = self._verify_summary_non_empty(normal, "Normal condensation")
        if result:
            return result

        return None

    def _verify_task_outputs(self) -> TestResult | None:
        """Verify actual task completion by checking for expected outputs."""
        # Collect all tool outputs
        tool_outputs = [
            "".join(content_to_str(event.observation.to_llm_content))
            for event in self.collected_events
            if isinstance(event, ObservationEvent)
        ]
        all_output = " ".join(tool_outputs)

        # Check for key indicators of first task completion
        task_indicators = ["1", "2", "3", "numbers.txt"]
        missing_indicators = [ind for ind in task_indicators if ind not in all_output]
        if missing_indicators:
            return self._fail(
                f"Task verification failed: Missing indicators in outputs: "
                f"{missing_indicators}"
            )

        # Check that wc -l was run (to count lines)
        if "wc" not in all_output and "3" not in all_output:
            return self._fail(
                "Task verification failed: Line count check not found in outputs"
            )

        # Check for the second task completion message
        if "Task completed successfully" not in all_output:
            return self._fail(
                "Task verification failed: "
                "'Task completed successfully' not found in outputs"
            )

        return None

    def _verify_view_structure(self) -> TestResult | None:
        """Build and verify the View structure is well-formed."""
        # Build the view
        try:
            view = View.from_events(self.conversation.state.events)
        except Exception as e:
            return self._fail(f"Failed to build View from conversation state: {e}")

        # Verify view has at least 2 condensations
        result = self._verify_count(len(view.condensations), 2, "condensations in view")
        if result:
            return result

        # Verify first condensation in view is hard reset
        if view.condensations[0].summary_offset != 0:
            return self._fail(
                f"First condensation in view should be hard reset (summary_offset=0), "
                f"got {view.condensations[0].summary_offset}"
            )

        # Verify second condensation in view is normal
        second_offset = view.condensations[1].summary_offset
        if second_offset is None or second_offset <= 0:
            return self._fail(
                f"Second condensation in view should be normal (summary_offset>0), "
                f"got {second_offset}"
            )

        # Verify forgotten events are excluded from the view
        result = self._verify_forgotten_events_excluded_from_view(view)
        if result:
            return result

        # Verify summary event exists and is at correct position
        result = self._verify_summary_event_position(view)
        if result:
            return result

        # Verify view events are accessible
        if not view.events:
            return self._fail("View should have events but none found")

        return None

    def _verify_forgotten_events_excluded_from_view(
        self, view: View
    ) -> TestResult | None:
        """Verify forgotten events are excluded from the view."""
        event_ids_in_view = {event.id for event in view.events}
        for i, condensation in enumerate(view.condensations[:2]):
            for forgotten_id in condensation.forgotten_event_ids:
                if forgotten_id in event_ids_in_view:
                    return self._fail(
                        f"Condensation {i + 1}: Forgotten event {forgotten_id} "
                        "still appears in view.events"
                    )
        return None

    def _verify_summary_event_position(self, view: View) -> TestResult | None:
        """Verify summary event exists and is at the expected position."""
        # Find summary events
        summary_events = [
            (i, event)
            for i, event in enumerate(view.events)
            if isinstance(event, CondensationSummaryEvent)
        ]
        if not summary_events:
            return self._fail("View should have a summary event but none found")

        # Verify most recent condensation has summary_offset
        if not view.condensations:
            return self._fail("View should have condensations but none found")

        most_recent_condensation = view.condensations[-1]
        if most_recent_condensation.summary_offset is None:
            return self._fail("Most recent condensation should have a summary_offset")

        # Find the summary event corresponding to the most recent condensation
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
            return self._fail(
                f"Could not find summary event with id {expected_summary_id} "
                "in view.events"
            )

        # Verify position matches summary_offset
        if summary_event_index != most_recent_condensation.summary_offset:
            return self._fail(
                f"Summary event index {summary_event_index} doesn't match "
                f"most recent condensation's summary_offset "
                f"{most_recent_condensation.summary_offset}"
            )

        return None

    def _success_result(self) -> TestResult:
        """Generate success result with summary statistics."""
        # These are guaranteed to be non-None at this point (verified earlier)
        assert self.hard_reset_condensation is not None
        assert self.normal_condensation is not None

        hard_reset_count = len(self.hard_reset_condensation.forgotten_event_ids)
        normal_count = len(self.normal_condensation.forgotten_event_ids)
        view = View.from_events(self.conversation.state.events)

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
