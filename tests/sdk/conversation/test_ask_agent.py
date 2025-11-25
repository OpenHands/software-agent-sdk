"""Tests for ask_agent functionality in conversation classes."""

import json
import tempfile
from collections.abc import Sequence
from unittest.mock import Mock, patch

from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import (
    LLM,
    ImageContent,
    LLMResponse,
    Message,
    MessageToolCall,
    MetricsSnapshot,
    TextContent,
)
from openhands.sdk.tool import Action, Observation
from openhands.sdk.workspace import RemoteWorkspace
from tests.sdk.conversation.conftest import create_mock_http_client


class MockAction(Action):
    """Mock action for testing."""

    command: str


class MockObservation(Observation):
    """Mock observation for testing."""

    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


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


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_ask_agent_with_existing_events_and_tool_calls(mock_completion):
    """Test ask_agent with existing conversation events including tool calls."""
    agent = create_test_agent()

    # Mock the LLM completion response
    mock_response = create_mock_llm_response(
        "Based on the tool calls, I can see you ran 'ls' command."
    )
    mock_completion.return_value = mock_response

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)

        # Add existing events to the conversation including tool calls
        # 1. User message
        user_message = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="List the files in current directory")],
            ),
        )
        conv.state.events.append(user_message)

        # 2. Action event with tool call
        tool_call = MessageToolCall(
            id="call_123",
            name="terminal",
            arguments=json.dumps({"command": "ls -la"}),
            origin="completion",
        )
        action_event = ActionEvent(
            source="agent",
            thought=[TextContent(text="I'll list the files using the terminal")],
            action=MockAction(command="ls -la"),
            tool_name="terminal",
            tool_call_id="call_123",
            tool_call=tool_call,
            llm_response_id="response_1",
        )
        conv.state.events.append(action_event)

        # 3. Observation event
        observation_result = (
            "total 8\n"
            "drwxr-xr-x 2 user user 4096 Nov 25 10:00 .\n"
            "drwxr-xr-x 3 user user 4096 Nov 25 09:59 ..\n"
            "-rw-r--r-- 1 user user   12 Nov 25 10:00 test.txt"
        )
        observation_event = ObservationEvent(
            source="environment",
            observation=MockObservation(result=observation_result),
            action_id="action_123",
            tool_name="terminal",
            tool_call_id="call_123",
        )
        conv.state.events.append(observation_event)

        # Now call ask_agent
        result = conv.ask_agent("What did you find?")

        assert result == "Based on the tool calls, I can see you ran 'ls' command."

        # Verify the LLM was called with all existing events converted to messages
        mock_completion.assert_called_once()
        call_args = mock_completion.call_args[0][0]

        # Should include system message, user message, assistant message with tool call,
        # tool response, and the question
        assert len(call_args) >= 5

        # Find the user message, assistant message with tool call, and tool response
        user_msg = None
        assistant_msg = None
        tool_msg = None
        question_msg = None

        for msg in call_args:
            if msg.role == "user" and any(
                "List the files" in content.text
                for content in msg.content
                if hasattr(content, "text")
            ):
                user_msg = msg
            elif msg.role == "assistant" and msg.tool_calls:
                assistant_msg = msg
            elif msg.role == "tool":
                tool_msg = msg
            elif msg.role == "user" and any(
                "What did you find?" in content.text
                for content in msg.content
                if hasattr(content, "text")
            ):
                question_msg = msg

        # Verify all expected messages are present
        assert user_msg is not None, "User message should be present"
        assert assistant_msg is not None, (
            "Assistant message with tool call should be present"
        )
        assert tool_msg is not None, "Tool response message should be present"
        assert question_msg is not None, "Question message should be present"

        # Verify tool call details
        assert len(assistant_msg.tool_calls) == 1
        assert assistant_msg.tool_calls[0].id == "call_123"
        assert assistant_msg.tool_calls[0].name == "terminal"

        # Verify tool response details
        assert tool_msg.tool_call_id == "call_123"
        assert tool_msg.name == "terminal"
