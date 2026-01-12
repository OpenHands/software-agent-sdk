"""Test that agent with size-based condenser successfully triggers condensation.

This integration test verifies that:
1. An agent can be configured with an LLMSummarizingCondenser using max_size
2. The condenser correctly counts events to measure conversation size
3. Condensation is triggered when event count limit is exceeded
"""

from openhands.sdk import get_logger
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Instruction designed to generate multiple agent messages
INSTRUCTION = """
Count from 1 to 50. For each number, use the echo command to print it along with
a short description (e.g., "1 is the first number", "2 is an even number", etc.).

DO NOT write a script to do this. Instead, interactively call the echo command
50 times, once for each number from 1 to 50.

This is intentionally inefficient to test our context management system.
"""

logger = get_logger(__name__)


class SizeCondenserTest(BaseIntegrationTest):
    """Test that agent with size-based condenser triggers condensation."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking variables."""
        self.condensations: list[Condensation] = []
        super().__init__(*args, **kwargs)

        # Some models explicitly disallow long, repetitive tool loops for cost/safety.
        # Skip this test for models that decline such requests.
        self.skip_if_model_matches(
            "gpt-5.1-codex-max",
            "This test stresses long repetitive tool loops to trigger size-based "
            "condensation. GPT-5.1 Codex Max often declines such requests for "
            "efficiency/safety reasons.",
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
        """Configure a size-based condenser with low limit to trigger condensation."""
        # Create a condenser with a low max_size to trigger condensation
        # Using max_size instead of max_tokens to test event counting
        condenser_llm = self.create_llm_copy("test-condenser-llm")
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=10,  # Low event limit to ensure condensation triggers
            max_tokens=None,  # Don't use token limit
            keep_first=1,  # Keep only initial user message
        )

    @property
    def max_iteration_per_run(self) -> int:
        return 50

    def conversation_callback(self, event):
        """Override callback to detect condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            if len(self.condensations) >= 1:
                logger.info("2nd condensation detected! Stopping test early.")
                self.conversation.pause()
            # We allow the first condensation request to test if condensation works
            self.condensations.append(event)

    def setup(self) -> None:
        logger.info(f"Size condenser test: max_size={self.condenser.max_size}")

    def verify_result(self) -> TestResult:
        """Verify that condensation was triggered based on event count."""
        if len(self.condensations) == 0:
            return TestResult(
                success=False,
                reason="Condensation not triggered. Event counting may not work.",
            )

        events_summarized = len(self.condensations[0].forgotten_event_ids)
        return TestResult(
            success=True,
            reason=f"Condensation triggered, summarizing {events_summarized} events.",
        )
