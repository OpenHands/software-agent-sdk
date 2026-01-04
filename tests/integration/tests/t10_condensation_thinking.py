"""Test condensation with thinking blocks.

Verifies that conversation continues successfully after condensation when
thinking blocks are forgotten. Tests that signature verification works correctly
when earlier thinking blocks are condensed away while later ones remain.
"""

from itertools import chain

from openhands.sdk import get_logger
from openhands.sdk.context.condenser import CondenserBase
from openhands.sdk.context.view import View
from openhands.sdk.event import ActionEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.model_features import get_features
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult


# Multi-step task requiring multiple tool calls with reasoning
INSTRUCTION = """
Solve this multi-step problem using bash commands. Show your reasoning for each step:

1. Calculate compound interest for $10,000 at 5% annually, compounded quarterly for 3
years:
   A = P(1 + r/n)^(nt) where P=10000, r=0.05, n=4, t=3

2. Calculate simple interest for the same amount:
   A = P(1 + rt) where P=10000, r=0.05, t=3

3. Calculate the difference between compound and simple interest

4. Write each intermediate result to separate files:
   - compound.txt: compound interest result
   - simple.txt: simple interest result
   - difference.txt: the difference

Use the 'bc' calculator command for each calculation. You can use echo and pipes to
calculate.

For example: echo "scale=2; 10000 * 1.05" | bc
"""

logger = get_logger(__name__)


class FirstToolLoopCondenser(CondenserBase):
    """Custom condenser that forgets the first tool loop on request.

    This condenser handles CondensationRequest events and forgets the first
    tool loop (identified via manipulation_indices), replacing it with a static summary.
    """

    def handles_condensation_requests(self) -> bool:
        """This condenser handles explicit condensation requests."""
        return True

    def condense(self, view: View, agent_llm: LLM | None = None) -> View | Condensation:
        """Condense by forgetting the first tool loop with a static summary.

        Args:
            view: The current view of the conversation
            agent_llm: The LLM instance (unused but required by interface)

        Returns:
            Condensation event that forgets the first tool loop
        """
        # Only condense if there's an unhandled condensation request
        if not view.unhandled_condensation_request:
            return view

        # Get manipulation indices to identify tool loop boundaries
        indices = view.manipulation_indices

        # We need at least 2 atomic units with thinking blocks to condense the first one
        if len(indices) < 3:
            return view

        # Find the first atomic unit that contains thinking blocks
        first_thinking_block_unit_idx = None
        for i in range(len(indices) - 1):
            start_idx = indices[i]
            end_idx = indices[i + 1]

            # Check if this atomic unit has any events with thinking blocks
            for event in view.events[start_idx:end_idx]:
                if isinstance(event, ActionEvent) and event.thinking_blocks:
                    first_thinking_block_unit_idx = i
                    break

            if first_thinking_block_unit_idx is not None:
                break

        if first_thinking_block_unit_idx is None:
            # No thinking blocks found, can't condense
            return view

        # Find the second atomic unit with thinking blocks
        second_thinking_block_unit_idx = None
        for i in range(first_thinking_block_unit_idx + 1, len(indices) - 1):
            start_idx = indices[i]
            end_idx = indices[i + 1]

            # Check if this atomic unit has any events with thinking blocks
            for event in view.events[start_idx:end_idx]:
                if isinstance(event, ActionEvent) and event.thinking_blocks:
                    second_thinking_block_unit_idx = i
                    break

            if second_thinking_block_unit_idx is not None:
                break

        if second_thinking_block_unit_idx is None:
            # Only one tool loop with thinking blocks, can't condense safely
            return view

        # Forget everything from the start up to and including the first tool loop
        # This is from indices[0] to indices[first_thinking_block_unit_idx + 1]
        first_loop_end = indices[first_thinking_block_unit_idx + 1]

        # Collect event IDs to forget (everything up to and including first tool loop)
        forgotten_event_ids = [
            event.id for event in view.events[:first_loop_end]
        ]

        # Create a static summary
        summary = (
            "Previous calculations completed: compound interest formula applied "
            "to calculate A = P(1 + r/n)^(nt), simple interest calculated, "
            "and results written to files."
        )

        # Return condensation event
        # Get the llm_response_id from the last event before condensation
        # (we need this for the Condensation event)
        last_event = view.events[-1]
        if isinstance(last_event, ActionEvent):
            llm_response_id = last_event.llm_response_id
        else:
            # Find the most recent ActionEvent
            llm_response_id = None
            for event in reversed(view.events):
                if isinstance(event, ActionEvent):
                    llm_response_id = event.llm_response_id
                    break
            if llm_response_id is None:
                # Fallback: just return the view if we can't find an llm_response_id
                return view

        return Condensation(
            forgotten_event_ids=forgotten_event_ids,
            summary=summary,
            summary_offset=0,  # Insert summary at the beginning
            llm_response_id=llm_response_id,
        )


class CondensationThinkingTest(BaseIntegrationTest):
    """Test condensation with thinking blocks."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        self.thinking_block_events = []
        self.condensations = []
        
        super().__init__(*args, **kwargs)

        # Grab the model name so we can check features
        model_name = self.llm.model
        canonical = self.llm.model_info.get("model") if self.llm.model_info else None
        name = canonical or model_name or "unknown"
        
        features = get_features(name)

        # This test only works for models that generate thinking blocks, so skip
        # otherwise
        supports_thinking = (
            features.supports_extended_thinking or features.supports_reasoning_effort
        )
        if not supports_thinking:
            raise SkipTest(
                f"Model '{name}' does not support extended thinking or reasoning effort"
            )

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        return [Tool(name="TerminalTool")]

    @property
    def condenser(self) -> FirstToolLoopCondenser:
        """Configure custom condenser that forgets first tool loop on request.

        This condenser handles CondensationRequest events and replaces the first
        tool loop with a static summary, ensuring thinking blocks from that loop
        are forgotten.
        """
        return FirstToolLoopCondenser()

    @property
    def max_iteration_per_run(self) -> int:
        return 30

    def conversation_callback(self, event):
        super().conversation_callback(event)

        if isinstance(event, ActionEvent) and event.thinking_blocks:
            self.thinking_block_events.append(event)
            
        if isinstance(event, Condensation):
            self.condensations.append(event)

    def setup(self) -> None:
        pass

    def verify_result(self) -> TestResult:
        # Sanity checks to ensure the flow worked the way we expected: at least three
        # thinking blocks and one condensation
        if len(self.thinking_block_events) < 3:
            return TestResult(
                success=False,
                reason=(
                    f"Expected at least 3 thinking blocks, got "
                    f"{len(self.thinking_block_events)}"
                ),
            )
        
        if len(self.condensations) != 1:
            return TestResult(
                success=False,
                reason=(
                    f"Expected exactly 1 condensation event, got "
                    f"{len(self.condensations)}"
                ),
            )

        # Check the conditions of the actual test. The condensation should forget the
        # first thinking block but keep the second.
        forgotten_event_ids = list(chain.from_iterable(
            c.forgotten_event_ids for c in self.condensations
        ))

        if self.thinking_block_events[0].id not in forgotten_event_ids:
            return TestResult(
                success=False,
                reason="First thinking block was not forgotten during condensation",
            )
        
        if self.thinking_block_events[1].id in forgotten_event_ids:
            return TestResult(
                success=False,
                reason=(
                    "Second thinking block was incorrectly forgotten during "
                    "condensation"
                ),
            )

        # In all other cases, if the test was successfully run without errors then it's
        # a pass
        return TestResult(
            success=True,
            reason=(
                f"Condensation successful with {len(self.thinking_block_events)} "
                "thinking blocks"
            ),
        )

    def execute_conversation(self) -> None:
        """Custom conversation flow with follow-up messages and manual condensation."""
        # Send the instruction and run until completion
        self.conversation.send_message(self.INSTRUCTION)
        self.conversation.run()

        # The agent should resolve the task with a single tool loop and just one
        # thinking block

        self.conversation.send_message(
            "Now verify your calculations are correct by re-running the commands "
            "and comparing the results. Show your reasoning about whether the "
            "calculations match."
        )
        self.conversation.run()

        # Manually trigger condensation
        self.conversation.condense()
        self.conversation.run()
        
        # Send one more message to see if conversation can continue
        self.conversation.send_message(
            "Now run the commands again with 10% interest rate compounded annually."
            " Show your reasoning and explain which method will yield a larger return."
        )
        self.conversation.run()