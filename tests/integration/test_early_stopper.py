"""Unit tests for early stopping utilities."""

from typing import cast

from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.tools.file_editor.definition import CommandLiteral, FileEditorAction
from openhands.tools.terminal.definition import TerminalAction
from tests.integration.early_stopper import (
    BashCommandPruner,
    CompositeEarlyStopper,
    EarlyStopResult,
    FileEditPruner,
)


def create_file_editor_event(command: CommandLiteral, path: str) -> ActionEvent:
    """Create a real ActionEvent with a FileEditorAction."""
    action = FileEditorAction(command=command, path=path)
    return ActionEvent(
        source="agent",
        thought=[TextContent(text=f"Performing {command} on {path}")],
        action=action,
        tool_name="file_editor",
        tool_call_id=f"call_{command}_{path.replace('/', '_')}",
        tool_call=MessageToolCall(
            id=f"call_{command}_{path.replace('/', '_')}",
            name="file_editor",
            arguments=f'{{"command": "{command}", "path": "{path}"}}',
            origin="completion",
        ),
        llm_response_id="test_response_id",
    )


def create_terminal_event(command: str) -> ActionEvent:
    """Create a real ActionEvent with a TerminalAction."""
    action = TerminalAction(command=command)
    return ActionEvent(
        source="agent",
        thought=[TextContent(text=f"Running command: {command}")],
        action=action,
        tool_name="terminal",
        tool_call_id=f"call_terminal_{hash(command)}",
        tool_call=MessageToolCall(
            id=f"call_terminal_{hash(command)}",
            name="terminal",
            arguments=f'{{"command": "{command}"}}',
            origin="completion",
        ),
        llm_response_id="test_response_id",
    )


class TestFileEditPruner:
    """Tests for FileEditPruner."""

    def test_no_events_returns_no_stop(self):
        """Empty events list should not trigger stop."""
        pruner = FileEditPruner()
        result = pruner.check([])
        assert result.should_stop is False
        assert result.reason is None

    def test_view_command_not_blocked(self):
        """View command should not trigger stop."""
        pruner = FileEditPruner()
        event = create_file_editor_event(command="view", path="/test.py")
        result = pruner.check(cast(list[Event], [event]))
        assert result.should_stop is False

    def test_create_command_triggers_stop(self):
        """Create command should trigger stop."""
        pruner = FileEditPruner()
        event = create_file_editor_event(command="create", path="/new_file.py")
        result = pruner.check(cast(list[Event], [event]))
        assert result.should_stop is True
        assert result.reason is not None
        assert "create" in result.reason
        assert "new_file.py" in result.reason

    def test_str_replace_triggers_stop(self):
        """str_replace command should trigger stop."""
        pruner = FileEditPruner()
        event = create_file_editor_event(command="str_replace", path="/test.py")
        result = pruner.check(cast(list[Event], [event]))
        assert result.should_stop is True
        assert result.reason is not None
        assert "str_replace" in result.reason

    def test_custom_forbidden_commands(self):
        """Custom forbidden commands should be respected."""
        # Note: 'undo_edit' is a valid FileEditorAction command
        pruner = FileEditPruner(forbidden_commands=["undo_edit"])
        event = create_file_editor_event(command="undo_edit", path="/test.py")
        result = pruner.check(cast(list[Event], [event]))
        assert result.should_stop is True

    def test_non_matching_event_not_stopped(self):
        """Non-file-editor events should not trigger stop."""
        pruner = FileEditPruner()
        # Terminal events should not trigger file edit pruner
        event = create_terminal_event(command="ls -la")
        result = pruner.check(cast(list[Event], [event]))
        assert result.should_stop is False


class TestBashCommandPruner:
    """Tests for BashCommandPruner."""

    def test_no_events_returns_no_stop(self):
        """Empty events should not trigger stop."""
        pruner = BashCommandPruner(forbidden_patterns=["rm -rf"])
        result = pruner.check([])
        assert result.should_stop is False

    def test_forbidden_pattern_triggers_stop(self):
        """Forbidden command pattern should trigger stop."""
        pruner = BashCommandPruner(forbidden_patterns=["rm -rf"])
        event = create_terminal_event(command="rm -rf /important")
        result = pruner.check(cast(list[Event], [event]))
        assert result.should_stop is True
        assert result.reason is not None
        assert "rm -rf" in result.reason

    def test_safe_command_not_stopped(self):
        """Safe commands should not trigger stop."""
        pruner = BashCommandPruner(forbidden_patterns=["rm -rf"])
        event = create_terminal_event(command="ls -la")
        result = pruner.check(cast(list[Event], [event]))
        assert result.should_stop is False


class TestCompositeEarlyStopper:
    """Tests for CompositeEarlyStopper."""

    def test_empty_stoppers_never_stops(self):
        """Empty stopper list should never stop."""
        composite = CompositeEarlyStopper(stoppers=[])
        result = composite.check([])
        assert result.should_stop is False

    def test_stops_on_first_match(self):
        """Should stop on first matching stopper."""
        # Create two pruners
        file_pruner = FileEditPruner()
        bash_pruner = BashCommandPruner(forbidden_patterns=["dangerous"])

        composite = CompositeEarlyStopper(stoppers=[file_pruner, bash_pruner])

        # Test with file edit
        event = create_file_editor_event(command="create", path="/test.py")
        result = composite.check(cast(list[Event], [event]))
        assert result.should_stop is True

    def test_no_match_continues(self):
        """Should not stop if no stopper matches."""
        file_pruner = FileEditPruner()
        composite = CompositeEarlyStopper(stoppers=[file_pruner])

        # Terminal event should not trigger file edit pruner
        event = create_terminal_event(command="ls -la")
        result = composite.check(cast(list[Event], [event]))
        assert result.should_stop is False


class TestEarlyStopResult:
    """Tests for EarlyStopResult model."""

    def test_default_values(self):
        """Test default values."""
        result = EarlyStopResult(should_stop=False)
        assert result.should_stop is False
        assert result.reason is None

    def test_with_reason(self):
        """Test with reason."""
        result = EarlyStopResult(should_stop=True, reason="Test reason")
        assert result.should_stop is True
        assert result.reason == "Test reason"


class TestLLMJudgePruner:
    """Tests for LLMJudgePruner."""

    def test_skip_when_not_enough_events(self):
        """Should not call LLM when below check threshold."""
        from tests.integration.early_stopper import LLMJudgePruner

        pruner = LLMJudgePruner(
            evaluation_criteria="Test criteria",
            user_instruction="Test instruction",
            check_every_n_events=10,
        )
        # Create a list of less than check_every_n_events events
        events: list[Event] = [create_terminal_event(f"echo {i}") for i in range(5)]
        result = pruner.check(events)
        # Should not stop because not enough events
        assert result.should_stop is False
        # LLM should not have been called
        assert pruner._llm is None

    def test_caches_result(self):
        """Should cache result between checks."""
        from tests.integration.early_stopper import LLMJudgePruner

        pruner = LLMJudgePruner(
            evaluation_criteria="Test criteria",
            user_instruction="Test instruction",
            check_every_n_events=10,
        )
        # First check - not enough events
        events: list[Event] = [create_terminal_event(f"echo {i}") for i in range(3)]
        result1 = pruner.check(events)
        assert result1.should_stop is False

        # Same number of events - should return cached
        result2 = pruner.check(events)
        assert result2.should_stop is False
        # Should be the same object (cached)
        assert pruner._last_checked_count == 0  # Never actually checked with LLM

    def test_initialization(self):
        """Test pruner initialization."""
        from tests.integration.early_stopper import LLMJudgePruner

        pruner = LLMJudgePruner(
            evaluation_criteria="Agent should not hallucinate",
            user_instruction="Fix the bug",
            check_every_n_events=5,
        )
        assert pruner.evaluation_criteria == "Agent should not hallucinate"
        assert pruner.user_instruction == "Fix the bug"
        assert pruner.check_every_n_events == 5
        assert pruner._llm is None
        assert pruner._last_checked_count == 0
