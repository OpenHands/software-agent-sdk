"""Early stopping utilities for behavior tests.

This module provides pattern-based early stopping mechanisms to detect
test failures early and terminate execution before the full trajectory
completes, reducing LLM costs.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class EarlyStopResult(BaseModel):
    """Result from an early stopping check."""

    should_stop: bool
    reason: str | None = None


class EarlyStopperBase(ABC):
    """Base class for early stopping conditions.

    Early stoppers monitor conversation events and can trigger
    early termination when definitive failure patterns are detected.
    This saves LLM costs by avoiding running the full trajectory
    for tests that have already failed.
    """

    @abstractmethod
    def check(self, events: list[Event]) -> EarlyStopResult:
        """Check if early stopping should be triggered.

        Args:
            events: List of conversation events collected so far

        Returns:
            EarlyStopResult indicating whether to stop and why
        """
        pass


class FileEditPruner(EarlyStopperBase):
    """Stop early if file editing operations are detected.

    Useful for tests where the agent should NOT edit files,
    such as b01_no_premature_implementation.
    """

    def __init__(self, forbidden_commands: list[str] | None = None):
        """Initialize the pruner.

        Args:
            forbidden_commands: List of file editor commands to detect.
                Defaults to ["create", "str_replace", "insert", "undo_edit"]
        """
        self.forbidden_commands = forbidden_commands or [
            "create",
            "str_replace",
            "insert",
            "undo_edit",
        ]

    def check(self, events: list[Event]) -> EarlyStopResult:
        """Check if any file editing operations were performed."""
        from openhands.tools.file_editor.definition import (
            FileEditorAction,
            FileEditorTool,
        )

        for event in events:
            if (
                isinstance(event, ActionEvent)
                and event.tool_name == FileEditorTool.name
            ):
                if event.action is not None and isinstance(
                    event.action, FileEditorAction
                ):
                    if event.action.command in self.forbidden_commands:
                        return EarlyStopResult(
                            should_stop=True,
                            reason=(
                                f"Detected forbidden file operation: "
                                f"{event.action.command} on {event.action.path}"
                            ),
                        )

        return EarlyStopResult(should_stop=False)


class BashCommandPruner(EarlyStopperBase):
    """Stop early if specific bash commands are detected.

    Useful for tests that should avoid certain terminal operations.
    """

    def __init__(self, forbidden_patterns: list[str]):
        """Initialize the pruner.

        Args:
            forbidden_patterns: List of command patterns to detect.
                Uses substring matching.
        """
        self.forbidden_patterns = forbidden_patterns

    def check(self, events: list[Event]) -> EarlyStopResult:
        """Check if any forbidden bash commands were executed."""
        from openhands.tools.terminal.definition import (
            TerminalAction,
            TerminalTool,
        )

        for event in events:
            if isinstance(event, ActionEvent) and event.tool_name == TerminalTool.name:
                if event.action is not None and isinstance(
                    event.action, TerminalAction
                ):
                    command = event.action.command
                    for pattern in self.forbidden_patterns:
                        if pattern in command:
                            return EarlyStopResult(
                                should_stop=True,
                                reason=(
                                    f"Detected forbidden command pattern "
                                    f"'{pattern}' in: {command[:100]}"
                                ),
                            )

        return EarlyStopResult(should_stop=False)


class TestExecutionPruner(EarlyStopperBase):
    """Stop early if excessive test execution is detected.

    Useful for tests like b02_no_oververification where the agent
    should run only targeted tests, not broad test suites.
    """

    def __init__(
        self,
        max_test_commands: int = 3,
        broad_test_patterns: list[str] | None = None,
    ):
        """Initialize the pruner.

        Args:
            max_test_commands: Max number of pytest/test commands allowed
            broad_test_patterns: Patterns indicating overly broad test runs
        """
        self.max_test_commands = max_test_commands
        self.broad_test_patterns = broad_test_patterns or [
            "pytest tests/",
            "pytest .",
            "python -m pytest",
        ]
        self._test_command_count = 0

    def check(self, events: list[Event]) -> EarlyStopResult:
        """Check if too many test commands were executed."""
        from openhands.tools.terminal.definition import (
            TerminalAction,
            TerminalTool,
        )

        test_commands = 0
        for event in events:
            if isinstance(event, ActionEvent) and event.tool_name == TerminalTool.name:
                if event.action is not None and isinstance(
                    event.action, TerminalAction
                ):
                    command = event.action.command
                    if "pytest" in command or "python -m unittest" in command:
                        test_commands += 1

                        # Check for overly broad test patterns
                        for pattern in self.broad_test_patterns:
                            if pattern in command:
                                return EarlyStopResult(
                                    should_stop=True,
                                    reason=(
                                        f"Detected overly broad test command: "
                                        f"{command[:100]}"
                                    ),
                                )

        if test_commands > self.max_test_commands:
            return EarlyStopResult(
                should_stop=True,
                reason=(
                    f"Executed {test_commands} test commands, "
                    f"exceeding limit of {self.max_test_commands}"
                ),
            )

        return EarlyStopResult(should_stop=False)


class CompositeEarlyStopper(EarlyStopperBase):
    """Combine multiple early stoppers.

    Stops if ANY of the contained stoppers triggers.
    """

    def __init__(self, stoppers: list[EarlyStopperBase]):
        """Initialize with a list of stoppers to combine."""
        self.stoppers = stoppers

    def check(self, events: list[Event]) -> EarlyStopResult:
        """Check all contained stoppers, stop if any triggers."""
        for stopper in self.stoppers:
            result = stopper.check(events)
            if result.should_stop:
                return result

        return EarlyStopResult(should_stop=False)


class LLMJudgePruner(EarlyStopperBase):
    """Stop early based on periodic LLM judge evaluation.

    This pruner calls a lightweight LLM judge at regular intervals
    to detect subtle failures that pattern-based pruners cannot catch.
    Uses a cheap model (e.g., Haiku) with a focused prompt.

    Note: This adds LLM cost but can detect more nuanced issues.
    """

    def __init__(
        self,
        evaluation_criteria: str,
        user_instruction: str,
        check_every_n_events: int = 10,
        llm=None,
    ):
        """Initialize the LLM-based pruner.

        Args:
            evaluation_criteria: Description of what constitutes failure
            user_instruction: The original user instruction for context
            check_every_n_events: How often to invoke the LLM judge
            llm: Optional LLM instance (creates cheap judge if not provided)
        """
        self.evaluation_criteria = evaluation_criteria
        self.user_instruction = user_instruction
        self.check_every_n_events = check_every_n_events
        self._llm = llm
        self._last_checked_count = 0
        self._cached_result: EarlyStopResult | None = None

    def _get_llm(self):
        """Get or create the LLM for judging."""
        if self._llm is None:
            from tests.integration.utils.llm_judge import create_judge_llm

            self._llm = create_judge_llm()
        return self._llm

    def _get_recent_summary(self, events: list[Event], max_events: int = 20) -> str:
        """Get a summary of recent events for the judge.

        Only includes the most recent events to keep costs low.
        """
        from tests.integration.behavior_utils import get_conversation_summary

        # Only summarize recent events to reduce token usage
        recent_events = events[-max_events:] if len(events) > max_events else events
        return get_conversation_summary(recent_events, max_observation_chars=1000)

    def check(self, events: list[Event]) -> EarlyStopResult:
        """Periodically check with LLM judge if test should stop early.

        Only invokes the LLM every check_every_n_events to control costs.
        """
        event_count = len(events)

        # Skip if not enough new events since last check
        if event_count - self._last_checked_count < self.check_every_n_events:
            return self._cached_result or EarlyStopResult(should_stop=False)

        # Skip if less than minimum events needed for judgement
        if event_count < 5:
            return EarlyStopResult(should_stop=False)

        self._last_checked_count = event_count

        try:
            llm = self._get_llm()
            recent_summary = self._get_recent_summary(events)

            # Use a focused, cost-effective prompt for early stopping
            from openhands.sdk import Message, TextContent

            prompt = f"""You are evaluating if an AI agent's behavior \
indicates DEFINITE FAILURE.

USER INSTRUCTION:
{self.user_instruction}

RECENT AGENT ACTIONS:
{recent_summary}

FAILURE CRITERIA:
{self.evaluation_criteria}

IMPORTANT: Only return "STOP" if you are CONFIDENT the agent has definitively failed.
If the agent is still exploring or might succeed, return "CONTINUE".

Respond with EXACTLY one word: STOP or CONTINUE
"""

            messages = [
                Message(
                    role="user",
                    content=[TextContent(text=prompt, enable_truncation=False)],
                )
            ]
            response = llm.completion(messages=messages)

            # Parse response
            response_text = ""
            if response.message.content:
                for content in response.message.content:
                    text = getattr(content, "text", None)
                    if text is not None:
                        response_text = text.strip().upper()
                        break

            if "STOP" in response_text:
                self._cached_result = EarlyStopResult(
                    should_stop=True,
                    reason=(
                        f"LLM judge detected failure at event {event_count}: "
                        "Agent behavior indicates definite failure"
                    ),
                )

            else:
                self._cached_result = EarlyStopResult(should_stop=False)

            logger.debug(
                "LLM judge check at event %d: %s",
                event_count,
                "STOP" if self._cached_result.should_stop else "CONTINUE",
            )

        except Exception as e:
            # On error, don't stop - let the test continue
            logger.warning("LLM judge pruner error: %s", e)
            self._cached_result = EarlyStopResult(should_stop=False)

        return self._cached_result
