"""Test that agent with token-based condenser successfully triggers condensation.

This integration test verifies that:
1. An agent can be configured with an LLMSummarizingCondenser using max_tokens
2. The condenser correctly uses get_token_count to measure conversation size
3. Condensation is triggered when token limit is exceeded
"""

from openhands.sdk import get_logger
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Large tool results cross the token limit without a long, model-dependent loop.
INSTRUCTION = """
Run these two commands using the terminal tool, in separate calls, in order:

1. python -c "print('token pressure context ' * 1000)"
2. python -c "print('second context payload ' * 1000)"

Do not redirect, truncate, or combine their output. The large output is intentional:
we are testing conversation condensation. After both calls, reply "done".
Make sure you generate some "extended thinking" for each tool call to help test
thinking blocks together with condensation.
"""

logger = get_logger(__name__)


class TokenCondenserTest(BaseIntegrationTest):
    """Test that agent with token-based condenser triggers condensation."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking variables."""
        self.condensations: list[Condensation] = []
        super().__init__(*args, **kwargs)

    @property
    def tools(self) -> list[Tool]:
        """List of tools available to the agent."""
        register_tool("TerminalTool", TerminalTool)
        return [
            Tool(name="TerminalTool"),
        ]

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        """Configure a token-based condenser with low limits to trigger condensation."""
        # Create a condenser with a low token limit to trigger condensation
        # Using max_tokens instead of max_size to test token counting
        condenser_llm = self.create_llm_copy("test-condenser-llm")
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=1000,  # Set high so it doesn't trigger on event count
            max_tokens=5000,  # Low token limit to ensure condensation triggers
            keep_first=1,  # Keep only initial user message (not tool loop start)
        )

    @property
    def max_iteration_per_run(self) -> int:
        return 10

    def conversation_callback(self, event):
        """Override callback to detect condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            if len(self.condensations) >= 1:
                logger.info("2nd condensation detected! Stopping test early.")
                self.conversation.pause()
            # We allow the first condensation request to test if
            # thinking block + condensation will work together
            self.condensations.append(event)

    def setup(self) -> None:
        logger.info(f"Token condenser test: max_tokens={self.condenser.max_tokens}")

    def verify_result(self) -> TestResult:
        """Verify that condensation was triggered based on token count."""
        if len(self.condensations) == 0:
            return TestResult(
                success=False,
                reason="Condensation not triggered. Token counting may not work.",
            )

        events_summarized = len(self.condensations[0].forgotten_event_ids)
        return TestResult(
            success=True,
            reason=f"Condensation triggered, summarizing {events_summarized} events.",
        )
