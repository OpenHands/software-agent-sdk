"""Test hard condensation requirement behavior when condensation is unavailable.

This test verifies that:
1. When condensation is explicitly requested via conversation.condense()
2. But no valid condensation range exists (only 1 event in history)
3. A NoCondensationAvailableException is raised
"""

from openhands.sdk import Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.condenser.base import NoCondensationAvailableException
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Module-level instruction for test runner
INSTRUCTION = """Using the echo command, print the numbers 1 through 3.
Use exactly 3 separate echo commands, one for each number."""


class HardCondensationRequirementTest(BaseIntegrationTest):
    """Test that hard requirements raise exception when condensation unavailable."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for exception."""
        self.exception_raised = False
        self.exception_type = None
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

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Test explicit condense() with insufficient events.

        Steps:
        1. Send initial message (creates 1 event)
        2. Try to explicitly condense - should raise NoCondensationAvailableException
        3. Verify exception was raised
        4. End test (don't call run() to avoid processing leftover CondensationRequest)
        """
        # Step 1: Send initial message but DON'T run yet
        conversation.send_message(message=self.instruction_message)

        # At this point we have only 1 event (the user message)
        # No valid condensation range exists (need at least 2 atomic units)

        # Step 2: Try to explicitly condense - should raise exception
        try:
            conversation.condense()
            # If we get here, condensation was available (shouldn't happen)
        except NoCondensationAvailableException as e:
            # Expected: no condensation available with just 1 event
            self.exception_raised = True
            self.exception_type = type(e).__name__
        except Exception as e:
            # Could get various errors when condensation is not available
            error_msg = str(e)
            if (
                "Cannot condense 0 events" in error_msg
                or "Cannot condense conversation" in error_msg
                or "no valid range" in error_msg.lower()
            ):
                # Expected: condensation not available
                self.exception_raised = True
                self.exception_type = type(e).__name__
            else:
                # Unexpected error - re-raise it
                raise

        # Don't call conversation.run() here to avoid the CondensationRequest
        # being processed again as a hard requirement

    def verify_result(self) -> TestResult:
        """Verify that exception was raised when condensation unavailable.

        Success criteria:
        1. An exception was raised when explicitly requesting condensation
        """
        if not self.exception_raised:
            return TestResult(
                success=False,
                reason=(
                    "Expected exception when explicitly requesting condensation "
                    "with no valid range"
                ),
            )

        return TestResult(
            success=True,
            reason=(
                f"Hard requirement correctly raised {self.exception_type} when "
                "condensation unavailable"
            ),
        )
