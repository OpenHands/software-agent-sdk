"""Unit tests for ``UnknownEvent.to_llm_message`` branching.

The three branches exist to preserve tool_call / tool-response pairing even
when a single event of a pair was degraded to ``UnknownEvent``.
"""

from typing import Any

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import Function

from openhands.sdk.event import (
    ActionEvent,
    LLMConvertibleEvent,
    ObservationEvent,
    UnknownEvent,
)
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.schema import Action, Observation


class _ActionForTest(Action):
    command: str


class _ObservationForTest(Observation):
    result: str


def _real_action(call_id: str) -> ActionEvent:
    tool_call = MessageToolCall.from_chat_tool_call(
        ChatCompletionMessageToolCall(
            id=call_id,
            type="function",
            function=Function(name="test_tool", arguments='{"command": "ls"}'),
        )
    )
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="think")],
        action=_ActionForTest(command="ls"),
        tool_name="test_tool",
        tool_call_id=call_id,
        tool_call=tool_call,
        llm_response_id="resp_1",
    )


def _real_observation(call_id: str, action_id: str) -> ObservationEvent:
    return ObservationEvent(
        source="environment",
        observation=_ObservationForTest(result="ok"),
        action_id=action_id,
        tool_name="test_tool",
        tool_call_id=call_id,
    )


def _base_kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source": "agent",
        "original_kind": "RemovedFoo",
        "original_data": {"kind": "RemovedFoo"},
        "tool_name": "test_tool",
    }
    data.update(overrides)
    return data


def test_unknown_from_observation_emits_tool_role():
    evt = UnknownEvent(**_base_kwargs(tool_call_id="call_1", action_id="act_1"))
    msg = evt.to_llm_message()
    assert msg.role == "tool"
    assert msg.tool_call_id == "call_1"
    assert msg.name == "test_tool"
    assert msg.tool_calls is None


def test_unknown_from_action_emits_assistant_with_dummy_tool_call():
    evt = UnknownEvent(**_base_kwargs(tool_call_id="call_1", llm_response_id="resp_1"))
    msg = evt.to_llm_message()
    assert msg.role == "assistant"
    assert msg.tool_calls is not None
    assert len(msg.tool_calls) == 1
    call = msg.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "test_tool"
    assert call.arguments == "{}"


def test_unknown_without_tool_ids_falls_back_to_user_role():
    evt = UnknownEvent(**_base_kwargs(tool_name=None))
    msg = evt.to_llm_message()
    assert msg.role == "user"
    assert msg.tool_calls is None
    assert msg.tool_call_id is None


def test_unknown_with_missing_tool_name_uses_placeholder():
    evt = UnknownEvent(
        **_base_kwargs(tool_name=None, tool_call_id="call_1", llm_response_id="resp_1")
    )
    msg = evt.to_llm_message()
    assert msg.tool_calls is not None
    assert msg.tool_calls[0].name == "unknown"


def test_pairing_preserved_when_action_becomes_unknown():
    """Unknown-Action + surviving Observation → valid assistant+tool sequence."""
    call = "call_1"
    unknown_action = UnknownEvent(
        **_base_kwargs(tool_call_id=call, llm_response_id="resp_1")
    )
    observation = _real_observation(call_id=call, action_id=unknown_action.id)

    messages = LLMConvertibleEvent.events_to_messages([unknown_action, observation])

    assert [m.role for m in messages] == ["assistant", "tool"]
    assert messages[0].tool_calls is not None
    assert messages[0].tool_calls[0].id == call
    assert messages[1].tool_call_id == call


def test_pairing_preserved_when_observation_becomes_unknown():
    """Surviving Action + unknown-Observation → valid assistant+tool sequence."""
    action = _real_action(call_id="call_2")
    unknown_obs = UnknownEvent(
        **_base_kwargs(tool_call_id="call_2", action_id=action.id)
    )

    messages = LLMConvertibleEvent.events_to_messages([action, unknown_obs])

    assert [m.role for m in messages] == ["assistant", "tool"]
    assert messages[0].tool_calls is not None
    assert messages[0].tool_calls[0].id == "call_2"
    assert messages[1].tool_call_id == "call_2"
