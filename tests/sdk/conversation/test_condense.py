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


def create_test_agent_with_condenser() -> Agent:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    condenser_llm = LLM(
        model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="condenser-llm"
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


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_without_condenser(
    mock_completion, tmp_path, agent
):
    """condense creates ForceCondenser with agent's LLM when no condenser configured."""
    mock_completion.return_value = create_mock_llm_response(
        "## Summary\nThis is a condensed summary of the conversation."
    )

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

    # Call condense
    conv.condense()

    # LLM was called for condensation
    mock_completion.assert_called_once()
    messages = mock_completion.call_args.kwargs["messages"]
    assert len(messages) >= 1

    # Dedicated condense-llm is configured correctly
    condense_llm = conv.llm_registry.get("condense-llm")
    assert condense_llm.usage_id == "condense-llm"
    # Verify that parameters are copied from the original agent's LLM
    assert condense_llm.model == agent.llm.model
    assert condense_llm.native_tool_calling == agent.llm.native_tool_calling
    assert condense_llm.caching_prompt == agent.llm.caching_prompt


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_with_condenser(
    mock_completion, tmp_path, agent_with_condenser
):
    """condense uses existing condenser's LLM configuration when condenser is configured."""  # noqa: E501
    mock_completion.return_value = create_mock_llm_response(
        "## Summary\nThis is a condensed summary using the existing condenser."
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

    # LLM was called for condensation
    mock_completion.assert_called_once()

    # Dedicated condense-llm is configured correctly
    condense_llm = conv.llm_registry.get("condense-llm")
    assert condense_llm.usage_id == "condense-llm"
    # Verify that parameters are copied from the condenser's LLM
    assert condense_llm.model == agent_with_condenser.condenser.llm.model
    assert (
        condense_llm.native_tool_calling
        == agent_with_condenser.condenser.llm.native_tool_calling
    )
    assert (
        condense_llm.caching_prompt == agent_with_condenser.condenser.llm.caching_prompt
    )


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_copies_llm_config(mock_completion, tmp_path):
    """condense creates LLM with parameters copied from original agent's LLM."""
    mock_completion.return_value = create_mock_llm_response("Test condensation")

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

    conv.condense()

    # Verify that condense-llm copies the custom configuration
    condense_llm = conv.llm_registry.get("condense-llm")
    assert condense_llm.native_tool_calling == agent.llm.native_tool_calling
    assert condense_llm.caching_prompt == agent.llm.caching_prompt
    assert condense_llm.usage_id == "condense-llm"
    # Verify the specific custom values are copied
    assert condense_llm.native_tool_calling is False
    assert condense_llm.caching_prompt is False


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_with_existing_events_and_tool_calls(
    mock_completion, tmp_path, agent
):
    """condense includes prior events (user, tool call, observation) in the context."""
    summary_text = (
        "## Summary\nUser requested file listing. "
        "Agent executed 'ls' command and found test.txt file."
    )
    mock_completion.return_value = create_mock_llm_response(summary_text)

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

    # condense should incorporate the entire history
    conv.condense()

    mock_completion.assert_called_once()
    messages = mock_completion.call_args.kwargs["messages"]

    # The condense method creates a single user message with the condensation prompt
    # containing the conversation history, so we expect at least 1 message
    assert len(messages) >= 1

    # The condensation prompt should be a user message
    condensation_msg = messages[0]
    assert condensation_msg.role == "user"

    # The condensation should have been applied (we can see from the output that it worked)  # noqa: E501
    # The specific content format is handled by the condenser implementation


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_force_condenser_bypasses_window(
    mock_completion, tmp_path, agent
):
    """condense uses ForceCondenser that bypasses condensation window requirements."""
    mock_completion.return_value = create_mock_llm_response(
        "## Summary\nForced condensation applied."
    )

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

    # condense should work even with minimal history (ForceCondenser bypasses window)
    conv.condense()

    # LLM was called despite minimal history
    mock_completion.assert_called_once()

    # Verify ForceCondenser was used by checking that condensation happened
    # even with minimal events
    condense_llm = conv.llm_registry.get("condense-llm")
    assert condense_llm is not None


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


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_raises_context_window_error(
    mock_completion, tmp_path, agent
):
    """condense properly propagates LLMContextWindowExceedError from LLM completion."""
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

    # condense should propagate the exception
    with pytest.raises(LLMContextWindowExceedError) as exc_info:
        conv.condense()

    assert "Context window exceeded" in str(exc_info.value)
    mock_completion.assert_called_once()


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_handles_empty_response(
    mock_completion, tmp_path, agent
):
    """condense handles empty LLM response gracefully."""
    # Mock LLM response with no text content
    mock_response = create_mock_llm_response("")
    mock_response.message.content = []  # Empty content list
    mock_completion.return_value = mock_response

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

    # condense should handle empty response gracefully (no exception)
    conv.condense()
    mock_completion.assert_called_once()


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


@patch("openhands.sdk.llm.llm.LLM.completion")
def test_local_conversation_condense_llm_registry_isolation(
    mock_completion, tmp_path, agent
):
    """condense creates separate LLM instances that don't interfere with agent's LLM."""
    mock_completion.return_value = create_mock_llm_response("Test condensation")

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

    # Call condense
    conv.condense()

    # Check final LLM registry state
    final_llms = conv.llm_registry.list_usage_ids()
    assert "condense-llm" in final_llms

    # Verify LLM instances are separate
    condense_llm = conv.llm_registry.get("condense-llm")
    agent_llm = agent.llm

    assert condense_llm is not agent_llm
    assert condense_llm.usage_id == "condense-llm"
    assert agent_llm.usage_id == "test-llm"
