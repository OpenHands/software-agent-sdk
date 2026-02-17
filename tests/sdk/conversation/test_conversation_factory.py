"""Tests for Conversation factory functionality."""

import uuid
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import RemoteWorkspace


@pytest.fixture
def agent():
    """Create test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"))
    return Agent(llm=llm, tools=[])


@pytest.fixture
def remote_workspace():
    """Create RemoteWorkspace with mocked client."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace/project"
    )

    # Mock the workspace client
    mock_client = Mock()
    workspace._client = mock_client

    # Mock conversation creation response
    conversation_id = str(uuid.uuid4())
    mock_conv_response = Mock()
    mock_conv_response.raise_for_status.return_value = None
    mock_conv_response.json.return_value = {"id": conversation_id}

    # Mock events response (used by _do_full_sync during RemoteEventsList init)
    mock_events_response = Mock()
    mock_events_response.raise_for_status.return_value = None
    mock_events_response.json.return_value = {"items": [], "next_page_id": None}

    # Mock events response for reconcile() call after WebSocket subscription
    mock_reconcile_response = Mock()
    mock_reconcile_response.raise_for_status.return_value = None
    mock_reconcile_response.json.return_value = {"items": [], "next_page_id": None}

    mock_client.request.side_effect = [
        mock_conv_response,
        mock_events_response,
        mock_reconcile_response,
    ]

    return workspace


def test_conversation_factory_creates_local_by_default(agent):
    """Test factory creates LocalConversation when no workspace specified."""
    conversation = Conversation(agent=agent)

    assert isinstance(conversation, LocalConversation)


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_conversation_factory_creates_remote_with_workspace(
    mock_ws_client, agent, remote_workspace
):
    """Test factory creates RemoteConversation with RemoteWorkspace."""
    conversation = Conversation(agent=agent, workspace=remote_workspace)

    assert isinstance(conversation, RemoteConversation)


def test_conversation_factory_forwards_local_parameters(agent):
    """Test factory forwards parameters to LocalConversation correctly."""
    conversation = Conversation(
        agent=agent,
        max_iteration_per_run=100,
        stuck_detection=False,
        visualizer=None,
    )

    assert isinstance(conversation, LocalConversation)
    assert conversation.max_iteration_per_run == 100


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_conversation_factory_forwards_remote_parameters(
    mock_ws_client, agent, remote_workspace
):
    """Test factory forwards parameters to RemoteConversation correctly."""
    conversation = Conversation(
        agent=agent,
        workspace=remote_workspace,
        max_iteration_per_run=200,
        stuck_detection=True,
    )

    assert isinstance(conversation, RemoteConversation)
    assert conversation.max_iteration_per_run == 200


def test_conversation_factory_string_workspace_creates_local(agent):
    """Test that string workspace creates LocalConversation."""
    conversation = Conversation(agent=agent, workspace="")

    assert isinstance(conversation, LocalConversation)


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_conversation_factory_type_inference(mock_ws_client, agent, remote_workspace):
    """Test that type hints work correctly for both conversation types."""
    local_conv = Conversation(agent=agent)
    remote_conv = Conversation(agent=agent, workspace=remote_workspace)

    assert isinstance(local_conv, LocalConversation)
    assert isinstance(remote_conv, RemoteConversation)
