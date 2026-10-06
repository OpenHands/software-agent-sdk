import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from litellm.types.utils import ModelResponse

from openhands.sdk import LLM, Agent
from openhands.sdk.context.condenser import AgentResetCondenser
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLMResponse, Message, MessageToolCall, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.tool import Tool


def response(text="", calls=(), response_id="response"):
    return LLMResponse(
        message=Message(
            role="assistant",
            content=[TextContent(text=text)] if text else [],
            tool_calls=[
                MessageToolCall(
                    id=f"{response_id}-{index}",
                    name=name,
                    arguments=json.dumps(arguments),
                    origin="completion",
                )
                for index, (name, arguments) in enumerate(calls)
            ],
        ),
        raw_response=ModelResponse(id=response_id),
        metrics=MetricsSnapshot(
            model_name="test-model",
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=None,
        ),
    )


@pytest.fixture
def response_factory():
    return response


@pytest.fixture
def scripted_conversation(monkeypatch, tmp_path):
    conversations = []

    def make(
        script,
        before: Callable | None = None,
        max_input_tokens=None,
        *,
        tools=None,
        callbacks=None,
    ):
        requests = []
        remaining = iter(script)

        def complete(llm, messages, **kwargs):
            requests.append(messages)
            if before is not None:
                before(len(requests))
            result = next(remaining)
            if isinstance(result, Exception):
                raise result
            return result

        async def acomplete(llm, messages, **kwargs):
            return await asyncio.to_thread(complete, llm, messages, **kwargs)

        monkeypatch.setattr(LLM, "completion", complete)
        monkeypatch.setattr(LLM, "acompletion", acomplete)
        llm = LLM(
            model="test-model",
            usage_id="test-reset",
            max_input_tokens=max_input_tokens,
        )
        agent = Agent(
            llm=llm,
            system_prompt="Keep working and use history when needed.",
            condenser=AgentResetCondenser(),
            tools=tools
            if tools is not None
            else [Tool(name="NewContextTool"), Tool(name="ConversationHistoryTool")],
        )
        conversation = LocalConversation(
            agent=agent,
            workspace=str(tmp_path),
            persistence_dir=str(tmp_path / "state"),
            callbacks=callbacks,
            visualizer=None,
        )
        conversations.append(conversation)
        return conversation, requests

    yield make
    for conversation in conversations:
        conversation.close()


@pytest.fixture
def reopen_conversation():
    reopened = []

    def reopen(original):
        assert original.state.persistence_dir is not None
        original.close()
        conversation = LocalConversation(
            agent=None,
            workspace=original.workspace,
            persistence_dir=Path(original.state.persistence_dir).parent,
            conversation_id=original.id,
            visualizer=None,
        )
        reopened.append(conversation)
        return conversation

    yield reopen
    for conversation in reopened:
        conversation.close()
