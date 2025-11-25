"""Tests for ask_agent functionality in conversation classes."""

import tempfile
from unittest.mock import Mock, patch

from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.llm import LLM, LLMResponse, Message, MetricsSnapshot
from openhands.sdk.workspace import RemoteWorkspace
from tests.sdk.conversation.conftest import create_mock_http_client


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def create_mock_llm_response(content: str) -> LLMResponse:
    """Create a properly structured LLM response."""
    # Create LiteLLM message
    message = LiteLLMMessage(content=content, role="assistant")

    # Create choice
    choice = Choices(finish_reason="stop", index=0, message=message)

    # Create usage
    usage = Usage(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )

    # Create ModelResponse
    model_response = ModelResponse(
        id="test-id",
        choices=[choice],
        created=1234567890,
        model="gpt-4o-mini",
        object="chat.completion",
        usage=usage,
    )
    message = Message.from_llm_chat_message(choice["message"])
    metrics = MetricsSnapshot(
        model_name="gpt-4o-mini",
        accumulated_cost=0.0,
        max_budget_per_task=None,
        accumulated_token_usage=None,
    )
    return LLMResponse(
        message=message,
        metrics=metrics,
        raw_response=model_response,
    )


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_ask_agent(mock_completion):
    """Test ask_agent functionality in LocalConversation."""
    agent = create_test_agent()

    # Mock the LLM completion response
    mock_response = create_mock_llm_response("This is the agent's response")
    mock_completion.return_value = mock_response

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        result = conv.ask_agent("What is 2+2?")

        assert result == "This is the agent's response"

        # Verify the LLM was called with the correct messages
        mock_completion.assert_called_once()
        call_args = mock_completion.call_args[0][0]
        # Should include system message and user question
        assert len(call_args) >= 2
        # Last message should be the user question
        user_message = call_args[-1]
        assert user_message.role == "user"
        assert (
            "# Question section\n"
            "Based on the activity so far answer the following question"
            "##Question\n\nWhat is 2+2?" == user_message.content[0].text
        )

        ask_agent_llm = conv.llm_registry.get("ask-agent-llm")
        assert ask_agent_llm.native_tool_calling is False
        assert ask_agent_llm.usage_id == "ask-agent-llm"
        assert ask_agent_llm.caching_prompt is False


def test_remote_conversation_ask_agent():
    """Test ask_agent functionality in RemoteConversation."""
    agent = create_test_agent()
    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")

    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    # Mock the ask_agent endpoint response
    mock_ask_response = Mock()
    mock_ask_response.raise_for_status.return_value = None
    mock_ask_response.json.return_value = {"response": "Remote agent response"}

    # Update the mock client to handle ask_agent requests
    def mock_request(method, url, **kwargs):
        if method == "POST" and "ask_agent" in url:
            return mock_ask_response
        elif method == "POST":
            # Default POST response for conversation creation
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"id": "12345678-1234-5678-9abc-123456789abc"}
            return response
        else:
            # Default GET response
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"items": []}
            return response

    mock_client.request = Mock(side_effect=mock_request)

    with patch("httpx.Client", return_value=mock_client):
        conv = RemoteConversation(
            base_url="http://test-server",
            api_key="test-key",
            agent=agent,
            workspace=workspace,
        )

        result = conv.ask_agent("What is the weather?")

        assert result == "Remote agent response"

        # Verify the correct API call was made
        mock_client.request.assert_called()
        # Find the ask_agent call
        ask_agent_calls = [
            call
            for call in mock_client.request.call_args_list
            if len(call[0]) >= 2 and "ask_agent" in call[0][1]
        ]
        assert len(ask_agent_calls) == 1

        call_args, call_kwargs = ask_agent_calls[0]
        assert call_args[0] == "POST"
        assert "ask_agent" in call_args[1]
        assert call_kwargs["json"] == {"question": "What is the weather?"}
