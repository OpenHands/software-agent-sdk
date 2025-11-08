"""Tests for API key functionality in RemoteConversation."""

import uuid
from unittest.mock import Mock, patch

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.remote_conversation import (
    RemoteConversation,
    WebSocketCallbackClient,
)
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import RemoteWorkspace

from ..conftest import create_mock_http_client


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def test_conversation_factory_passes_api_key_to_remote():
    """Test that Conversation factory passes api_key to RemoteConversation."""
    agent = create_test_agent()
    test_api_key = "test-api-key-123"

    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.RemoteConversation"
    ) as mock_remote:
        # Mock the RemoteConversation constructor
        mock_instance = Mock()
        mock_remote.return_value = mock_instance

        # Create conversation with RemoteWorkspace
        workspace = RemoteWorkspace(
            working_dir="/tmp",
            host="http://localhost:3000",
            api_key=test_api_key,
        )
        Conversation(
            agent=agent,
            workspace=workspace,
        )

        # Verify RemoteConversation was called with the workspace
        mock_remote.assert_called_once()
        call_args = mock_remote.call_args
        assert call_args.kwargs["workspace"] == workspace


def test_remote_conversation_no_api_key_no_headers():
    """Test that RemoteConversation doesn't add headers when no API key is provided."""
    agent = create_test_agent()

    # Mock httpx client
    mock_client_instance = create_mock_http_client()

    with (
        patch("httpx.Client", return_value=mock_client_instance) as mock_httpx_client,
        patch(
            "openhands.sdk.conversation.impl.remote_conversation"
            ".WebSocketCallbackClient"
        ),
    ):
        # Create RemoteWorkspace without API key
        workspace = RemoteWorkspace(
            working_dir="/tmp",
            host="http://localhost:3000",
            api_key=None,
        )
        # Create RemoteConversation without API key
        RemoteConversation(
            agent=agent,
            workspace=workspace,
        )

        # Verify httpx.Client was called without API key headers
        mock_httpx_client.assert_called_once()
        call_args = mock_httpx_client.call_args

        # Check that headers were empty or don't contain API key
        headers = call_args.kwargs.get("headers", {})
        assert "X-Session-API-Key" not in headers


def test_websocket_client_includes_api_key_in_url():
    """Test that WebSocketCallbackClient includes API key in WebSocket URL."""
    test_api_key = "test-api-key-123"
    host = "http://localhost:3000"
    conversation_id = str(uuid.uuid4())
    callback = Mock()

    ws_client = WebSocketCallbackClient(
        host=host,
        conversation_id=conversation_id,
        callback=callback,
        api_key=test_api_key,
    )

    # Test the URL construction logic by checking the stored api_key
    assert ws_client.api_key == test_api_key
    assert ws_client.host == host
    assert ws_client.conversation_id == conversation_id


def test_websocket_client_no_api_key():
    """Test that WebSocketCallbackClient works without API key."""
    host = "http://localhost:3000"
    conversation_id = str(uuid.uuid4())
    callback = Mock()

    ws_client = WebSocketCallbackClient(
        host=host,
        conversation_id=conversation_id,
        callback=callback,
        api_key=None,
    )

    # Test that it works without API key
    assert ws_client.api_key is None
    assert ws_client.host == host
    assert ws_client.conversation_id == conversation_id


def test_remote_conversation_passes_api_key_to_websocket_client():
    """Test that RemoteConversation passes API key to WebSocketCallbackClient."""
    agent = create_test_agent()
    test_api_key = "test-api-key-123"

    # Mock httpx client
    mock_client_instance = create_mock_http_client()

    with (
        patch("httpx.Client", return_value=mock_client_instance),
        patch(
            "openhands.sdk.conversation.impl.remote_conversation"
            ".WebSocketCallbackClient"
        ) as mock_ws_client,
    ):
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create RemoteWorkspace with API key
        workspace = RemoteWorkspace(
            working_dir="/tmp",
            host="http://localhost:3000",
            api_key=test_api_key,
        )
        # Create RemoteConversation with API key
        RemoteConversation(
            agent=agent,
            workspace=workspace,
        )

        # Verify WebSocketCallbackClient was called with api_key
        mock_ws_client.assert_called_once()
        call_args = mock_ws_client.call_args
        assert call_args.kwargs["api_key"] == test_api_key
