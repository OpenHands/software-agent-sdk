from __future__ import annotations

from unittest.mock import MagicMock

from litellm.types.utils import ModelResponse
from pydantic import Field, PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import LLM, LLMResponse, Message, MessageToolCall, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
from openhands.sdk.tool import Tool, ToolDefinition, ToolExecutor, register_tool
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.tool.schema import Action, Observation


class _NoOpAction(Action):
    value: str = Field(default="")


class _NoOpObservation(Observation):
    pass


class _NoOpExecutor(ToolExecutor[_NoOpAction, _NoOpObservation]):
    def __call__(
        self,
        _: _NoOpAction,
        conversation: LocalConversation | None = None,  # noqa: ARG002
    ) -> _NoOpObservation:
        return _NoOpObservation.from_text(text="ok")


class _NonDefaultTool(ToolDefinition[_NoOpAction, _NoOpObservation]):
    """A minimal non-default tool used to enable tool mode in tests."""

    @classmethod
    def create(cls, conv_state=None, **params):  # noqa: ARG003
        if params:
            raise ValueError("_NonDefaultTool doesn't accept parameters")
        return [
            cls(
                description="noop",
                action_type=_NoOpAction,
                observation_type=_NoOpObservation,
                executor=_NoOpExecutor(),
            )
        ]


# Global registration for this test module.
register_tool("TestNonDefaultTool1896", _NonDefaultTool)


class _SequencedLLM(LLM):
    _responses: list[LLMResponse] = PrivateAttr(default_factory=list)
    _calls: int = PrivateAttr(default=0)

    def __init__(self, responses: list[LLMResponse]):
        super().__init__(model="test-model", usage_id="test-llm")
        self._responses = list(responses)

    def uses_responses_api(self) -> bool:
        return False

    def completion(self, *, messages, tools=None, **kwargs) -> LLMResponse:  # type: ignore[override]
        self._calls += 1
        return self._responses.pop(0)


def _mk_response(message: Message, *, rid: str) -> LLMResponse:
    return LLMResponse(
        message=message,
        metrics=MetricsSnapshot(
            model_name="test",
            accumulated_cost=0.0,
            max_budget_per_task=0.0,
            accumulated_token_usage=TokenUsage(model="test"),
        ),
        raw_response=MagicMock(spec=ModelResponse, id=rid),
    )


def test_message_only_response_does_not_stop_conversation_with_non_default_tools(
    tmp_path,
):
    """Regression test for #1896.

    When tools beyond the default finish/think are present, a content-only model
    response (no tool_calls) should not stop the conversation run.
    """

    content_only = _mk_response(
        Message(role="assistant", content=[TextContent(text="I'll proceed.")]),
        rid="r1",
    )

    finish_call = MessageToolCall(
        id="call_finish",
        name="finish",
        arguments='{"message": "done"}',
        origin="completion",
    )
    finish_response = _mk_response(
        Message(role="assistant", content=[], tool_calls=[finish_call]),
        rid="r2",
    )

    llm = _SequencedLLM([content_only, finish_response])
    agent = Agent(llm=llm, tools=[Tool(name="TestNonDefaultTool1896")])

    conversation = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        visualizer=None,
        max_iteration_per_run=10,
        stuck_detection=False,
    )

    conversation.send_message("Do the task")
    conversation.run()

    assert llm._calls == 2

    finish_actions = [
        e
        for e in conversation.state.events
        if isinstance(e, ActionEvent)
        and e.tool_name == "finish"
        and isinstance(e.action, FinishAction)
    ]
    assert len(finish_actions) == 1

    continue_prompts = [
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent)
        and e.source == "environment"
        and e.llm_message.role == "user"
        and any(
            isinstance(c, TextContent) and "did not include any tool calls" in c.text
            for c in e.llm_message.content
        )
    ]
    assert len(continue_prompts) == 1

    assert any(
        isinstance(e, MessageEvent)
        and e.source == "agent"
        and any(
            isinstance(c, TextContent) and c.text == "I'll proceed."
            for c in e.llm_message.content
        )
        for e in conversation.state.events
    )


def test_message_only_response_finishes_when_only_default_tools_present(tmp_path):
    """If the agent only has default tools, message-only responses end the turn."""

    content_only = _mk_response(
        Message(role="assistant", content=[TextContent(text="Hello!")]),
        rid="r1",
    )

    llm = _SequencedLLM([content_only])
    agent = Agent(llm=llm, tools=[])

    conversation = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        visualizer=None,
        max_iteration_per_run=5,
        stuck_detection=False,
    )

    conversation.send_message("Hi")
    conversation.run()

    assert llm._calls == 1
    assert conversation.state.execution_status.value == "finished"
