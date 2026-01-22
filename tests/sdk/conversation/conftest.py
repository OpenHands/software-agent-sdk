"""Shared test fixtures for conversation tests."""

from unittest.mock import Mock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_websocket_client():
    """Auto-mock WebSocketCallbackClient for all conversation tests.

    This prevents tests from blocking on WebSocket connection attempts
    when testing RemoteConversation functionality.
    """
    with patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    ) as mock_ws_class:
        mock_ws_instance = Mock()
        mock_ws_instance.wait_for_connection.return_value = True
        mock_ws_class.return_value = mock_ws_instance
        yield mock_ws_class


def create_mock_http_client(conversation_id: str | None = None):
    """Create a comprehensive mock HTTP client for RemoteConversation.

    This helper creates a mock httpx.Client that properly handles both
    POST and GET requests with appropriate mock responses.

    Args:
        conversation_id: Optional specific conversation ID to use for mocking.
                        If not provided, a fixed test ID will be used.
    """
    # Use a fixed conversation ID for testing if not provided
    if conversation_id is None:
        conversation_id = "12345678-1234-5678-9abc-123456789abc"

    mock_client = Mock()

    # Mock POST response for conversation creation
    mock_post_response = Mock()
    mock_post_response.raise_for_status.return_value = None
    mock_post_response.json.return_value = {"id": conversation_id}

    # Mock GET response for events sync
    mock_get_response = Mock()
    mock_get_response.raise_for_status.return_value = None
    mock_get_response.json.return_value = {"items": []}

    # Configure the request method to return appropriate responses
    def mock_request(method, url, **kwargs):
        if method == "POST":
            return mock_post_response
        elif method == "GET":
            return mock_get_response
        else:
            # Default response
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {}
            return response

    mock_client.request = Mock(side_effect=mock_request)
    mock_client.post = Mock(return_value=mock_post_response)
    mock_client.get = Mock(return_value=mock_get_response)

    return mock_client
