"""Conversation outcomes for content-free chat completions (#6085).

A completion with no text, no tool calls and no reasoning is a provider
failure, not agent behaviour: the LLM layer retries it inside the retry
boundary, so no empty agent message or corrective nudge is persisted and
exhausted retries end the run as a retryable ERROR instead of STUCK.
"""

import tempfile
from unittest.mock import patch

import pytest
from litellm.types.utils import (
    Choices,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError


def _response(content: str, response_id: str = "chatcmpl-test") -> ModelResponse:
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1,
        model="gpt-4o",
        object="chat.completion",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _conversation(tmpdir: str, *, num_retries: int) -> LocalConversation:
    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=num_retries,
        retry_min_wait=0,
        retry_max_wait=0,
        temperature=0.0,
    )
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[]),
        persistence_dir=tmpdir,
        workspace=tmpdir,
    )
    assert isinstance(conversation, LocalConversation)
    conversation.send_message(
        Message(role="user", content=[TextContent(text="continue")])
    )
    return conversation


def _message_events(conversation: LocalConversation) -> list[MessageEvent]:
    return [
        event for event in conversation.state.events if isinstance(event, MessageEvent)
    ]


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_empty_completions_retry_without_persisting_or_sticking(mock_completion):
    """Three empty completions then a normal reply: the run finishes with no
    empty agent turn, no nudge, and no stuck detection."""
    mock_completion.side_effect = [
        _response(""),
        _response(""),
        _response(""),
        _response("The answer is ready."),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = _conversation(tmpdir, num_retries=5)
        conversation.run()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        messages = _message_events(conversation)
        agent_messages = [event for event in messages if event.source == "agent"]
        assert len(agent_messages) == 1
        content = agent_messages[0].llm_message.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "The answer is ready."
        assert not any(event.source == "environment" for event in messages)
        assert mock_completion.call_count == 4


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_exhausted_empty_completions_end_as_retryable_error(mock_completion):
    """When every retry returns an empty completion, the run ends as a
    retryable ERROR, not STUCK, and no empty agent message is persisted."""
    mock_completion.side_effect = [_response(""), _response("")]

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = _conversation(tmpdir, num_retries=2)
        with pytest.raises(ConversationRunError) as exc_info:
            conversation.run()

        assert isinstance(exc_info.value.original_exception, LLMNoResponseError)
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
        errors = [
            event
            for event in conversation.state.events
            if isinstance(event, ConversationErrorEvent)
        ]
        assert len(errors) == 1
        assert errors[0].code == "LLMNoResponseError"
        assert errors[0].classification is not None
        assert errors[0].classification.retryable is True
        assert not any(
            event.source == "agent" for event in _message_events(conversation)
        )
