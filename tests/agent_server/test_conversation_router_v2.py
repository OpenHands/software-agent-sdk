"""Tests for the v2 ACP-capable conversation router."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.conversation_router_v2 import conversation_router_v2
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import ConversationInfoV2
from openhands.agent_server.utils import utc_now
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(conversation_router_v2, prefix="/api")
    return TestClient(app)


@pytest.fixture
def mock_conversation_service():
    return AsyncMock(spec=ConversationService)


@pytest.fixture
def sample_conversation_info_v2():
    now = utc_now()
    return ConversationInfoV2(
        id=uuid4(),
        agent=ACPAgent(acp_command=["echo", "test"]),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        execution_status=ConversationExecutionStatus.IDLE,
        title="ACP Conversation",
        created_at=now,
        updated_at=now,
    )


def test_start_conversation_v2_accepts_acp_agent(
    client, mock_conversation_service, sample_conversation_info_v2
):
    mock_conversation_service.start_conversation_v2.return_value = (
        sample_conversation_info_v2,
        True,
    )
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(
            "/api/v2/conversations",
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
        mock_conversation_service.start_conversation_v2.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_get_conversation_v2_returns_acp_agent(
    client, mock_conversation_service, sample_conversation_info_v2
):
    mock_conversation_service.get_conversation_v2.return_value = (
        sample_conversation_info_v2
    )
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(f"/api/v2/conversations/{sample_conversation_info_v2.id}")

        assert response.status_code == 200
        assert response.json()["agent"]["kind"] == "ACPAgent"
    finally:
        client.app.dependency_overrides.clear()
