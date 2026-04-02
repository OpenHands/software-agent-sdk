"""Tests for legacy tool-name compatibility shims."""

import json
from unittest.mock import patch

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import ActionEvent, AgentErrorEvent, ObservationEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def _make_agent(*tool_names: str) -> Agent:
    llm = LLM(
        model="test-model",
        usage_id="test-llm",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    return Agent(llm=llm, tools=[Tool(name=tool_name) for tool_name in tool_names])


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
        tool_names=(TerminalTool.name,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    observation_event = next(e for e in events if isinstance(e, ObservationEvent))

    assert action_event.tool_name == TerminalTool.name
    assert action_event.tool_call.name == TerminalTool.name
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
        tool_names=(FileEditorTool.name,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == FileEditorTool.name
    assert action_event.tool_call.name == FileEditorTool.name
    assert action_event.action is not None
    assert getattr(action_event.action, "command") == "str_replace"
    assert test_file.read_text() == "value = 'new'\n"


def test_shell_tool_name_falls_back_to_terminal(tmp_path):
    events = _run_tool_call(
        tmp_path,
        tool_name="ls",
        arguments={},
        tool_names=(TerminalTool.name,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == TerminalTool.name
    assert action_event.action is not None
    assert getattr(action_event.action, "command") == "ls"


def test_grep_arguments_can_fall_back_to_terminal(tmp_path):
    test_file = tmp_path / "needle.txt"
    test_file.write_text("needle\n")

    events = _run_tool_call(
        tmp_path,
        tool_name="grep",
        arguments={"pattern": "needle", "path": str(tmp_path)},
        tool_names=(TerminalTool.name,),
    )

    action_event = next(e for e in events if isinstance(e, ActionEvent))
    observation_event = next(e for e in events if isinstance(e, ObservationEvent))
    errors = [e for e in events if isinstance(e, AgentErrorEvent)]

    assert not errors
    assert action_event.tool_name == TerminalTool.name
    assert action_event.action is not None
    assert "grep -RIn" in getattr(action_event.action, "command")
    assert "needle.txt" in observation_event.observation.text
