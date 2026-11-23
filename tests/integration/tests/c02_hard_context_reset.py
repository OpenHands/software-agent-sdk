"""Test hard context reset when condensation range is invalid."""

from openhands.sdk import Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    Condensation,
    LLMConvertibleEvent,
    SystemPromptEvent,
)
from openhands.sdk.tool import register_tool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult


# Number of deterministic context-marker events sent before the normal
# condensation. Each is a single atomic user message, so the resulting event
# count is model-independent. Large enough that a valid condensation range
# exists even if the agent chooses not to call a tool.
CONTEXT_MARKER_COUNT = 8

# Token placed in the hard-reset turn's instruction so we can assert the summary
# carries content, not just forgotten ids.
HARD_RESET_MARKER = "HARD_RESET_MARKER_7F3A"

# keep_first must be large enough that a normal condensation is possible on the
# post-hard-reset view (so the second condensation is a normal one, not another
# hard reset). See the inline note on `condenser`.
KEEP_FIRST = 4

# The test drives the conversation itself in run_instructions(); this module-level
# value just satisfies the integration runner's requirement.
INSTRUCTION: str = "This test defines its own instructions in run_instructions()."


class HardContextResetTest(BaseIntegrationTest):
    """Test hard context reset when condensation range is invalid.

    This test sets up a situation where an explicit condensation is requested but
    there isn't one available, which should trigger a hard context reset. Then we verify
    that we can continue the conversation normally afterward, that we can perform a
    normal (non-hard-reset) condensation when sufficient events exist, and that both
    condensations are reflected correctly in the conversation state.
    """

    INSTRUCTION: str = "This test defines its own instructions in run_instructions()."

    def __init__(self, *args, **kwargs):
        """Initialize test with tracking for condensation events."""
        self.condensations: list[Condensation] = []
        self.hard_reset_input: list[LLMConvertibleEvent] = []
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
            # keep_first=4 protects [system, summary, agent turn, action] so the
            # normal condensation below starts on an observation boundary,
            # guaranteeing a valid forgetting range and splitting the
            # action/observation pair from the newer history.
            #
            # It also keeps the required forgetting start (4) at or above
            # `keep_first` for the post-hard-reset view, so the second
            # condensation is a *normal* condensation. (With keep_first=5 the
            # required range start would sit below `keep_first`, raising a
            # ValueError and falling back to a hard reset.)
            keep_first=KEEP_FIRST,
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

    def _assert_finished(self) -> None:
        """Fail if the last run() ended in an error/stuck state.

        Provider errors already surface as ConversationRunError, but hitting the
        max-iteration limit only sets ERROR without raising, so check it here.
        """
        status = self.conversation.state.execution_status
        if status != ConversationExecutionStatus.FINISHED:
            raise AssertionError(f"Conversation did not finish cleanly: {status}")

    def run_instructions(self, conversation: LocalConversation) -> None:
        """Test hard reset semantics and subsequent normal condensation."""
        # Do a real agent turn first (message -> agent message) so the hard reset
        # summarizes an actual turn rather than just [system, user]. The marker
        # lets us assert the summary retains content.
        conversation.send_message(
            message=(
                "Reply with a short one-line acknowledgement. Include the exact "
                f"token {HARD_RESET_MARKER} in your reply."
            )
        )
        conversation.run()
        self._assert_finished()

        # Explicit condensation with only a short history: no valid forgetting
        # range exists past keep_first, so this must fall back to a hard reset.
        self.hard_reset_input = list(conversation.state.view.events)
        conversation.condense()
        self.post_hard_reset_view = list(conversation.state.view.events)

        # Exercise a real tool-using agent turn after the reset so the next
        # condensation has an action/observation pair to work with.
        conversation.send_message(
            message='Run `echo hello` with the terminal tool, then reply "done".'
        )
        conversation.run()
        self._assert_finished()

        # Add deterministic, model-independent marker events so the next explicit
        # request always has enough history for a normal condensation.
        for index in range(CONTEXT_MARKER_COUNT):
            conversation.send_message(message=f"Context marker {index}.")

        self.pre_normal_condensation_view = list(conversation.state.view.events)
        conversation.condense()
        self.post_normal_condensation_view = list(conversation.state.view.events)

        # Verify the normally condensed conversation can also continue.
        conversation.send_message(message='Echo back "hello world".')
        conversation.run()
        self._assert_finished()

    def verify_result(self) -> TestResult:
        """Verify both condensations by their effects on the active view."""
        if len(self.condensations) != 2:
            return TestResult(
                success=False,
                reason=f"Expected 2 condensations, got {len(self.condensations)}",
            )

        hard_reset, normal_condensation = self.condensations

        # The first condensation must be a hard reset: it forgets exactly the
        # non-system history and inserts its summary right after the system
        # prompt.
        system_index = next(
            (
                i
                for i, event in enumerate(self.hard_reset_input)
                if isinstance(event, SystemPromptEvent)
            ),
            None,
        )
        if system_index is None:
            return TestResult(
                success=False,
                reason="Hard-reset input did not contain a system prompt",
            )
        system_event = self.hard_reset_input[system_index]
        expected_forgotten = {
            event.id for event in self.hard_reset_input[system_index + 1 :]
        }
        if hard_reset.forgotten_event_ids != expected_forgotten:
            return TestResult(
                success=False,
                reason=(
                    "First condensation is not a hard reset: it did not forget "
                    "exactly the non-system history"
                ),
            )
        if hard_reset.summary_offset != system_index + 1:
            return TestResult(
                success=False,
                reason=(
                    "Hard reset did not insert the summary right after the system "
                    "prompt"
                ),
            )

        # Content check: the summary must carry the requested marker through, not
        # just drop the events.
        if not hard_reset.summary or HARD_RESET_MARKER not in hard_reset.summary:
            return TestResult(
                success=False,
                reason="Hard-reset summary did not retain the requested marker",
            )

        # The resulting view is exactly [system prompt, summary] and still opens
        # with a system message for the next LLM request.
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

        # The second condensation must be a normal one (not another hard reset).
        if (
            normal_condensation.summary_offset is None
            or normal_condensation.summary_offset <= 0
        ):
            return TestResult(
                success=False,
                reason="Second condensation is not a normal condensation "
                "(summary_offset <= 0)",
            )
        if not normal_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Normal condensation did not forget any history",
            )

        # It must protect the reset context and forget a real middle range that
        # starts after the protected prefix.
        hard_summary_id = hard_reset.summary_event.id
        pre_normal_ids = {event.id for event in self.pre_normal_condensation_view}
        if hard_summary_id not in pre_normal_ids:
            return TestResult(
                success=False,
                reason="Hard-reset summary was missing before normal condensation",
            )
        protected = {system_event.id, hard_summary_id}
        if protected & normal_condensation.forgotten_event_ids:
            return TestResult(
                success=False,
                reason="Normal condensation forgot protected hard-reset context",
            )
        if normal_condensation.summary_offset > len(self.pre_normal_condensation_view):
            return TestResult(
                success=False,
                reason="Normal condensation summary offset is outside the view",
            )

        post_normal_ids = {event.id for event in self.post_normal_condensation_view}
        if not (protected | {normal_condensation.summary_event.id}).issubset(
            post_normal_ids
        ):
            return TestResult(
                success=False,
                reason="Normal condensation did not preserve and extend reset context",
            )

        return TestResult(
            success=True,
            reason=(
                "Hard reset preserved the system prompt, summarized the prior turn "
                "with the marker intact, and supported a later normal condensation."
            ),
        )
