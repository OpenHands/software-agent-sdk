"""Fixtures for agent dispatch regression tests."""

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from litellm.types.utils import ModelResponse

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.llm import (
    LLMResponse,
    Message,
    MessageToolCall,
    ReasoningItemModel,
    TextContent,
    ThinkingBlock,
)


@pytest.fixture
def tool_concurrency_limit() -> int:
    return 1


@pytest.fixture
def scripted_tool_batch(
    mock_llm: LLM,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_concurrency_limit: int,
) -> Iterator[tuple[LocalConversation, Message, list[list[Message]]]]:
    message = Message(
        role="assistant",
        content=[TextContent(text="One batch")],
        reasoning_content="Consider all calls together",
        thinking_blocks=[ThinkingBlock(thinking="One thought", signature="test")],
        responses_reasoning_item=ReasoningItemModel(id="rs_test", summary=["One plan"]),
        tool_calls=[
            MessageToolCall(
                id=f"call_{index}",
                name="think",
                arguments='{"thought": "valid"}',
                origin="completion",
            )
            for index in range(3)
        ],
    )
    requests: list[list[Message]] = []

    def generate(_self: LLM, *, messages: list[Message], **_kwargs: Any) -> LLMResponse:
        requests.append(copy.deepcopy(messages))
        assert len(requests) <= 2
        return LLMResponse(
            message=message
            if len(requests) == 1
            else Message(role="assistant", content=[TextContent(text="Done")]),
            metrics=mock_llm.metrics.get_snapshot(),
            raw_response=ModelResponse(id=f"response_{len(requests)}"),
        )

    async def agenerate(
        self: LLM, *, messages: list[Message], **kwargs: Any
    ) -> LLMResponse:
        return generate(self, messages=messages, **kwargs)

    monkeypatch.setattr(LLM, "generate", generate)
    monkeypatch.setattr(LLM, "agenerate", agenerate)
    conversation = Conversation(
        agent=Agent(
            llm=mock_llm, tools=[], tool_concurrency_limit=tool_concurrency_limit
        ),
        workspace=tmp_path,
        visualizer=None,
        max_iteration_per_run=3,
    )
    try:
        conversation.send_message("Run this batch")
        yield conversation, message, requests
    finally:
        conversation.close()
