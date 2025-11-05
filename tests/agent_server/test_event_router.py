"""Tests for event_router.py endpoints."""

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.event_router import event_router
from openhands.agent_server.event_service import EventService
from openhands.sdk import Message
from openhands.sdk.llm.message import TextContent


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    app = FastAPI()
    app.include_router(event_router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def sample_conversation_id():
    """Return a sample conversation ID."""
    return uuid4()


@pytest.fixture
def mock_event_service():
    """Create a mock EventService for testing."""
    service = AsyncMock(spec=EventService)
    service.send_message = AsyncMock()
    return service


class TestSendMessageEndpoint:
    """Test cases for the send_message endpoint."""

    @pytest.mark.asyncio
    async def test_send_message_with_run_true(
        self, client, sample_conversation_id, mock_event_service
    ):
        """Test send_message endpoint with run=True."""
        from openhands.agent_server.dependencies import get_event_service

        # Override the dependency to return our mock
        client.app.dependency_overrides[get_event_service] = lambda: mock_event_service

        try:
            request_data = {
                "role": "user",
                "content": [{"type": "text", "text": "Hello, world!"}],
                "run": True,
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events", json=request_data
            )

            assert response.status_code == 200
            assert response.json() == {"success": True}

            # Verify send_message was called with correct parameters
            mock_event_service.send_message.assert_called_once()
            call_args = mock_event_service.send_message.call_args
            message, run_flag = call_args[0]

            assert isinstance(message, Message)
            assert message.role == "user"
            assert len(message.content) == 1
            assert isinstance(message.content[0], TextContent)
            assert message.content[0].text == "Hello, world!"
            assert run_flag is True
        finally:
            # Clean up the dependency override
            client.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_message_with_run_false(
        self, client, sample_conversation_id, mock_event_service
    ):
        """Test send_message endpoint with run=False."""
        from openhands.agent_server.dependencies import get_event_service

        # Override the dependency to return our mock
        client.app.dependency_overrides[get_event_service] = lambda: mock_event_service

        try:
            request_data = {
                "role": "assistant",
                "content": [{"type": "text", "text": "I understand."}],
                "run": False,
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events", json=request_data
            )

            assert response.status_code == 200
            assert response.json() == {"success": True}

            # Verify send_message was called with run=False
            mock_event_service.send_message.assert_called_once()
            call_args = mock_event_service.send_message.call_args
            message, run_flag = call_args[0]

            assert isinstance(message, Message)
            assert message.role == "assistant"
            assert run_flag is False
        finally:
            # Clean up the dependency override
            client.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_message_default_run_value(
        self, client, sample_conversation_id, mock_event_service
    ):
        """Test send_message endpoint with default run value."""
        from openhands.agent_server.dependencies import get_event_service

        # Override the dependency to return our mock
        client.app.dependency_overrides[get_event_service] = lambda: mock_event_service

        try:
            # Request without run field should use default value
            request_data = {
                "role": "user",
                "content": [{"type": "text", "text": "Test message"}],
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events", json=request_data
            )

            assert response.status_code == 200
            assert response.json() == {"success": True}

            # Verify send_message was called with default run value (False)
            mock_event_service.send_message.assert_called_once()
            call_args = mock_event_service.send_message.call_args
            message, run_flag = call_args[0]

            assert isinstance(message, Message)
            assert message.role == "user"
            assert run_flag is False  # Default value from SendMessageRequest
        finally:
            # Clean up the dependency override
            client.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_message_conversation_not_found(
        self, client, sample_conversation_id
    ):
        """Test send_message endpoint when conversation is not found."""
        from fastapi import HTTPException, status

        from openhands.agent_server.dependencies import get_event_service

        def raise_not_found():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation not found: {sample_conversation_id}",
            )

        # Override the dependency to raise HTTPException
        client.app.dependency_overrides[get_event_service] = raise_not_found

        try:
            request_data = {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}],
                "run": True,
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events", json=request_data
            )

            assert response.status_code == 404
        finally:
            # Clean up the dependency override
            client.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_message_with_different_content_types(
        self, client, sample_conversation_id, mock_event_service
    ):
        """Test send_message endpoint with different content types."""
        from openhands.agent_server.dependencies import get_event_service

        # Override the dependency to return our mock
        client.app.dependency_overrides[get_event_service] = lambda: mock_event_service

        try:
            # Test with multiple content items
            request_data = {
                "role": "user",
                "content": [
                    {"type": "text", "text": "First part"},
                    {"type": "text", "text": "Second part"},
                ],
                "run": False,
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events", json=request_data
            )

            assert response.status_code == 200
            assert response.json() == {"success": True}

            # Verify message content was parsed correctly
            mock_event_service.send_message.assert_called_once()
            call_args = mock_event_service.send_message.call_args
            message, run_flag = call_args[0]

            assert isinstance(message, Message)
            assert message.role == "user"
            assert len(message.content) == 2
            assert all(isinstance(content, TextContent) for content in message.content)
            text_content = cast(list[TextContent], message.content)
            assert text_content[0].text == "First part"
            assert text_content[1].text == "Second part"
            assert run_flag is False
        finally:
            # Clean up the dependency override
            client.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_message_with_system_role(
        self, client, sample_conversation_id, mock_event_service
    ):
        """Test send_message endpoint with system role."""
        from openhands.agent_server.dependencies import get_event_service

        # Override the dependency to return our mock
        client.app.dependency_overrides[get_event_service] = lambda: mock_event_service

        try:
            request_data = {
                "role": "system",
                "content": [{"type": "text", "text": "System initialization message"}],
                "run": True,
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events", json=request_data
            )

            assert response.status_code == 200
            assert response.json() == {"success": True}

            # Verify system message was processed correctly
            mock_event_service.send_message.assert_called_once()
            call_args = mock_event_service.send_message.call_args
            message, run_flag = call_args[0]

            assert isinstance(message, Message)
            assert message.role == "system"
            assert run_flag is True
        finally:
            # Clean up the dependency override
            client.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_message_invalid_request_data(
        self, client, sample_conversation_id
    ):
        """Test send_message endpoint with invalid request data."""
        from openhands.agent_server.dependencies import get_event_service

        # Override the dependency (though it shouldn't be called for validation errors)
        client.app.dependency_overrides[get_event_service] = lambda: None

        try:
            # Test with invalid role value
            invalid_role_data = {
                "role": "invalid_role",
                "content": [{"type": "text", "text": "Hello"}],
                "run": True,
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events",
                json=invalid_role_data,
            )

            assert response.status_code == 422  # Validation error

            # Test with invalid content structure
            invalid_content_data = {
                "role": "user",
                "content": "invalid_content_should_be_list",  # Should be a list
                "run": True,
            }

            response = client.post(
                f"/api/conversations/{sample_conversation_id}/events",
                json=invalid_content_data,
            )

            assert response.status_code == 422  # Validation error
        finally:
            # Clean up the dependency override
            client.app.dependency_overrides.clear()
