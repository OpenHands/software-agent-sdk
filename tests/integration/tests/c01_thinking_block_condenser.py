"""
Integration test for thinking block handling during condensation.

This test validates that Claude Opus's thinking blocks are properly handled
during conversation condensation, preventing malformed signature errors that
can occur when thinking blocks are included in conversation history.
"""

from openhands.sdk import LLM, Message, TextContent, Tool
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ActionEvent, Condensation
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult


# Module-level instruction for test runner
INSTRUCTION = """Using bc calculator, compute:
1. Compound interest on $5000 at 6% annual rate for 10 years (compounded annually)
   Formula: A = P(1 + r/n)^(nt) where n=1
2. Simple interest on the same principal, rate, and time
   Formula: I = P * r * t
3. The difference between compound and simple interest

Show your calculations step by step."""


class FirstToolLoopCondenser(CondenserBase):
    """
    Custom condenser that handles condensation by forgetting the first tool loop.

    This condenser is designed to test thinking block handling - it will forget
    the first atomic unit containing thinking blocks and replace it with a summary.
    """

    def handles_condensation_requests(self) -> bool:
        """Indicate that this condenser handles explicit condensation requests."""
        return True

    def condense(self, view: View, agent_llm: LLM | None = None) -> View | Condensation:
        """
        Condense by forgetting the first tool loop that contains thinking blocks.

        This validates that:
        1. We can identify atomic units with thinking blocks
        2. We can forget specific units
        3. Later thinking blocks are preserved
        """
        # Get manipulation indices which define boundaries of atomic units
        indices = view.manipulation_indices

        # Find atomic units (ranges between consecutive indices) with thinking blocks
        units_with_thinking = []
        for i in range(len(indices) - 1):
            start_idx = indices[i]
            end_idx = indices[i + 1]
            has_thinking = False
            for event in view.events[start_idx:end_idx]:
                if isinstance(event, ActionEvent) and event.thinking_blocks:
                    has_thinking = True
                    break
            if has_thinking:
                units_with_thinking.append((start_idx, end_idx, i))

        # We need at least two units with thinking blocks to test properly:
        # - One to forget (first)
        # - One to keep (second)
        if len(units_with_thinking) < 2:
            return view

        # Forget the first unit with thinking blocks
        start_idx, end_idx, _ = units_with_thinking[0]

        # Create summary for the forgotten content
        summary = (
            "Previously, I calculated compound and simple interest values "
            "using the bc calculator."
        )

        # Get event IDs to forget
        forgotten_event_ids = [event.id for event in view.events[start_idx:end_idx]]

        # Create condensation event
        return Condensation(
            forgotten_event_ids=forgotten_event_ids,
            summary=summary,
            summary_offset=start_idx,
            llm_response_id="test-condenser-response",
        )


class ThinkingBlockCondenserTest(BaseIntegrationTest):
    """
    Test that thinking blocks are properly handled during condensation.

    This test:
    1. Runs a multi-step conversation that generates thinking blocks
    2. Triggers condensation manually
    3. Verifies that:
       - Multiple thinking blocks were generated
       - Condensation occurred exactly once
       - The first thinking block was forgotten
       - Later thinking blocks were preserved
    """

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for thinking blocks and condensations."""
        self.thinking_block_count = 0
        self.condensation_count = 0
        self.condensed_thinking_blocks = False
        self.preserved_thinking_blocks = False
        super().__init__(*args, **kwargs)

    @property
    def tools(self) -> list[Tool]:
        """Provide terminal tool for bc calculator."""
        register_tool("TerminalTool", TerminalTool)
        return [Tool(name="TerminalTool")]

    @property
    def condenser(self) -> CondenserBase:
        """Use custom condenser that handles thinking blocks."""
        return FirstToolLoopCondenser()

    @property
    def max_iteration_per_run(self) -> int:
        """Allow up to 30 iterations per run."""
        return 30

    def setup(self) -> None:
        """
        Validate that the model supports extended thinking.

        Thinking blocks are primarily supported by:
        - Anthropic Claude models (extended_thinking)
        - Some Gemini models (extended_thinking)
        - Some other models (reasoning_effort)
        """
        model = self.llm_config.get("model", "")

        # Check if model has extended thinking or reasoning effort configured
        has_extended_thinking = self.llm_config.get("extended_thinking", False)
        has_reasoning_effort = "reasoning_effort" in self.llm_config

        # For Claude Opus, automatically enable extended thinking if not set
        if "opus" in model.lower() and not has_extended_thinking:
            self.llm_config["extended_thinking"] = True
            # Recreate LLM with updated config
            self.llm = self.llm.__class__(
                **{**self.llm.model_dump(), **self.llm_config}
            )
            self.agent.llm = self.llm

        # Skip test if model doesn't support thinking blocks
        if not has_extended_thinking and not has_reasoning_effort:
            raise SkipTest(
                f"Model {model} does not support extended thinking or reasoning effort"
            )

    def conversation_callback(self, event):
        """Track thinking blocks and condensation events."""
        super().conversation_callback(event)

        # Count thinking blocks before any condensation
        if isinstance(event, ActionEvent) and event.thinking_blocks:
            if self.condensation_count == 0:
                self.thinking_block_count += 1
            else:
                # Thinking blocks appearing after condensation means they were preserved
                self.preserved_thinking_blocks = True
                self.thinking_block_count += 1

        # Track condensations
        if isinstance(event, Condensation):
            self.condensation_count += 1
            # If we've seen thinking blocks before and now we're condensing,
            # we can assume some thinking blocks were condensed
            if self.thinking_block_count > 0 and event.forgotten_event_ids:
                self.condensed_thinking_blocks = True

    def run_instructions(self, conversation: LocalConversation) -> None:
        """
        Execute multi-step conversation flow.

        Steps:
        1. Initial calculation request
        2. Verification request to ensure correctness
        3. Manual condensation trigger
        4. Additional calculation with different parameters
        """
        # Step 1: Initial instruction
        conversation.send_message(message=self.instruction_message)
        conversation.run()

        # Step 2: Ask for verification (generates more thinking)
        conversation.send_message(
            message=Message(
                role="user",
                content=[
                    TextContent(
                        text=(
                            "Please verify your calculations are correct "
                            "and explain the reasoning."
                        )
                    )
                ],
            )
        )
        conversation.run()

        # Step 3: Trigger condensation manually
        conversation.send_message(
            message=Message(
                role="user",
                content=[
                    TextContent(
                        text="Now, compute the same for $10000 at 5% for 15 years."
                    )
                ],
            )
        )
        # Request condensation before running
        conversation.condense()
        conversation.run()

    def verify_result(self) -> TestResult:
        """
        Verify that thinking blocks were handled correctly during condensation.

        Success criteria:
        1. At least 3 thinking blocks generated (across multiple steps)
        2. At least 1 condensation event triggered (may be automatic or manual)
        3. Thinking blocks were condensed (forgotten) at some point
        4. Later thinking blocks were preserved (new blocks after condensation)
        """
        reasons = []

        # Check thinking block count
        if self.thinking_block_count < 3:
            reasons.append(
                f"Expected at least 3 thinking blocks, got {self.thinking_block_count}"
            )

        # Check condensation count (allow multiple condensations)
        if self.condensation_count < 1:
            reasons.append(
                f"Expected at least 1 condensation event, got {self.condensation_count}"
            )

        # Check that thinking blocks were condensed
        if not self.condensed_thinking_blocks:
            reasons.append(
                "Expected first thinking block to be forgotten during condensation"
            )

        # Check that later thinking blocks were preserved
        if not self.preserved_thinking_blocks:
            reasons.append("Expected new thinking blocks to appear after condensation")

        if reasons:
            return TestResult(
                success=False,
                reason=(
                    f"Thinking block handling validation failed: {'; '.join(reasons)}"
                ),
            )

        return TestResult(
            success=True,
            reason=(
                f"Successfully handled {self.thinking_block_count} thinking blocks "
                f"with {self.condensation_count} condensation(s)"
            ),
        )
