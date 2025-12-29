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
