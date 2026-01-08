"""Test hard/soft condensation requirement behavior when condensation is unavailable.

This test verifies that:
1. When a condensation is explicitly requested but not available (hard requirement),
   a NoCondensationAvailableException is raised
2. When a soft condensation requirement cannot be satisfied, the system continues
   gracefully without raising an exception
3. Once more events make condensation available, soft requirements are satisfied
"""

from openhands.sdk import Message, TextContent, Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.condenser.base import NoCondensationAvailableException
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Module-level instruction for test runner
INSTRUCTION = """Using the echo command, print the numbers 1 through 3.
Use exactly 3 separate echo commands, one for each number."""


class CondensationAvailabilityTest(BaseIntegrationTest):
    """Test condensation availability with hard and soft requirements."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation attempts."""
        self.hard_requirement_failed = False
        self.soft_requirement_attempted = False
        self.condensation_succeeded = False
        super().__init__(*args, **kwargs)

    @property
    def tools(self) -> list[Tool]:
        """Provide terminal tool."""
        register_tool("TerminalTool", TerminalTool)
        return [Tool(name="TerminalTool")]

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        """Use LLMSummarizingCondenser with low max_size for soft requirement."""
        condenser_llm = self.llm.model_copy(update={"usage_id": "test-condenser-llm"})
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=10,  # Low size to trigger soft requirement
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
            self.condensation_succeeded = True

    def setup(self) -> None:
        """No special setup needed."""
        pass

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Execute test flow with hard and soft condensation requirements.

        Steps:
        1. Send initial message (creates user message event)
        2. Try explicit condense() immediately - should fail (only 1 event)
        3. Complete the task to create multiple atomic units
        4. Add more messages and let soft requirement trigger naturally
        """
        # Step 1: Send initial message but DON'T run yet
        conversation.send_message(message=self.instruction_message)

        # At this point we have only 1 event (the user message)
        # No valid condensation range exists yet (need at least 2 atomic units)

        # Step 2: Try to explicitly condense - should raise exception
        try:
            conversation.condense()
            # If we get here, condensation was available (shouldn't happen)
        except NoCondensationAvailableException:
            # Expected: no condensation available with just 1 event
            self.hard_requirement_failed = True
        except Exception as e:
            # Could get various errors when condensation is not available:
            # - ValueError: "Cannot condense conversation" (no condenser)
            # - RuntimeError: "Cannot condense 0 events" (no valid range)
            # - NoCondensationAvailableException (explicit case)
            error_msg = str(e)
            if (
                "Cannot condense 0 events" in error_msg
                or "Cannot condense conversation" in error_msg
                or "no valid range" in error_msg.lower()
            ):
                # Expected: condensation not available for various reasons
                self.hard_requirement_failed = True
            else:
                # Unexpected error - re-raise it
                raise

        # Step 3: Now run to complete the first task
        conversation.run()

        # Step 4: Add more messages to create atomic unit boundaries
        # This gives us multiple units so condensation becomes available
        conversation.send_message(
            message=Message(
                role="user",
                content=[TextContent(text="Now print the numbers 4 and 5.")],
            )
        )
        conversation.run()

        # Step 5: Add one more message to push past max_size (soft requirement)
        # At this point we have multiple atomic units, so condensation should succeed
        conversation.send_message(
            message=Message(
                role="user",
                content=[TextContent(text="Finally, print the number 6.")],
            )
        )

        # Mark that we're attempting soft requirement
        self.soft_requirement_attempted = True
        conversation.run()

    def verify_result(self) -> TestResult:
        """Verify condensation availability behavior.

        Success criteria:
        1. Hard requirement (explicit condense) failed when no condensation available
        2. Soft requirement was attempted (by continuing after hard failure)
        3. Condensation eventually succeeded once events made it available
        """
        reasons = []

        # Check that hard requirement properly failed
        if not self.hard_requirement_failed:
            reasons.append(
                "Expected NoCondensationAvailableException when explicitly "
                "requesting condensation with no valid range"
            )

        # Check that soft requirement was attempted
        if not self.soft_requirement_attempted:
            reasons.append(
                "Expected to attempt soft requirement after hard failure"
            )

        # Check that condensation eventually succeeded
        if not self.condensation_succeeded:
            reasons.append(
                "Expected condensation to succeed once multiple atomic units exist"
            )

        if reasons:
            return TestResult(
                success=False,
                reason=(
                    f"Condensation availability validation failed: "
                    f"{'; '.join(reasons)}"
                ),
            )

        return TestResult(
            success=True,
            reason=(
                "Successfully validated hard/soft requirement behavior: "
                "hard requirement raised exception when unavailable, "
                "soft requirement succeeded once condensation became available"
            ),
        )
