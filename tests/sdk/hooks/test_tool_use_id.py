"""Tests for the tool_use_id field on tool hook input (#6214)."""

import json
import uuid

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.hooks.conversation_hooks import HookEventProcessor
from openhands.sdk.hooks.manager import HookManager
from openhands.sdk.hooks.types import HookEventType
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins import ThinkAction, ThinkObservation
from openhands.sdk.workspace import LocalWorkspace
from tests.command_utils import python_command


def _stdin_to_file_command(path) -> str:
    return python_command(
        "import sys; "
        "from pathlib import Path; "
        f"Path({str(path)!r}).write_text(sys.stdin.read())"
    )


def _config(event_type: str, command: str) -> HookConfig:
    return HookConfig.from_dict(
        {
            "hooks": {
                event_type: [
                    {"matcher": "*", "hooks": [{"type": "command", "command": command}]}
                ]
            }
        }
    )


def _conversation_state(tmp_path):
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=Agent(llm=LLM(model="test-model", api_key=SecretStr("test-key"))),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )


def _action_event(tool_call_id: str) -> ActionEvent:
    return ActionEvent(
        source="agent",
        tool_name="Think",
        tool_call_id=tool_call_id,
        tool_call=MessageToolCall(
            id=tool_call_id, name="Think", arguments="{}", origin="completion"
        ),
        llm_response_id="test-response-id",
        action=ThinkAction(thought="test thought"),
        thought=[],
    )


def test_pre_and_post_tool_use_receive_same_tool_use_id(tmp_path):
    """Both hooks for one tool call see the same tool_use_id via real execution."""
    pre_log = tmp_path / "pre.json"
    post_log = tmp_path / "post.json"

    manager = HookManager(
        config=HookConfig.from_dict(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": _stdin_to_file_command(pre_log),
                                }
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": _stdin_to_file_command(post_log),
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        working_dir=str(tmp_path),
    )
    state = _conversation_state(tmp_path)
    processor = HookEventProcessor(hook_manager=manager)
    processor.set_conversation_state(state)

    action = _action_event("call-abc-123")
    state.events.append(action)
    processor.on_event(action)

    observation = ObservationEvent(
        source="agent",
        action_id=action.id,
        tool_name="Think",
        tool_call_id="call-abc-123",
        observation=ThinkObservation(),
    )
    processor.on_event(observation)

    pre_input = json.loads(pre_log.read_text())
    post_input = json.loads(post_log.read_text())
    assert pre_input["tool_use_id"] == "call-abc-123"
    assert post_input["tool_use_id"] == "call-abc-123"


def test_non_tool_hook_receives_null_tool_use_id(tmp_path):
    """UserPromptSubmit hook input carries tool_use_id as null."""
    prompt_log = tmp_path / "prompt.json"
    manager = HookManager(
        config=_config("UserPromptSubmit", _stdin_to_file_command(prompt_log)),
        working_dir=str(tmp_path),
    )
    processor = HookEventProcessor(hook_manager=manager)

    processor.on_event(
        MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="hi")]),
        )
    )

    prompt_input = json.loads(prompt_log.read_text())
    assert "tool_use_id" in prompt_input
    assert prompt_input["tool_use_id"] is None


def test_run_pre_tool_use_without_tool_use_id_keeps_field_null(tmp_path):
    """Existing callers that omit the optional argument keep working."""
    manager = HookManager(config=HookConfig(), working_dir=str(tmp_path))

    event = manager._create_event(
        HookEventType.PRE_TOOL_USE, tool_name="Think", tool_input={}
    )
    assert event.tool_use_id is None

    should_continue, results = manager.run_pre_tool_use(
        tool_name="Think", tool_input={}
    )
    assert should_continue
    assert results == []
