"""Tests for ask_agent functionality in conversation classes."""

import json
from collections.abc import Sequence
from unittest.mock import Mock, patch

import pytest
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


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockAction(Action):
    command: str


class MockObservation(Observation):
    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


def create_test_agent() -> Agent:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def create_mock_llm_response(content: str) -> LLMResponse:
    """Create a minimal, properly structured LLM response."""
    message = LiteLLMMessage(content=content, role="assistant")
    choice = Choices(finish_reason="stop", index=0, message=message)
    usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    model_response = ModelResponse(
        id="test-id",
        choices=[choice],
        created=1234567890,
        model="gpt-4o-mini",
        object="chat.completion",
        usage=usage,
    )

    msg = Message.from_llm_chat_message(choice["message"])
    metrics = MetricsSnapshot(
        model_name="gpt-4o-mini",
        accumulated_cost=0.0,
        max_budget_per_task=None,
        accumulated_token_usage=None,
    )

    return LLMResponse(message=msg, metrics=metrics, raw_response=model_response)


def find_msg(messages: list[Message], role: str, text_substring: str | None = None):
    """Find first message with given role and (optionally) containing a substring."""
    for m in messages:
        if m.role != role:
            continue
        if text_substring is None:
            return m
        if any(getattr(c, "text", "").find(text_substring) != -1 for c in m.content):
            return m
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> Agent:
    return create_test_agent()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_ask_agent(mock_completion, tmp_path, agent):
    """ask_agent returns the LLM response and configures a dedicated ask-agent-llm."""
    mock_completion.return_value = create_mock_llm_response(
        "This is the agent's response"
    )

    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    result = conv.ask_agent("What is 2+2?")

    assert result == "This is the agent's response"

    # LLM was called with a question appended as the last user message
    mock_completion.assert_called_once()
    messages = mock_completion.call_args.kwargs["messages"]
    assert len(messages) >= 2

    user_msg = messages[-1]
    assert user_msg.role == "user"
    expected_text = (
        "<QUESTION>\n"
        "Based on the activity so far answer the following question\n\n"
        "## Question\n"
        "What is 2+2?\n\n\n"
        "<IMPORTANT>\n"
        "This is a question, do not make any tool call and just answer my question.\n"
        "</IMPORTANT>\n"
        "</QUESTION>"
    )
    assert user_msg.content[0].text == expected_text

    # Dedicated ask-agent LLM is configured correctly
    ask_agent_llm = conv.llm_registry.get("ask-agent-llm")
    # Verify that parameters are copied from the original agent's LLM
    assert ask_agent_llm.native_tool_calling == agent.llm.native_tool_calling
    assert ask_agent_llm.caching_prompt == agent.llm.caching_prompt
    assert ask_agent_llm.usage_id == "ask-agent-llm"
    # Since we're using default LLM values, these should be True
    assert ask_agent_llm.native_tool_calling is True
    assert ask_agent_llm.caching_prompt is True


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_ask_agent_copies_llm_config(mock_completion, tmp_path):
    """ask_agent creates LLM with parameters copied from original agent's LLM."""
    mock_completion.return_value = create_mock_llm_response("Test response")

    # Create agent with custom LLM configuration
    llm = LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
        native_tool_calling=False,  # Non-default value
        caching_prompt=False,  # Non-default value
    )
    agent = Agent(llm=llm, tools=[])

    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    result = conv.ask_agent("Test question")
    assert result == "Test response"

    # Verify that ask-agent-llm copies the custom configuration
    ask_agent_llm = conv.llm_registry.get("ask-agent-llm")
    assert ask_agent_llm.native_tool_calling == agent.llm.native_tool_calling
    assert ask_agent_llm.caching_prompt == agent.llm.caching_prompt
    assert ask_agent_llm.usage_id == "ask-agent-llm"
    # Verify the specific custom values are copied
    assert ask_agent_llm.native_tool_calling is False
    assert ask_agent_llm.caching_prompt is False


def test_remote_conversation_ask_agent(agent):
    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")
    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    # Response for /ask_agent
    mock_ask_response = Mock()
    mock_ask_response.raise_for_status.return_value = None
    mock_ask_response.json.return_value = {"response": "Remote agent response"}

    def mock_request(method, url, **kwargs):
        if method == "POST" and "ask_agent" in url:
            return mock_ask_response

        response = Mock()
        response.raise_for_status.return_value = None
        # For conversation creation, return an ID; otherwise, return empty list
        response.json.return_value = (
            {"id": "12345678-1234-5678-9abc-123456789abc"}
            if method == "POST"
            else {"items": []}
        )
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

        # Ensure we made exactly one ask_agent call with the expected payload
        ask_calls = [
            c
            for c in mock_client.request.call_args_list
            if len(c[0]) >= 2 and "ask_agent" in c[0][1]
        ]
        assert len(ask_calls) == 1

        (method, url), kwargs = ask_calls[0]
        assert method == "POST"
        assert "ask_agent" in url
        assert kwargs["json"] == {"question": "What is the weather?"}


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_ask_agent_with_existing_events_and_tool_calls(
    mock_completion, tmp_path, agent
):
    """ask_agent includes prior events (user, tool call, observation) in the context."""
    mock_completion.return_value = create_mock_llm_response(
        "Based on the tool calls, I can see you ran 'ls' command."
    )

    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # 1. Prior user message
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="List the files in current directory")],
            ),
        )
    )

    # 2. Action event with tool call
    tool_call = MessageToolCall(
        id="call_123",
        name="terminal",
        arguments=json.dumps({"command": "ls -la"}),
        origin="completion",
    )
    conv.state.events.append(
        ActionEvent(
            source="agent",
            thought=[TextContent(text="I'll list the files using the terminal")],
            action=MockAction(command="ls -la"),
            tool_name="terminal",
            tool_call_id="call_123",
            tool_call=tool_call,
            llm_response_id="response_1",
        )
    )

    # 3. Observation event (tool result)
    observation_result = (
        "total 8\n"
        "drwxr-xr-x 2 user user 4096 Nov 25 10:00 .\n"
        "drwxr-xr-x 3 user user 4096 Nov 25 09:59 ..\n"
        "-rw-r--r-- 1 user user   12 Nov 25 10:00 test.txt"
    )
    conv.state.events.append(
        ObservationEvent(
            source="environment",
            observation=MockObservation(result=observation_result),
            action_id="action_123",
            tool_name="terminal",
            tool_call_id="call_123",
        )
    )

    # ask_agent should incorporate the entire history
    result = conv.ask_agent("What did you find?")
    assert result == "Based on the tool calls, I can see you ran 'ls' command."

    mock_completion.assert_called_once()
    messages = mock_completion.call_args.kwargs["messages"]

    # Expect: system message + user + assistant(tool_call) + tool + question
    assert len(messages) >= 5

    user_msg = find_msg(messages, "user", "List the files")
    assistant_msg = next(
        (m for m in messages if m.role == "assistant" and m.tool_calls), None
    )
    tool_msg = next((m for m in messages if m.role == "tool"), None)
    question_msg = find_msg(messages, "user", "What did you find?")

    assert user_msg is not None, "User message should be present"
    assert assistant_msg is not None, "Assistant tool-call message should be present"
    assert tool_msg is not None, "Tool response message should be present"
    assert question_msg is not None, "ask_agent question message should be present"

    # Tool call wiring
    assert len(assistant_msg.tool_calls) == 1
    assert assistant_msg.tool_calls[0].id == "call_123"
    assert assistant_msg.tool_calls[0].name == "terminal"

    assert tool_msg.tool_call_id == "call_123"
    assert tool_msg.name == "terminal"


# ---------------------------------------------------------------------------
# Exception handling tests
# ---------------------------------------------------------------------------


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_ask_agent_raises_context_window_error(
    mock_completion, tmp_path, agent
):
    """ask_agent properly propagates LLMContextWindowExceedError from LLM completion."""
    from openhands.sdk.llm.exceptions import LLMContextWindowExceedError

    # Mock LLM completion to raise context window error
    mock_completion.side_effect = LLMContextWindowExceedError(
        "Context window exceeded: conversation too long"
    )

    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # ask_agent should propagate the exception
    with pytest.raises(LLMContextWindowExceedError) as exc_info:
        conv.ask_agent("What is the current status?")

    assert "Context window exceeded" in str(exc_info.value)
    mock_completion.assert_called_once()


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_ask_agent_raises_failed_to_generate_summary(
    mock_completion, tmp_path, agent
):
    """ask_agent raises 'Failed to generate summary' when LLM returns no text."""
    # Mock LLM response with no text content
    mock_response = create_mock_llm_response("")
    mock_response.message.content = []  # Empty content list
    mock_completion.return_value = mock_response

    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # ask_agent should raise the generic exception
    with pytest.raises(Exception) as exc_info:
        conv.ask_agent("What is the current status?")

    assert str(exc_info.value) == "Failed to generate summary"
    mock_completion.assert_called_once()


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_ask_agent_raises_failed_to_generate_summary_non_text(
    mock_completion, tmp_path, agent
):
    """ask_agent raises 'Failed to generate summary' when LLM returns only non-text."""
    # Mock LLM response with only image content (no text content)
    mock_response = create_mock_llm_response("")
    mock_response.message.content = [
        ImageContent(image_urls=["http://example.com/image.jpg"])
    ]
    mock_completion.return_value = mock_response

    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # ask_agent should raise the generic exception
    with pytest.raises(Exception) as exc_info:
        conv.ask_agent("What is the current status?")

    assert str(exc_info.value) == "Failed to generate summary"
    mock_completion.assert_called_once()


def test_remote_conversation_ask_agent_raises_http_status_error(agent):
    """RemoteConversation ask_agent properly propagates HTTPStatusError from server."""
    import httpx

    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")
    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    # Mock HTTP error response for ask_agent endpoint
    mock_error_response = Mock()
    mock_error_response.status_code = 500
    mock_error_response.reason_phrase = "Internal Server Error"
    mock_error_response.json.return_value = {"error": "LLM context window exceeded"}
    mock_error_response.text = "Internal Server Error"

    def mock_request(method, url, **kwargs):
        if method == "POST" and "ask_agent" in url:
            # Raise HTTPStatusError for ask_agent requests
            raise httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=Mock(),
                response=mock_error_response,
            )

        # Normal responses for other requests
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = (
            {"id": "12345678-1234-5678-9abc-123456789abc"}
            if method == "POST"
            else {"items": []}
        )
        return response

    mock_client.request = Mock(side_effect=mock_request)

    with patch("httpx.Client", return_value=mock_client):
        conv = RemoteConversation(
            base_url="http://test-server",
            api_key="test-key",
            agent=agent,
            workspace=workspace,
        )

        # ask_agent should propagate the HTTPStatusError
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            conv.ask_agent("What is the current status?")

        assert "500 Internal Server Error" in str(exc_info.value)


def test_remote_conversation_ask_agent_raises_request_error(agent):
    """RemoteConversation ask_agent properly propagates RequestError from network."""
    import httpx

    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")
    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    def mock_request(method, url, **kwargs):
        if method == "POST" and "ask_agent" in url:
            # Raise RequestError for ask_agent requests (network error)
            raise httpx.RequestError("Connection failed", request=Mock())

        # Normal responses for other requests
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = (
            {"id": "12345678-1234-5678-9abc-123456789abc"}
            if method == "POST"
            else {"items": []}
        )
        return response

    mock_client.request = Mock(side_effect=mock_request)

    with patch("httpx.Client", return_value=mock_client):
        conv = RemoteConversation(
            base_url="http://test-server",
            api_key="test-key",
            agent=agent,
            workspace=workspace,
        )

        # ask_agent should propagate the RequestError
        with pytest.raises(httpx.RequestError) as exc_info:
            conv.ask_agent("What is the current status?")

        assert "Connection failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Template directory and rendering tests
# ---------------------------------------------------------------------------


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_ask_agent_template_dir_path_construction(mock_completion, tmp_path, agent):
    """Test that ask_agent correctly constructs template_dir path and finds template."""
    mock_completion.return_value = create_mock_llm_response(
        "Template rendered successfully"
    )

    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # Call ask_agent to trigger template_dir construction
    result = conv.ask_agent("Test question")
    assert result == "Template rendered successfully"

    # Verify LLM was called with properly formatted question
    mock_completion.assert_called_once()
    messages = mock_completion.call_args.kwargs["messages"]

    # Find the user message with the question
    question_msg = None
    for msg in messages:
        if msg.role == "user" and msg.content:
            for content in msg.content:
                if isinstance(content, TextContent) and "Test question" in content.text:
                    question_msg = msg
                    break

    assert question_msg is not None, "Question message should be found"

    # Verify the template was rendered correctly (contains expected template structure)
    question_text = question_msg.content[0].text
    assert "<QUESTION>" in question_text
    assert "Test question" in question_text
    assert "<IMPORTANT>" in question_text
    assert "do not make any tool call" in question_text
