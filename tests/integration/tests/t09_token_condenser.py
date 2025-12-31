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
from tests.integration.base import BaseIntegrationTest, SkipTest, TestResult


# Instruction designed to generate multiple agent messages
INSTRUCTION = """
Count from 1 to 1000. For each number, use the echo command to print it along with
a short, unique property of that number (e.g., "1 is the first natural number",
"2 is the only even prime number", etc.). Be creative with your descriptions.

DO NOT write a script to do this. Instead, interactively call the echo command
1000 times, once for each number from 1 to 1000.

This won't be efficient -- that is okay, we're using the output as a test for our
context management system.

Make sure you should generate some "extended thinking" for each tool call you make
to help us test the system.
"""

logger = get_logger(__name__)


class TokenCondenserTest(BaseIntegrationTest):
    """Test that agent with token-based condenser triggers condensation."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking variables."""
        self.condensation_count = 0
        super().__init__(*args, **kwargs)

        # Some models explicitly disallow long, repetitive tool loops for cost/safety.
        # GPT-5.1 Codex Max often refuses the 1..1000 echo prompt and may disable
        # extended tool usage. In such cases, token-count-based condensation cannot be
        # reliably exercised. Skip this test for that model to avoid spurious failures.
        model_name = self.llm.model
        canonical = self.llm.model_info.get("model") if self.llm.model_info else None
        name = (canonical or model_name or "").split("/")[-1]
        if name == "gpt-5.1-codex-max":
            raise SkipTest(
                "This test stresses long repetitive tool loops to trigger token-based "
                "condensation. GPT-5.1 Codex Max often declines such requests for "
                "efficiency/safety reasons."
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
        """Configure a token-based condenser with low limits to trigger condensation."""
        # Create a condenser with a low token limit to trigger condensation
        # Using max_tokens instead of max_size to test token counting
        condenser_llm = self.llm.model_copy(update={"usage_id": "test-condenser-llm"})
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=1000,  # Set high so it doesn't trigger on event count
            max_tokens=5000,  # Low token limit to ensure condensation triggers
            keep_first=1,  # Keep only initial user message (not tool loop start)
        )

    @property
    def max_iteration_per_run(self) -> int:
        return 50

    def conversation_callback(self, event):
        """Override callback to detect condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            if self.condensation_count >= 1:
                logger.info("2nd condensation detected! Stopping test early.")
                self.conversation.pause()
            # We allow the first condensation request to test if
            # thinking block + condensation will work together
            self.condensation_count += 1

    def setup(self) -> None:
        logger.info(f"Token condenser test: max_tokens={self.condenser.max_tokens}")

    def verify_result(self) -> TestResult:
        """Verify that condensation was triggered based on token count."""
        if self.condensation_count == 0:
            return TestResult(
                success=False,
                reason="Condensation not triggered. Token counting may not work.",
            )

        return TestResult(
            success=True,
            reason="Condensation triggered. Token counting works correctly.",
        )
