"""Real-model continuity across file notes, a voluntary reset, and history recall."""

from pathlib import Path
from textwrap import dedent
from uuid import uuid4

from openhands.sdk.context.condenser import AgentResetCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import (
    ActionEvent,
    Condensation,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.tool import Tool, register_tool
from openhands.sdk.tool.builtins import FinishAction
from openhands.sdk.tool.builtins.new_context import NewContextAction
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction
from tests.integration.base import TestResult
from tests.integration.utils.behavior_helpers import (
    SoftwareAgentSDKBehaviorTest,
    append_environment_tips,
)


NOTES = "retry-review-notes.md"
REPORT = "retry-review.md"
INSTRUCTION = append_environment_tips(
    dedent(
        f"""
        Review retry handling in the pinned software-agent-sdk checkout. Trace
        the LLM call path and retry policy through the implementation and its
        tests. I need a focused regression-test plan, not a code change.

        Work in two phases. First investigate the relevant modules and tests,
        and save your findings and open questions in {NOTES} at the workspace
        root using ordinary file tools. Keep the notes about the engineering
        task, without copying administrative conversation metadata.

        Then start a fresh model context before drafting the report. Keep the
        handoff short: the notes location and the remaining task are sufficient.
        Continue without waiting for me, reread your saved notes, and write
        {REPORT} at the workspace root with concrete source/test references and
        the highest-value regression cases. Do not edit repository source files
        or run installations. Finish when the report is ready.
        """
    )
)


class AgentResetHistoryBehaviorTest(SoftwareAgentSDKBehaviorTest):
    """Exercise model-selected tool calls against the shared historic checkout."""

    INSTRUCTION = INSTRUCTION

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        return [
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
            Tool(name="NewContextTool"),
            Tool(name="ConversationHistoryTool"),
        ]

    @property
    def condenser(self) -> AgentResetCondenser:
        return AgentResetCondenser()

    @property
    def max_iteration_per_run(self) -> int:
        return 30

    def run_instructions(self, conversation: LocalConversation) -> None:
        self.archive_reference = f"REVIEW-{uuid4().hex}"
        conversation.send_message(
            "Administrative metadata for this maintenance review: the archive "
            f"reference is {self.archive_reference}. It is not a source-code "
            "finding and is not needed in engineering notes or the report."
        )
        with conversation.state:
            self.metadata_event_id = conversation.state.active_branch()[-1].id
        conversation.send_message(self.instruction_message)
        conversation.run()
        self.followup_start = len(self.collected_events)
        with conversation.state:
            self.reference_still_visible = any(
                self.archive_reference in event.to_llm_message().model_dump_json()
                for event in conversation.state.view.events
            )
        notes_path = Path(self.workspace) / NOTES
        report_path = Path(self.workspace) / REPORT
        self.notes_before_followup = (
            notes_path.read_text() if notes_path.is_file() else None
        )
        self.report_before_followup = (
            report_path.read_text() if report_path.is_file() else None
        )
        if not any(
            isinstance(event, Condensation)
            and event.summary is None
            and self.metadata_event_id in event.forgotten_event_ids
            for event in self.collected_events
        ):
            return
        conversation.send_message(
            "One bookkeeping follow-up: what was the exact archive reference "
            "supplied before this review? Recover the original value from our "
            "earlier conversation and include it in your answer; do not guess."
        )
        conversation.run()

    def verify_result(self) -> TestResult:
        resets = [
            event
            for event in self.collected_events
            if isinstance(event, Condensation) and event.summary is None
        ]
        summaries = [
            event
            for event in self.collected_events
            if isinstance(event, Condensation) and event.summary is not None
        ]
        counts = f"voluntary_resets={len(resets)}, summary_rescues={len(summaries)}"

        def result(success: bool, reason: str) -> TestResult:
            return TestResult(success=success, reason=f"{reason} ({counts})")

        reset = next(
            (
                event
                for event in resets
                if self.metadata_event_id in event.forgotten_event_ids
            ),
            None,
        )
        if reset is None:
            return result(False, "The model did not reset away the earlier context.")
        if summaries:
            return result(False, "Summary rescue cannot substitute for this workflow.")

        notes = self.notes_before_followup
        report = self.report_before_followup
        if notes is None or report is None:
            return result(False, "The notes or continued-work report is missing.")
        handoffs = [
            event.action.handoff
            for event in self.collected_events[: self.followup_start]
            if isinstance(event, ActionEvent)
            and isinstance(event.action, NewContextAction)
        ]
        if self.reference_still_visible or any(
            self.archive_reference in text for text in [notes, report, *handoffs]
        ):
            return result(False, "The recall value was carried into the new context.")
        if len(report.strip()) < 200 or ".py" not in report:
            return result(False, "The continued-work report lacks source references.")

        reset_index = self.collected_events.index(reset)
        after_reset = self.collected_events[reset_index + 1 : self.followup_start]
        note_reads = {
            event.id
            for event in after_reset
            if isinstance(event, ActionEvent)
            and (
                isinstance(event.action, FileEditorAction)
                and event.action.command == "view"
                and Path(event.action.path).name == NOTES
                or isinstance(event.action, TerminalAction)
                and NOTES in event.action.command
            )
        }
        if not any(
            isinstance(event, ObservationEvent)
            and event.action_id in note_reads
            and not event.observation.is_error
            and any(
                line in event.observation.text
                for line in notes.splitlines()
                if line.strip()
            )
            for event in after_reset
        ):
            return result(False, "No successful post-reset read of the ordinary notes.")

        followup = self.collected_events[self.followup_start :]
        recovered = any(
            isinstance(event, ObservationEvent)
            and event.tool_name == "conversation_history"
            and not event.observation.is_error
            and self.archive_reference in event.observation.text
            for event in followup
        )
        answered = any(
            isinstance(event, MessageEvent)
            and event.source == "agent"
            and self.archive_reference in str(event.llm_message.content)
            or isinstance(event, ActionEvent)
            and isinstance(event.action, FinishAction)
            and self.archive_reference in event.action.message
            for event in followup
        )
        if not recovered or not answered:
            return result(
                False, "The model did not retrieve and answer the omitted value."
            )
        return result(
            True, "The model resumed from file notes and recalled omitted history."
        )
