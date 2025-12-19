"""Tests for condense functionality in conversation classes."""

import json
from collections.abc import Sequence
from unittest.mock import Mock, patch

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
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


class CondenseTestMockAction(Action):
    command: str


class CondenseTestMockObservation(Observation):
    result: str

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


def create_test_agent() -> Agent:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def create_test_agent_with_condenser() -> Agent:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    condenser_llm = LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("test-key"),
        usage_id="test-condenser-llm",
    )
    condenser = LLMSummarizingCondenser(llm=condenser_llm, max_size=100, keep_first=5)
    return Agent(llm=llm, condenser=condenser, tools=[])


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> Agent:
    return create_test_agent()


@pytest.fixture
def agent_with_condenser() -> Agent:
    return create_test_agent_with_condenser()


# ---------------------------------------------------------------------------
# Tests for LocalConversation.condense()
# ---------------------------------------------------------------------------


def test_local_conversation_condense_without_condenser(tmp_path, agent):
    """condense raises ValueError when no condenser is configured."""
    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # Add some events to create history
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hello, how are you?")],
            ),
        )
    )

    # Call condense should raise ValueError
    with pytest.raises(
        ValueError, match="Cannot condense conversation: No condenser configured"
    ):
        conv.condense()


@patch(
    "openhands.sdk.context.condenser.llm_summarizing_condenser.LLMSummarizingCondenser.condense"
)
def test_local_conversation_condense_with_condenser(
    mock_condense, tmp_path, agent_with_condenser
):
    """condense adds CondensationRequest and calls agent.step() when condenser is configured."""  # noqa: E501
    # Mock the condenser to avoid actual LLM calls
    from openhands.sdk.event.condenser import Condensation

    # Return a Condensation event to simulate successful condensation
    mock_condense.return_value = Condensation(
        summary="Test summary", llm_response_id="test-response-id"
    )

    conv = Conversation(
        agent=agent_with_condenser,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # Add some events to create history
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Hello, how are you?")],
            ),
        )
    )

    # Call condense
    conv.condense()

    # Check that a CondensationRequest was added to the events
    from openhands.sdk.event.condenser import CondensationRequest

    condensation_requests = [
        e for e in conv.state.events if isinstance(e, CondensationRequest)
    ]
    assert len(condensation_requests) == 1

    # The condenser should have been called
    mock_condense.assert_called_once()


def test_local_conversation_condense_copies_llm_config(tmp_path):
    """condense raises ValueError when no condenser is configured, even with custom LLM config."""  # noqa: E501
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

    # Add some events to create history
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Test message")],
            ),
        )
    )

    # Call condense should raise ValueError
    with pytest.raises(
        ValueError, match="Cannot condense conversation: No condenser configured"
    ):
        conv.condense()


def test_local_conversation_condense_with_existing_events_and_tool_calls(
    tmp_path, agent
):
    """condense raises ValueError when no condenser is configured, even with complex history."""  # noqa: E501
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
            action=CondenseTestMockAction(command="ls -la"),
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
            observation=CondenseTestMockObservation(result=observation_result),
            action_id="action_123",
            tool_name="terminal",
            tool_call_id="call_123",
        )
    )

    # Call condense should raise ValueError
    with pytest.raises(
        ValueError, match="Cannot condense conversation: No condenser configured"
    ):
        conv.condense()


def test_local_conversation_condense_force_condenser_bypasses_window(tmp_path, agent):
    """condense raises ValueError when no condenser is configured, even with minimal history."""  # noqa: E501
    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # Add minimal events (normally wouldn't trigger condensation)
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Short message")],
            ),
        )
    )

    # Call condense should raise ValueError
    with pytest.raises(
        ValueError, match="Cannot condense conversation: No condenser configured"
    ):
        conv.condense()


# ---------------------------------------------------------------------------
# Tests for RemoteConversation.condense()
# ---------------------------------------------------------------------------


def test_remote_conversation_condense(agent):
    """RemoteConversation.condense() calls the server condense endpoint."""
    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")
    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    # Response for /condense
    mock_condense_response = Mock()
    mock_condense_response.raise_for_status.return_value = None
    mock_condense_response.json.return_value = {"success": True}

    def mock_request(method, url, **kwargs):
        if method == "POST" and "condense" in url:
            return mock_condense_response

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

        # Call condense - should not raise any exceptions
        conv.condense()

        # Ensure we made exactly one condense call
        condense_calls = [
            c
            for c in mock_client.request.call_args_list
            if len(c[0]) >= 2 and "condense" in c[0][1]
        ]
        assert len(condense_calls) == 1

        (method, url), kwargs = condense_calls[0]
        assert method == "POST"
        assert "condense" in url
        # condense endpoint doesn't require a JSON payload
        assert "json" not in kwargs or kwargs["json"] is None


def test_remote_conversation_condense_with_agent_with_condenser(agent_with_condenser):
    """RemoteConversation.condense() works with agents that have condensers."""
    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")
    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    # Response for /condense
    mock_condense_response = Mock()
    mock_condense_response.raise_for_status.return_value = None
    mock_condense_response.json.return_value = {"success": True}

    def mock_request(method, url, **kwargs):
        if method == "POST" and "condense" in url:
            return mock_condense_response

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
            agent=agent_with_condenser,
            workspace=workspace,
        )

        # Call condense - should work with condenser-enabled agent
        conv.condense()

        # Ensure we made exactly one condense call
        condense_calls = [
            c
            for c in mock_client.request.call_args_list
            if len(c[0]) >= 2 and "condense" in c[0][1]
        ]
        assert len(condense_calls) == 1


# ---------------------------------------------------------------------------
# Exception handling tests
# ---------------------------------------------------------------------------


def test_local_conversation_condense_raises_context_window_error(tmp_path, agent):
    """condense raises ValueError when no condenser is configured."""
    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # Add some events to create history
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Test message")],
            ),
        )
    )

    # Call condense should raise ValueError
    with pytest.raises(
        ValueError, match="Cannot condense conversation: No condenser configured"
    ):
        conv.condense()


def test_local_conversation_condense_handles_empty_response(tmp_path, agent):
    """condense raises ValueError when no condenser is configured."""
    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # Add some events to create history
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Test message")],
            ),
        )
    )

    # Call condense should raise ValueError
    with pytest.raises(
        ValueError, match="Cannot condense conversation: No condenser configured"
    ):
        conv.condense()


def test_remote_conversation_condense_raises_http_status_error(agent):
    """RemoteConversation condense properly propagates HTTPStatusError from server."""
    import httpx

    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")
    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    # Mock HTTP error response for condense endpoint
    mock_error_response = Mock()
    mock_error_response.status_code = 500
    mock_error_response.reason_phrase = "Internal Server Error"
    mock_error_response.json.return_value = {"error": "Condensation failed"}
    mock_error_response.text = "Internal Server Error"

    def mock_request(method, url, **kwargs):
        if method == "POST" and "condense" in url:
            # Raise HTTPStatusError for condense requests
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

        # condense should propagate the HTTPStatusError
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            conv.condense()

        assert "500 Internal Server Error" in str(exc_info.value)


def test_remote_conversation_condense_raises_request_error(agent):
    """RemoteConversation condense properly propagates RequestError from network."""
    import httpx

    workspace = RemoteWorkspace(host="http://test-server", working_dir="/tmp")
    mock_client = create_mock_http_client("12345678-1234-5678-9abc-123456789abc")

    def mock_request(method, url, **kwargs):
        if method == "POST" and "condense" in url:
            # Raise RequestError for condense requests
            raise httpx.RequestError("Network connection failed")

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

        # condense should propagate the RequestError
        with pytest.raises(httpx.RequestError) as exc_info:
            conv.condense()

        assert "Network connection failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# LLM Registry tests
# ---------------------------------------------------------------------------


def test_local_conversation_condense_llm_registry_isolation(tmp_path, agent):
    """condense raises ValueError when no condenser is configured."""
    conv = Conversation(
        agent=agent,
        persistence_dir=str(tmp_path),
        workspace=str(tmp_path),
    )

    # Add some events to create history
    conv.state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Test message")],
            ),
        )
    )

    # Check initial LLM registry state
    initial_llms = conv.llm_registry.list_usage_ids()
    assert "condense-llm" not in initial_llms

    # Call condense should raise ValueError
    with pytest.raises(
        ValueError, match="Cannot condense conversation: No condenser configured"
    ):
        conv.condense()

    # LLM registry should remain unchanged
    final_llms = conv.llm_registry.list_usage_ids()
    assert "condense-llm" not in final_llms
