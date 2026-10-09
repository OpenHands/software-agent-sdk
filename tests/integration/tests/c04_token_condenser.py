"""Test that token-based condensation triggers on deterministic context pressure.

This integration test verifies that:
1. An agent can be configured with an LLMSummarizingCondenser using max_tokens
2. The real agent LLM's tokenizer measures the active conversation view
3. Condensation is triggered when a deterministic message exceeds the token limit
"""

from openhands.sdk import get_logger
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.types import EventID
from openhands.sdk.tool import Tool
from tests.integration.base import BaseIntegrationTest, TestResult


INSTRUCTION = "This test defines its own instructions in run_instructions()."
TOKEN_LIMIT = 5000
TOKEN_PRESSURE_REPETITIONS = 2000

logger = get_logger(__name__)


class TokenCondenserTest(BaseIntegrationTest):
    """Test that agent with token-based condenser triggers condensation."""

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking variables."""
        self.condensations: list[Condensation] = []
        self.tokens_before_run = 0
        self.pressure_event_id: EventID | None = None
        super().__init__(*args, **kwargs)

    @property
    def tools(self) -> list[Tool]:
        """Use no tools so the measured pressure comes only from conversation text."""
        return []

    @property
    def condenser(self) -> LLMSummarizingCondenser:
        """Configure a token-based condenser with low limits to trigger condensation."""
        # Create a condenser with a low token limit to trigger condensation
        # Using max_tokens instead of max_size to test token counting
        condenser_llm = self.create_llm_copy("test-condenser-llm")
        return LLMSummarizingCondenser(
            llm=condenser_llm,
            max_size=1000,  # Set high so it doesn't trigger on event count
            max_tokens=TOKEN_LIMIT,
            keep_first=1,
        )

    @property
    def max_iteration_per_run(self) -> int:
        return 10

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Create deterministic token pressure and run through the agent loop."""
        pressure_message = (
            "Retain this repeated context, then acknowledge it briefly.\n\n"
            + "token-pressure-context " * TOKEN_PRESSURE_REPETITIONS
        )
        conversation.send_message(message=pressure_message)

        active_view = conversation.state.view
        self.pressure_event_id = active_view.events[-1].id
        self.tokens_before_run = get_total_token_count(active_view.events, self.llm)
        logger.info(
            "Token condenser measured %d tokens before run (limit=%d)",
            self.tokens_before_run,
            TOKEN_LIMIT,
        )

        conversation.run()

    def conversation_callback(self, event):
        """Override callback to detect condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            if len(self.condensations) >= 1:
                logger.info("2nd condensation detected! Stopping test early.")
                self.conversation.pause()
            self.condensations.append(event)

    def setup(self) -> None:
        logger.info("Token condenser test: max_tokens=%d", TOKEN_LIMIT)

    def verify_result(self) -> TestResult:
        """Verify measured token pressure triggered the expected condensation."""
        if self.tokens_before_run <= TOKEN_LIMIT:
            return TestResult(
                success=False,
                reason=(
                    f"Deterministic context measured {self.tokens_before_run} tokens, "
                    f"which did not exceed the {TOKEN_LIMIT}-token limit."
                ),
            )

        if len(self.condensations) == 0:
            return TestResult(
                success=False,
                reason=(
                    f"Condensation not triggered for {self.tokens_before_run} tokens "
                    f"with a {TOKEN_LIMIT}-token limit."
                ),
            )

        first_condensation = self.condensations[0]
        if self.pressure_event_id not in first_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Token condensation did not summarize the oversized message.",
            )

        events_summarized = len(first_condensation.forgotten_event_ids)
        return TestResult(
            success=True,
            reason=(
                f"Condensation triggered at {self.tokens_before_run} tokens, "
                f"summarizing {events_summarized} events."
            ),
        )
