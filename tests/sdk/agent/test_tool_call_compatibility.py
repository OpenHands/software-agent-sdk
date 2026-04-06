"""Tests for legacy tool-name compatibility shims."""

import json
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.event import ActionEvent, AgentErrorEvent, ObservationEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


FILE_EDITOR_TOOL_NAME = "file_editor"
FILE_EDITOR_TOOL_SPEC = "FileEditorCompatTool"
TERMINAL_TOOL_NAME = "terminal"
TERMINAL_TOOL_SPEC = "TerminalCompatTool"


class _TerminalAction(Action):
    command: str


class _TerminalObservation(Observation):
    pass


class _TerminalExecutor(ToolExecutor[_TerminalAction, _TerminalObservation]):
    def __call__(
        self,
        action: _TerminalAction,
        conversation: LocalConversation | None = None,
    ) -> _TerminalObservation:
        working_dir = conversation.workspace.working_dir if conversation else None
        completed = subprocess.run(
            shlex.split(action.command),
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return _TerminalObservation.from_text(completed.stdout or completed.stderr)


class _TerminalTool(ToolDefinition[_TerminalAction, _TerminalObservation]):
    name = TERMINAL_TOOL_NAME

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="Execute shell commands",
                action_type=_TerminalAction,
                observation_type=_TerminalObservation,
                executor=_TerminalExecutor(),
            )
        ]


class _FileEditorAction(Action):
    command: str
    path: str
    old_str: str | None = None
    new_str: str | None = None
    file_text: str | None = None
    insert_line: int | None = None
    view_range: list[int] | None = None


class _FileEditorObservation(Observation):
    pass


class _FileEditorExecutor(ToolExecutor[_FileEditorAction, _FileEditorObservation]):
    def __call__(
        self,
        action: _FileEditorAction,
        conversation: LocalConversation | None = None,
    ) -> _FileEditorObservation:
        path = Path(action.path)
        if action.command == "str_replace":
            if action.old_str is None:
                raise ValueError("old_str is required for str_replace")
            updated = path.read_text().replace(action.old_str, action.new_str or "", 1)
            path.write_text(updated)
            return _FileEditorObservation.from_text("replaced")
        if action.command == "view":
            return _FileEditorObservation.from_text(path.read_text())
        raise ValueError(f"Unsupported file_editor command: {action.command}")


class _FileEditorTool(ToolDefinition[_FileEditorAction, _FileEditorObservation]):
    name = FILE_EDITOR_TOOL_NAME

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="Edit files",
                action_type=_FileEditorAction,
                observation_type=_FileEditorObservation,
                executor=_FileEditorExecutor(),
            )
        ]


register_tool(TERMINAL_TOOL_SPEC, _TerminalTool)
register_tool(FILE_EDITOR_TOOL_SPEC, _FileEditorTool)


def _make_agent(*tool_specs: str) -> Agent:
    llm = LLM(
        model="test-model",
        usage_id="test-llm",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    return Agent(llm=llm, tools=[Tool(name=tool_spec) for tool_spec in tool_specs])


def _model_response(tool_name: str, arguments: dict[str, object]) -> ModelResponse:
    return ModelResponse(
        id="mock-response-1",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="Using a tool.",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call_1",
                            type="function",
                            function=Function(
                                name=tool_name,
                                arguments=json.dumps(arguments),
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def _run_tool_call(
    tmp_path,
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_names: tuple[str, ...],
) -> list[object]:
    agent = _make_agent(*tool_names)
    conversation = Conversation(agent=agent, workspace=str(tmp_path))
    events: list[object] = []

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        return_value=_model_response(tool_name, arguments),
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="Please help.")])
        )
        agent.step(conversation, on_event=events.append)

    return events


def test_bash_alias_executes_terminal_tool(tmp_path):
    events = _run_tool_call(
        tmp_path,
        tool_name="bash",
        arguments={"command": "printf hello"},
        tool_names=(TERMINAL_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    observation_event = next(e for e in events if isinstance(e, ObservationEvent))

    assert action_event.tool_name == TERMINAL_TOOL_NAME
    assert action_event.tool_call.name == TERMINAL_TOOL_NAME
    assert action_event.action is not None
    assert getattr(action_event.action, "command") == "printf hello"
    assert "hello" in observation_event.observation.text


def test_str_replace_alias_infers_file_editor_command(tmp_path):
    test_file = tmp_path / "sample.py"
    test_file.write_text("value = 'old'\n")

    events = _run_tool_call(
        tmp_path,
        tool_name="str_replace",
        arguments={
            "path": str(test_file),
            "old_str": "'old'",
            "new_str": "'new'",
        },
        tool_names=(FILE_EDITOR_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == FILE_EDITOR_TOOL_NAME
    assert action_event.tool_call.name == FILE_EDITOR_TOOL_NAME
    assert action_event.action is not None
    assert getattr(action_event.action, "command") == "str_replace"
    assert test_file.read_text() == "value = 'new'\n"


def test_shell_tool_name_falls_back_to_terminal(tmp_path):
    events = _run_tool_call(
        tmp_path,
        tool_name="ls",
        arguments={},
        tool_names=(TERMINAL_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == TERMINAL_TOOL_NAME
    assert action_event.action is not None
    assert getattr(action_event.action, "command") == "ls"


@pytest.mark.parametrize("tool_name", ["cat /etc/passwd", "ls; echo pwned"])
def test_shell_tool_name_requires_exact_command_name(tmp_path, tool_name):
    events = _run_tool_call(
        tmp_path,
        tool_name=tool_name,
        arguments={},
        tool_names=(TERMINAL_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    observations = [e for e in events if isinstance(e, ObservationEvent)]

    assert not observations
    assert action_event.tool_name == tool_name
    assert action_event.action is None
    assert errors
    assert errors[0].tool_name == tool_name


def test_grep_without_pattern_does_not_fall_back_to_terminal(tmp_path):
    events = _run_tool_call(
        tmp_path,
        tool_name="grep",
        arguments={"path": str(tmp_path)},
        tool_names=(TERMINAL_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    observations = [e for e in events if isinstance(e, ObservationEvent)]

    assert not observations
    assert action_event.tool_name == "grep"
    assert action_event.action is None
    assert errors
    assert errors[0].tool_name == "grep"


def test_shell_tool_name_does_not_fall_back_without_terminal(tmp_path):
    events = _run_tool_call(
        tmp_path,
        tool_name="ls",
        arguments={},
        tool_names=(FILE_EDITOR_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    observations = [e for e in events if isinstance(e, ObservationEvent)]

    assert not observations
    assert action_event.tool_name == "ls"
    assert action_event.action is None
    assert errors
    assert errors[0].tool_name == "ls"


def test_grep_arguments_can_fall_back_to_terminal(tmp_path):
    test_file = tmp_path / "needle.txt"
    test_file.write_text("needle\n")

    events = _run_tool_call(
        tmp_path,
        tool_name="grep",
        arguments={"pattern": "needle", "path": str(tmp_path)},
        tool_names=(TERMINAL_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    observation_event = next(e for e in events if isinstance(e, ObservationEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == TERMINAL_TOOL_NAME
    assert action_event.action is not None
    assert "grep -RIn" in getattr(action_event.action, "command")
    assert "needle.txt" in observation_event.observation.text


def test_security_risk_typo_normalized(tmp_path):
    """Test that security_risk typos are normalized before validation."""
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello\n")

    events = _run_tool_call(
        tmp_path,
        tool_name="bash",
        arguments={"command": "cat hello.txt", "security_rort": "LOW"},
        tool_names=(TERMINAL_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    observation_event = next(e for e in events if isinstance(e, ObservationEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == TERMINAL_TOOL_NAME
    assert action_event.action is not None
    assert "hello" in observation_event.observation.text


def test_file_editor_command_inferred_from_old_str(tmp_path):
    """Test that file_editor command is inferred when old_str is present."""
    test_file = tmp_path / "sample.py"
    test_file.write_text("value = 'old'\n")

    events = _run_tool_call(
        tmp_path,
        tool_name="str_replace_editor",
        arguments={
            "path": str(test_file),
            "old_str": "'old'",
            "new_str": "'new'",
        },
        tool_names=(FILE_EDITOR_TOOL_SPEC,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == FILE_EDITOR_TOOL_NAME
    assert action_event.action is not None
    assert getattr(action_event.action, "command") == "str_replace"
    assert test_file.read_text() == "value = 'new'\n"


def test_file_editor_empty_args_emits_error(tmp_path):
    """Test that file_editor with empty args produces helpful error."""
    events = _run_tool_call(
        tmp_path,
        tool_name="file_editor",
        arguments={},
        tool_names=(FILE_EDITOR_TOOL_SPEC,),
    )

    errors = [e for e in events if isinstance(e, AgentErrorEvent)]
    observations = [e for e in events if isinstance(e, ObservationEvent)]

    assert not observations
    assert len(errors) == 1
    error_event = errors[0]
    assert "file_editor" in error_event.error
    assert "Cannot infer" in error_event.error or "command" in error_event.error.lower()
    # Should NOT be the raw Pydantic validation error
    assert "Field required" not in error_event.error
    assert "validation errors" not in error_event.error
