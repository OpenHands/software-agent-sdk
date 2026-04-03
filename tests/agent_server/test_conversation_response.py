"""Tests for the GET /conversations/{id}/agent_final_response endpoint."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.sdk import Message
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def sample_conversation_id():
    return uuid4()


@pytest.fixture
def mock_conversation_service():
    return AsyncMock(spec=ConversationService)


@pytest.fixture
def mock_event_service():
    return AsyncMock(spec=EventService)


def test_get_response_with_finish_action(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Endpoint returns FinishAction message text."""
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.get_agent_final_response.return_value = (
        "Task completed successfully!"
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            f"/api/conversations/{sample_conversation_id}/agent_final_response"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["response"] == "Task completed successfully!"
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
        mock_event_service.get_agent_final_response.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_get_response_empty_when_no_agent_events(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Endpoint returns empty string when no agent response exists."""
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.get_agent_final_response.return_value = ""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            f"/api/conversations/{sample_conversation_id}/agent_final_response"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["response"] == ""
    finally:
        client.app.dependency_overrides.clear()


def test_get_response_conversation_not_found(
    client, mock_conversation_service, sample_conversation_id
):
    """Endpoint returns 404 when conversation does not exist."""
    mock_conversation_service.get_event_service.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            f"/api/conversations/{sample_conversation_id}/agent_final_response"
        )
        assert response.status_code == 404
    finally:
        client.app.dependency_overrides.clear()


def test_event_service_get_agent_final_response_with_finish():
    """EventService delegates to get_agent_final_response from SDK."""
    event_service = EventService(stored=MagicMock(), conversations_dir=Path("test_dir"))

    finish_action = FinishAction(message="Done!")
    tool_call = MessageToolCall(
        id="tc1", name="finish", arguments="{}", origin="completion"
    )
    action_event = ActionEvent(
        source="agent",
        thought=[TextContent(text="Finishing")],
        action=finish_action,
        tool_name="finish",
        tool_call_id="tc1",
        tool_call=tool_call,
        llm_response_id="resp1",
    )

    conversation = MagicMock()
    state = MagicMock()
    state.events = [action_event]
    conversation._state = state
    event_service._conversation = conversation

    result = event_service._get_agent_final_response_sync()
    assert result == "Done!"


def test_event_service_get_agent_final_response_with_message():
    """EventService returns MessageEvent text when no FinishAction."""
    event_service = EventService(stored=MagicMock(), conversations_dir=Path("test_dir"))

    message_event = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant",
            content=[TextContent(text="Here is my answer")],
        ),
    )

    conversation = MagicMock()
    state = MagicMock()
    state.events = [message_event]
    conversation._state = state
    event_service._conversation = conversation

    result = event_service._get_agent_final_response_sync()
    assert result == "Here is my answer"


def test_event_service_get_agent_final_response_empty():
    """EventService returns empty string with no agent events."""
    event_service = EventService(stored=MagicMock(), conversations_dir=Path("test_dir"))

    conversation = MagicMock()
    state = MagicMock()
    state.events = []
    conversation._state = state
    event_service._conversation = conversation

    result = event_service._get_agent_final_response_sync()
    assert result == ""


def test_event_service_get_agent_final_response_inactive():
    """EventService raises ValueError when service is inactive."""
    event_service = EventService(stored=MagicMock(), conversations_dir=Path("test_dir"))

    with pytest.raises(ValueError, match="inactive_service"):
        event_service._get_agent_final_response_sync()
