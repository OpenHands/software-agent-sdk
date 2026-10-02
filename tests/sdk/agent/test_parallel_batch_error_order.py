"""One parallel batch stays one assistant message when calls fail validation."""

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
from openhands.sdk.event import ActionEvent, AgentErrorEvent
from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.llm import LLM, Message, TextContent


def _mock_4_invalid_calls(messages, **kwargs):
    return ModelResponse(
        id="mock-response-batch",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="four parallel calls",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id=f"call_{i}",
                            type="function",
                            function=Function(
                                name=f"nonexistent_tool_{i}",
                                arguments="{}",
                            ),
                        )
                        for i in range(1, 5)
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def test_batch_errors_flush_after_sibling_actions():
    """Validation errors must not split one parallel batch.

    Regression test for strict providers (MiniMax 2013, Gemini
    function-response count): every tool result must sit directly behind the
    assistant message carrying its call.
    """
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(llm=llm, tools=[])
    collected_events = []
    conversation = Conversation(
        agent=agent, callbacks=[collected_events.append]
    )

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=_mock_4_invalid_calls,
    ):
        conversation.send_message(
            Message(
                role="user",
                content=[TextContent(text="Please help me with something.")],
            )
        )
        agent.step(conversation, on_event=collected_events.append)

    batch = [
        e
        for e in collected_events
        if isinstance(e, (ActionEvent, AgentErrorEvent))
    ]
    kinds = [type(e).__name__ for e in batch]
    assert kinds == ["ActionEvent"] * 4 + ["AgentErrorEvent"] * 4, kinds
    assert [e.tool_call_id for e in batch[:4]] == [
        f"call_{i}" for i in range(1, 5)
    ]
    assert [e.tool_call_id for e in batch[4:]] == [
        f"call_{i}" for i in range(1, 5)
    ]

    messages = LLMConvertibleEvent.events_to_messages(
        [e for e in collected_events if isinstance(e, LLMConvertibleEvent)]
    )
    assistant_batches = [
        m for m in messages if m.role == "assistant" and m.tool_calls
    ]
    assert len(assistant_batches) == 1
    assert [c.id for c in assistant_batches[0].tool_calls] == [
        f"call_{i}" for i in range(1, 5)
    ]
    head = messages.index(assistant_batches[0])
    tail = messages[head + 1 : head + 5]
    assert [m.role for m in tail] == ["tool"] * 4
    assert [m.tool_call_id for m in tail] == [
        f"call_{i}" for i in range(1, 5)
    ]
