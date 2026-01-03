"""Test condensation with thinking blocks (Issue #1575).

This integration test reproduces the scenario described in issue #1575 where
models with extended thinking/reasoning effort use thinking blocks across
multiple tool loops, then condensation is manually triggered to test if
signature verification fails.

Hypothesis (csmith49): When condensation removes intermediate messages,
signature verification may fail because signatures are computed against
concatenated thinking tokens from previous turns. This was observed with
Opus 4.5 but could affect other models with thinking capabilities.

This test verifies:
1. The model produces multiple tool loops with thinking blocks
2. Manual condensation can be triggered after the second thinking block
3. Whether the conversation continues successfully or fails with signature errors
"""

import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from openhands.sdk import get_logger
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.event import ActionEvent
from openhands.sdk.event.condenser import Condensation, CondensationRequest
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult


# Multi-step task requiring multiple tool calls with reasoning
INSTRUCTION = """
Solve this multi-step problem using bash commands. Show your reasoning for each step:

1. Calculate compound interest for $10,000 at 5% annually, compounded quarterly for 3 years
   Formula: A = P(1 + r/n)^(nt) where P=10000, r=0.05, n=4, t=3

2. Calculate simple interest for the same amount
   Formula: A = P(1 + rt) where P=10000, r=0.05, t=3

3. Calculate the difference between compound and simple interest

4. Write each intermediate result to separate files:
   - compound.txt: compound interest result
   - simple.txt: simple interest result
   - difference.txt: the difference

Use the 'bc' calculator command for each calculation. You can use echo and pipes to calculate.
For example: echo "scale=2; 10000 * 1.05" | bc
"""

logger = get_logger(__name__)


class CondensationThinkingTest(BaseIntegrationTest):
    """Test condensation with thinking blocks for issue #1575."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking variables."""
        self.tool_loop_count = 0
        self.second_thinking_detected = False
        self.condensation_count = 0
        self.condensation_request_count = 0
        self.post_condensation_errors = []
        self.thinking_block_event_ids = []  # Track event IDs with thinking blocks
        self.first_thinking_forgotten = False  # Track if first thinking block was forgotten
        super().__init__(*args, **kwargs)

        # This test requires models that support extended thinking or reasoning effort
        # to test the thinking block signature hypothesis from issue #1575
        from openhands.sdk.llm.utils.model_features import get_features

        model_name = self.llm.model
        canonical = self.llm.model_info.get("model") if self.llm.model_info else None
        name = canonical or model_name or "unknown"

        # Check if model supports extended thinking or reasoning effort
        features = get_features(name)
        supports_thinking = (
            features.supports_extended_thinking or features.supports_reasoning_effort
        )

        if not supports_thinking:
            raise SkipTest(
                f"This test requires extended thinking or reasoning effort support. "
                f"Current model '{name}' does not support these features."
            )

    @property
    def tools(self) -> list[Tool]:
        """List of tools available to the agent."""
        register_tool("TerminalTool", TerminalTool)
        return [
            Tool(name="TerminalTool"),
        ]

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        """Configure condenser with very high limits to prevent automatic condensation.

        We want ONLY manual condensation to occur, so we can precisely control
        when condensation happens (after the second thinking block).

        We set keep_first=1 to only keep the initial user message, which means
        the first thinking block will be forgotten during condensation while the
        second one is kept. This is the scenario that tests the hypothesis.
        """
        condenser_llm = self.llm.model_copy(update={"usage_id": "test-condenser-llm"})
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=10000,     # Very high - won't auto-trigger
            max_tokens=100000,  # Very high - won't auto-trigger
            keep_first=1,       # Keep only initial user message
        )

    @property
    def max_iteration_per_run(self) -> int:
        """Limit iterations to prevent runaway tests."""
        return 30

    def conversation_callback(self, event):
        """Override callback to detect thinking blocks and condensation."""
        super().conversation_callback(event)

        # Count thinking blocks in ActionEvents (tool calls with thinking)
        if isinstance(event, ActionEvent) and event.thinking_blocks:
            self.tool_loop_count += 1
            self.thinking_block_event_ids.append(event.id)
            event_id_short = event.id[:8]
            logger.info(f"Thinking block #{self.tool_loop_count} ({event_id_short}...)")

            # Mark when we've seen the second thinking block
            if self.tool_loop_count == 2:
                self.second_thinking_detected = True

        # Track condensation requests (no logging - implementation detail)
        if isinstance(event, CondensationRequest):
            self.condensation_request_count += 1

        # Track condensation events
        if isinstance(event, Condensation):
            self.condensation_count += 1
            forgotten_count = len(event.forgotten_event_ids)

            # Check if the first thinking block event was forgotten
            if self.thinking_block_event_ids:
                first_thinking_id = self.thinking_block_event_ids[0]
                if first_thinking_id in event.forgotten_event_ids:
                    self.first_thinking_forgotten = True
                    status_symbol = "✓"
                else:
                    status_symbol = "✗"

                first_id_short = first_thinking_id[:8]
                logger.info(
                    f"Condensed {forgotten_count} events | "
                    f"{status_symbol} First thinking block ({first_id_short}...) "
                    f"{'forgotten' if self.first_thinking_forgotten else 'kept'}"
                )

    def setup(self) -> None:
        """Log test configuration."""
        logger.info("Condensation + Thinking Blocks Test (Issue #1575)")
        logger.info(f"Model: {self.llm.model}")
        if hasattr(self.llm, 'reasoning_effort'):
            logger.info(f"Reasoning effort: {self.llm.reasoning_effort}")
        if hasattr(self.llm, 'extended_thinking_budget'):
            logger.info(f"Extended thinking budget: {self.llm.extended_thinking_budget}")

    def verify_result(self) -> TestResult:
        """Verify the test results and document findings."""
        logger.info(
            f"Results: {self.tool_loop_count} thinking blocks, "
            f"{self.condensation_count} condensations, "
            f"first {'forgotten' if self.first_thinking_forgotten else 'kept'}, "
            f"{len(self.post_condensation_errors)} errors"
        )

        # Check if we got the expected scenario
        if self.tool_loop_count < 2:
            return TestResult(
                success=False,
                reason=(
                    f"Expected at least 2 tool loops with thinking blocks, "
                    f"but only got {self.tool_loop_count}. The model may not be "
                    f"using extended thinking/reasoning effort."
                ),
            )

        if not self.second_thinking_detected:
            return TestResult(
                success=False,
                reason="Second thinking block was not detected as expected.",
            )

        # We expect manual condensation to have been triggered
        # Note: This test may pass even if signature verification fails,
        # because we're documenting the behavior, not requiring success
        if self.condensation_count == 0:
            return TestResult(
                success=False,
                reason=(
                    "Manual condensation was not triggered. "
                    "Check if condenser is properly configured."
                ),
            )

        # Check if the first thinking block was actually forgotten
        if not self.first_thinking_forgotten:
            return TestResult(
                success=False,
                reason=(
                    "First thinking block was not forgotten during condensation. "
                    "The test scenario is not properly set up to test the hypothesis."
                ),
            )

        # Success criteria: Test executed as planned
        # The actual outcome (signature failure or success) is documented in logs
        reason_parts = [
            f"Test executed successfully with {self.tool_loop_count} thinking blocks.",
            f"Condensation triggered after second thinking block.",
        ]

        if self.post_condensation_errors:
            reason_parts.append(
                f"⚠️  {len(self.post_condensation_errors)} errors occurred "
                f"post-condensation (possible signature validation failures)."
            )
        else:
            reason_parts.append(
                "✓ No errors detected post-condensation "
                "(signature validation may have succeeded)."
            )

        return TestResult(
            success=True,
            reason=" ".join(reason_parts),
        )

    def run_instruction(self) -> TestResult:
        """Override to add follow-up message and manual condensation.

        This sends the initial instruction, then a follow-up to trigger a second
        thinking block, then manually triggers condensation to test the hypothesis.
        """
        try:
            self.setup()

            # Initialize log file with header
            with open(self.log_file_path, "w") as f:
                f.write(f"Agent Logs for Test: {self.instance_id}\n")
                f.write("=" * 50 + "\n\n")

            # Capture stdout and stderr during conversation
            stdout_buffer = StringIO()
            stderr_buffer = StringIO()

            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                # Send the instruction and run until completion
                self.conversation.send_message(self.INSTRUCTION)
                self.conversation.run()

                # If we only got one thinking block, send a follow-up to get another
                if self.tool_loop_count < 2:
                    logger.info(f"Requesting verification (need 2nd thinking block)")
                    self.conversation.send_message(
                        "Now verify your calculations are correct by running the commands again "
                        "and comparing the results. Show your reasoning about whether the "
                        "calculations match."
                    )
                    self.conversation.run()

                # Warn if we still don't have second thinking block
                if not self.second_thinking_detected:
                    logger.warning("⚠ Second thinking block not detected")

                # Manually trigger condensation
                try:
                    self.conversation.condense()
                    logger.info("✓ Condensation completed")
                except Exception as e:
                    logger.error(f"✗ Condensation failed: {e}")
                    self.post_condensation_errors.append(str(e))

                # Send one more message to see if conversation can continue after condensation
                try:
                    self.conversation.send_message("What was the final compound interest result?")
                    self.conversation.run()
                    logger.info("✓ Post-condensation query succeeded")
                except Exception as e:
                    logger.error(f"✗ Post-condensation query failed: {e}")
                    self.post_condensation_errors.append(str(e))

            # Save captured output to log file
            captured_output = stdout_buffer.getvalue()
            captured_errors = stderr_buffer.getvalue()

            with open(self.log_file_path, "a") as f:
                if captured_output:
                    f.write("STDOUT:\n")
                    f.write(captured_output)
                    f.write("\n")
                if captured_errors:
                    f.write("STDERR:\n")
                    f.write(captured_errors)
                    f.write("\n")

            # Also print to console for debugging
            if captured_output:
                print(captured_output, end="")
            if captured_errors:
                print(captured_errors, file=sys.stderr, end="")

            return self.verify_result()

        finally:
            self.teardown()
