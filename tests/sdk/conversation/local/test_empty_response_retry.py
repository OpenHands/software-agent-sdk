"""Conversation outcomes for content-free chat completions (#6085)."""

import tempfile
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError


def _response(
    content: str = "",
    *,
    reasoning_content: str | None = None,
) -> ModelResponse:
    message = LiteLLMMessage(role="assistant", content=content)
    if reasoning_content is not None:
        message.reasoning_content = reasoning_content
    return ModelResponse(
        id="chatcmpl-test",
        choices=[Choices(message=message, finish_reason="stop", index=0)],
        created=1,
        model="gpt-4o",
        object="chat.completion",
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
    """Three empty completions then a reply finish, with no empty agent turn."""
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
    """Exhausted empty completions end ERROR, not STUCK."""
    mock_completion.side_effect = [_response(""), _response("")]

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = _conversation(tmpdir, num_retries=2)
        with pytest.raises(ConversationRunError) as exc_info:
            conversation.run()

        assert isinstance(exc_info.value.__cause__, LLMNoResponseError)
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


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_reasoning_only_completion_still_nudges(mock_completion):
    """A reasoning-only completion is kept and still followed by the nudge."""
    mock_completion.side_effect = [
        _response("", reasoning_content="I should write the file next."),
        _response("Wrote the summary."),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = _conversation(tmpdir, num_retries=2)
        conversation.run()

        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
        messages = _message_events(conversation)
        assert messages[1].source == "agent"
        assert (
            messages[1].llm_message.reasoning_content == "I should write the file next."
        )
        assert messages[2].source == "environment"
        nudge = messages[2].llm_message.content[0]
        assert isinstance(nudge, TextContent)
        assert "function call" in nudge.text
        assert messages[3].source == "agent"
        final = messages[3].llm_message.content[0]
        assert isinstance(final, TextContent)
        assert final.text == "Wrote the summary."
        assert mock_completion.call_count == 2
