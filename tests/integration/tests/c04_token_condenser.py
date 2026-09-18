"""Test that agent with token-based condenser successfully triggers condensation.

This integration test verifies that:
1. An agent can be configured with an LLMSummarizingCondenser using max_tokens
2. The condenser correctly uses get_token_count to measure conversation size
3. Condensation is triggered when token limit is exceeded
"""

from openhands.sdk import Message, TextContent, get_logger
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = """I will send batches of service logs. For each batch, report the
number of ERROR entries in one sentence. Do not repeat the logs."""


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
            self.condensations.append(event)

    def run_instructions(self, conversation: LocalConversation) -> None:
        conversation.send_message(message=self.instruction_message)
        conversation.run()
        for batch in range(6):
            lines = [
                f"request={batch * 120 + index} level=INFO service=checkout "
                "operation=validate_order status=completed duration_ms=42"
                for index in range(120)
            ]
            conversation.send_message(
                message=Message(
                    role="user",
                    content=[TextContent(text="\n".join(lines))],
                )
            )
            conversation.run()
            if self.condensations:
                break

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
