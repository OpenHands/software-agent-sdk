"""Test hard context reset when condensation range is invalid."""

from openhands.sdk import Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


CONTEXT_MARKER_COUNT = 8


INSTRUCTION: str = "This test defines its own instructions in run_instructions()."


class HardContextResetTest(BaseIntegrationTest):
    """Test hard context reset when condensation range is invalid.

    This test sets up a situation where an explicit condensation is requested but there
    isn't one available, which should trigger a hard context reset. Then we verify that
    we can continue the conversation normally afterward, that we can perform a normal
    condensation when sufficient events exist, and that both condensations are reflected
    correctly in the conversation state.
    """

    INSTRUCTION: str = INSTRUCTION

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation events."""
        self.condensations: list[Condensation] = []
        self.pre_hard_reset_view: list[LLMConvertibleEvent] = []
        self.post_hard_reset_view: list[LLMConvertibleEvent] = []
        self.pre_normal_condensation_view: list[LLMConvertibleEvent] = []
        self.post_normal_condensation_view: list[LLMConvertibleEvent] = []
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
            max_size=100,  # High to prevent automatic triggering
            # The initial system/user/agent view cannot make minimum progress while
            # preserving this prefix, so the first request must use a hard reset.
            # Later, deterministic context markers make a normal condensation viable.
            keep_first=4,
        )

    @property
    def max_iteration_per_run(self) -> int:
        """Limit iterations since this is a simple test."""
        return 100

    def conversation_callback(self, event):
        """Override callback to detect condensation events."""
        super().conversation_callback(event)

        if isinstance(event, Condensation):
            self.condensations.append(event)

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Test hard reset semantics and subsequent normal condensation."""
        conversation.send_message(message="Remember the hard-reset marker.")
        self.pre_hard_reset_view = list(conversation.state.view.events)
        conversation.condense()
        self.post_hard_reset_view = list(conversation.state.view.events)

        # Exercise a real agent turn after the hard reset.
        conversation.send_message(message='Echo back "hello world".')
        conversation.run()

        # Add a model-independent number of atomic events so the next explicit request
        # always has enough history for a normal condensation.
        for index in range(CONTEXT_MARKER_COUNT):
            conversation.send_message(message=f"Context marker {index}.")

        self.pre_normal_condensation_view = list(conversation.state.view.events)
        conversation.condense()
        self.post_normal_condensation_view = list(conversation.state.view.events)

        # Verify the normally condensed conversation can also continue.
        conversation.send_message(message='Echo back "hello world".')
        conversation.run()

    def verify_result(self) -> TestResult:
        """Verify both condensations by their effects on the active view."""
        if len(self.condensations) != 2:
            return TestResult(
                success=False,
                reason=f"Expected 2 condensations, got {len(self.condensations)}",
            )

        if not self.pre_hard_reset_view or not isinstance(
            self.pre_hard_reset_view[0], SystemPromptEvent
        ):
            return TestResult(
                success=False,
                reason="Hard-reset input did not start with a system prompt",
            )

        hard_reset, normal_condensation = self.condensations
        system_event = self.pre_hard_reset_view[0]
        expected_forgotten_ids = {event.id for event in self.pre_hard_reset_view[1:]}
        if hard_reset.forgotten_event_ids != expected_forgotten_ids:
            return TestResult(
                success=False,
                reason="Hard reset did not forget exactly the non-system history",
            )

        expected_hard_reset_view = hard_reset.apply(self.pre_hard_reset_view)
        if [event.id for event in self.post_hard_reset_view] != [
            event.id for event in expected_hard_reset_view
        ]:
            return TestResult(
                success=False,
                reason="Hard-reset condensation was not applied to the active view",
            )

        if [event.id for event in self.post_hard_reset_view] != [
            system_event.id,
            hard_reset.summary_event.id,
        ]:
            return TestResult(
                success=False,
                reason="Hard reset did not preserve system-first summary placement",
            )

        hard_reset_messages = LLMConvertibleEvent.events_to_messages(
            self.post_hard_reset_view
        )
        if not hard_reset_messages or hard_reset_messages[0].role != "system":
            return TestResult(
                success=False,
                reason="Hard-reset view does not produce a system-first LLM request",
            )

        hard_summary_id = hard_reset.summary_event.id
        pre_normal_ids = {event.id for event in self.pre_normal_condensation_view}
        if hard_summary_id not in pre_normal_ids:
            return TestResult(
                success=False,
                reason="Hard-reset summary was missing before normal condensation",
            )

        if not normal_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Normal condensation did not forget any history",
            )

        if {
            system_event.id,
            hard_summary_id,
        } & normal_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Normal condensation forgot protected hard-reset context",
            )

        expected_normal_view = normal_condensation.apply(
            self.pre_normal_condensation_view
        )
        if [event.id for event in self.post_normal_condensation_view] != [
            event.id for event in expected_normal_view
        ]:
            return TestResult(
                success=False,
                reason="Normal condensation was not applied to the active view",
            )

        post_normal_ids = {event.id for event in self.post_normal_condensation_view}
        if not {
            system_event.id,
            hard_summary_id,
            normal_condensation.summary_event.id,
        }.issubset(post_normal_ids):
            return TestResult(
                success=False,
                reason="Normal condensation did not preserve and extend reset context",
            )

        return TestResult(
            success=True,
            reason=(
                "Hard reset preserved the system prompt, replaced all other history, "
                "and supported a later normal condensation."
            ),
        )
