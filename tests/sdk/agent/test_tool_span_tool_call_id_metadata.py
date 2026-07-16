"""Regression test: a tool execution's TOOL span must identify its events.

Before this fix, `_execute_action_event`'s `observe(name=tool_name,
span_type="TOOL")` call did not forward the persisted `ActionEvent.id`, so the
span could not be resolved to the action/observation pair in the event log.
"""

from collections.abc import Sequence
from pathlib import Path
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
from openhands.sdk.event import ActionEvent, Event, ObservationEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class _EchoAction(Action):
    value: str = ""


class _EchoObservation(Observation):
    result: str = ""


class _EchoExecutor(ToolExecutor[_EchoAction, _EchoObservation]):
    def __call__(self, action: _EchoAction, conversation=None) -> _EchoObservation:
        return _EchoObservation(result=action.value)


class _EchoTool(ToolDefinition[_EchoAction, _EchoObservation]):
    name = "echo_tool"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="Echoes its input",
                action_type=_EchoAction,
                observation_type=_EchoObservation,
                executor=_EchoExecutor(),
            )
        ]


register_tool("EchoTool", _EchoTool)


def _mock_response_with_tool_call(call_id: str) -> ModelResponse:
    return ModelResponse(
        id="mock-response-1",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="using the echo tool",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id=call_id,
                            type="function",
                            function=Function(
                                name="echo_tool",
                                arguments='{"value": "hi"}',
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


def test_tool_span_metadata_carries_tool_call_and_action_event_ids(tmp_path: Path):
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[Tool(name="EchoTool")])
    emitted_events: list[Event] = []
    conversation = Conversation(
        agent=agent,
        workspace=tmp_path / "workspace",
        persistence_dir=tmp_path / "state",
        callbacks=[emitted_events.append],
        max_iteration_per_run=1,
    )

    with (
        patch(
            "openhands.sdk.llm.llm.litellm_completion",
            side_effect=lambda messages, **kw: _mock_response_with_tool_call(
                "call_abc123"
            ),
        ),
        patch(
            "openhands.sdk.agent.agent.should_enable_observability", return_value=True
        ),
        patch(
            "openhands.sdk.agent.base.has_vision_profile_available",
            return_value=False,
        ),
        patch(
            "openhands.sdk.agent.agent.observe",
            side_effect=lambda **kwargs: (lambda f: f),
        ) as mock_observe,
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="please echo hi")])
        )
        conversation.run()

    persisted_events = conversation.state.events
    persisted_action = next(
        event for event in persisted_events if isinstance(event, ActionEvent)
    )
    emitted_action = next(
        event for event in emitted_events if isinstance(event, ActionEvent)
    )
    emitted_observation = next(
        event for event in emitted_events if isinstance(event, ObservationEvent)
    )
    linked_action = persisted_events[
        persisted_events.get_index(emitted_observation.action_id)
    ]

    tool_span_calls = [
        call
        for call in mock_observe.call_args_list
        if call.kwargs.get("span_type") == "TOOL"
    ]
    assert len(tool_span_calls) == 1
    assert tool_span_calls[0].kwargs["metadata"] == {
        "tool_call_id": "call_abc123",
        "action_event_id": persisted_action.id,
    }
    assert emitted_action.id == persisted_action.id
    assert emitted_observation.action_id == persisted_action.id
    assert linked_action == persisted_action
