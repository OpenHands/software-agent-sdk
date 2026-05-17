"""Tests for tool call argument parsing and empty-response recovery.

Covers two fixes for the Qwen3.5-Flash stuck conversation issue:

1. JSON argument parsing: raw json.loads first, sanitize_json_control_chars
   as fallback (fixes literal \\n whitespace being incorrectly escaped).

2. Corrective feedback: when the LLM produces no tool call and no content,
   inject a user message so the model can self-correct instead of silently
   looping into the monologue stuck detector.
"""

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self
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
from openhands.sdk.event import ActionEvent, AgentErrorEvent, MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


# ── minimal tool ─────────────────────────────────────────────────────────


class _ViewAction(Action):
    command: str
    path: str
    view_range: list[int] | None = None


class _ViewObs(Observation):
    output: str

    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text=self.output)]


class _ViewExec(ToolExecutor[_ViewAction, _ViewObs]):
    def __call__(self, action: _ViewAction, conversation=None) -> _ViewObs:
        return _ViewObs(output=f"viewed {action.path}")


class _ViewTool(ToolDefinition[_ViewAction, _ViewObs]):
    name = "view_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="View a file",
                action_type=_ViewAction,
                observation_type=_ViewObs,
                executor=_ViewExec(),
            )
        ]


register_tool("ViewTool", _ViewTool)


# ── helpers ──────────────────────────────────────────────────────────────


def _make_agent(*, with_tool: bool = True) -> Agent:
    llm = LLM(
        model="test-model",
        usage_id="test-llm",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    tools = [Tool(name="ViewTool")] if with_tool else []
    return Agent(llm=llm, tools=tools)


def _model_response(
    content: str | None,
    tool_calls: list[ChatCompletionMessageToolCall] | None = None,
    *,
    response_id: str = "resp-1",
    reasoning_content: str | None = None,
) -> ModelResponse:
    msg = LiteLLMMessage(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
    )
    if reasoning_content is not None:
        msg.reasoning_content = reasoning_content  # type: ignore[attr-defined]
    return ModelResponse(
        id=response_id,
        choices=[Choices(index=0, message=msg, finish_reason="stop")],
        created=0,
        model="test-model",
        object="chat.completion",
    )


# ── Fix 1: JSON argument parsing ────────────────────────────────────────


def test_newline_whitespace_in_arguments_parses_ok():
    """Arguments with raw newlines as JSON whitespace should parse directly.

    Qwen3.5-Flash emits arguments like:
        "view_range": \\n[1, 100]\\n\\n
    After API JSON decoding the \\n become 0x0A — valid JSON whitespace.
    """
    args_with_newlines = (
        '{"command": "view", "path": "/workspace/test.py", '
        '"view_range": \n[1, 100]\n\n}'
    )
    assert json.loads(args_with_newlines) is not None  # sanity

    agent = _make_agent()
    conv = Conversation(agent=agent)

    resp = _model_response(
        content="Viewing file",
        tool_calls=[
            ChatCompletionMessageToolCall(
                id="call_1",
                type="function",
                function=Function(
                    name="view_tool",
                    arguments=args_with_newlines,
                ),
            )
        ],
    )

    events: list[object] = []
    with patch("openhands.sdk.llm.llm.litellm_completion", return_value=resp):
        conv.send_message(
            Message(
                role="user",
                content=[TextContent(text="View file.")],
            )
        )
        agent.step(conv, on_event=events.append)

    action_events = [e for e in events if isinstance(e, ActionEvent)]
    error_events = [e for e in events if isinstance(e, AgentErrorEvent)]
    assert len(action_events) >= 1, (
        f"Expected ActionEvent, got errors: {[e.error for e in error_events]}"
    )
    assert action_events[0].action is not None
    assert isinstance(action_events[0].action, _ViewAction)


def test_control_chars_in_string_values_still_sanitized():
    """Raw 0x0A inside a JSON string value triggers fallback sanitization."""
    args_raw = '{"command": "view", "path": "/workspace/test\n.py"}'
    # This is invalid JSON (raw newline inside string)
    try:
        json.loads(args_raw)
        # If this doesn't raise, the test premise is wrong
        assert False, "Expected json.loads to fail"
    except json.JSONDecodeError:
        pass

    agent = _make_agent()
    conv = Conversation(agent=agent)

    resp = _model_response(
        content="Viewing file",
        tool_calls=[
            ChatCompletionMessageToolCall(
                id="call_2",
                type="function",
                function=Function(
                    name="view_tool",
                    arguments=args_raw,
                ),
            )
        ],
    )

    events: list[object] = []
    with patch("openhands.sdk.llm.llm.litellm_completion", return_value=resp):
        conv.send_message(
            Message(
                role="user",
                content=[TextContent(text="View file.")],
            )
        )
        agent.step(conv, on_event=events.append)

    # After sanitization fallback the action is still created
    action_events = [e for e in events if isinstance(e, ActionEvent)]
    assert len(action_events) >= 1
    assert action_events[0].action is not None


# ── Fix 2: Corrective feedback on empty response ────────────────────────


def test_reasoning_only_response_injects_nudge():
    """When LLM returns reasoning but no tool call / content, inject nudge."""
    agent = _make_agent(with_tool=False)
    conv = Conversation(agent=agent)

    resp = _model_response(
        content="",
        reasoning_content="Let me think about this...",
    )

    events: list[object] = []
    with patch("openhands.sdk.llm.llm.litellm_completion", return_value=resp):
        conv.send_message(
            Message(
                role="user",
                content=[TextContent(text="Fix the bug.")],
            )
        )
        agent.step(conv, on_event=events.append)

    agent_msgs = [
        e for e in events if isinstance(e, MessageEvent) and e.source == "agent"
    ]
    user_nudges = [
        e for e in events if isinstance(e, MessageEvent) and e.source == "user"
    ]
    assert len(agent_msgs) == 1
    assert len(user_nudges) == 1
    nudge_text = user_nudges[0].llm_message.content[0]
    assert isinstance(nudge_text, TextContent)
    assert "function call" in nudge_text.text


def test_content_response_does_not_inject_nudge():
    """When LLM produces meaningful content, no nudge should be injected."""
    agent = _make_agent(with_tool=False)
    conv = Conversation(agent=agent)

    resp = _model_response(content="Here is my analysis of the bug...")

    events: list[object] = []
    with patch("openhands.sdk.llm.llm.litellm_completion", return_value=resp):
        conv.send_message(
            Message(
                role="user",
                content=[TextContent(text="Fix the bug.")],
            )
        )
        agent.step(conv, on_event=events.append)

    user_nudges = [
        e for e in events if isinstance(e, MessageEvent) and e.source == "user"
    ]
    assert len(user_nudges) == 0


def test_completely_empty_response_injects_nudge():
    """Completely empty responses (no reasoning, no content) get a nudge."""
    agent = _make_agent(with_tool=False)
    conv = Conversation(agent=agent)

    resp = _model_response(content="")

    events: list[object] = []
    with patch("openhands.sdk.llm.llm.litellm_completion", return_value=resp):
        conv.send_message(
            Message(
                role="user",
                content=[TextContent(text="Fix the bug.")],
            )
        )
        agent.step(conv, on_event=events.append)

    user_nudges = [
        e for e in events if isinstance(e, MessageEvent) and e.source == "user"
    ]
    assert len(user_nudges) == 1
