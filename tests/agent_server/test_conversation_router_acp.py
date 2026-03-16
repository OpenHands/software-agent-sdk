"""Tests for the ACP-capable conversation router."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.conversation_router_acp import conversation_router_acp
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import ACPConversationInfo, ACPConversationPage
from openhands.agent_server.utils import utc_now
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(conversation_router_acp, prefix="/api")
    return TestClient(app)


@pytest.fixture
def mock_conversation_service():
    return AsyncMock(spec=ConversationService)


@pytest.fixture
def sample_acp_conversation_info():
    now = utc_now()
    return ACPConversationInfo(
        id=uuid4(),
        agent=ACPAgent(acp_command=["echo", "test"]),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        execution_status=ConversationExecutionStatus.IDLE,
        title="ACP Conversation",
        created_at=now,
        updated_at=now,
    )


def test_start_acp_conversation_accepts_acp_agent(
    client, mock_conversation_service, sample_acp_conversation_info
):
    mock_conversation_service.start_acp_conversation.return_value = (
        sample_acp_conversation_info,
        True,
    )
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(
            "/api/acp/conversations",
            json={
                "agent": {
                    "kind": "ACPAgent",
                    "acp_command": ["echo", "test"],
                },
                "workspace": {"working_dir": "/tmp/test"},
            },
        )

        assert response.status_code == 201
        assert response.json()["agent"]["kind"] == "ACPAgent"
        mock_conversation_service.start_acp_conversation.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_get_acp_conversation_returns_acp_agent(
    client, mock_conversation_service, sample_acp_conversation_info
):
    mock_conversation_service.get_acp_conversation.return_value = (
        sample_acp_conversation_info
    )
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            f"/api/acp/conversations/{sample_acp_conversation_info.id}"
        )

        assert response.status_code == 200
        assert response.json()["agent"]["kind"] == "ACPAgent"
    finally:
        client.app.dependency_overrides.clear()


def test_search_acp_conversations_returns_acp_page(
    client, mock_conversation_service, sample_acp_conversation_info
):
    mock_conversation_service.search_acp_conversations.return_value = (
        ACPConversationPage(
            items=[sample_acp_conversation_info],
            next_page_id=None,
        )
    )
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get("/api/acp/conversations/search")

        assert response.status_code == 200
        assert response.json()["items"][0]["agent"]["kind"] == "ACPAgent"
    finally:
        client.app.dependency_overrides.clear()


def test_batch_get_acp_conversations_returns_acp_agents(
    client, mock_conversation_service, sample_acp_conversation_info
):
    mock_conversation_service.batch_get_acp_conversations.return_value = [
        sample_acp_conversation_info
    ]
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            f"/api/acp/conversations?ids={sample_acp_conversation_info.id}"
        )

        assert response.status_code == 200
        assert response.json()[0]["agent"]["kind"] == "ACPAgent"
    finally:
        client.app.dependency_overrides.clear()
