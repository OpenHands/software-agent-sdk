"""Tests for RemoteConversation observability span management.

This test verifies that RemoteConversation does NOT start/end observability spans
on the client side. For remote conversations, the observability span should be
managed on the server side (in EventService via LocalConversation) to ensure
proper session_id tracking in Laminar/OTEL tracing.

See issue #1390 for more details.
"""

import uuid
from unittest.mock import Mock, patch

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import RemoteWorkspace


class TestRemoteConversationObservability:
    """Test that RemoteConversation does not manage observability spans."""

    def setup_method(self):
        """Set up test environment."""
        self.host: str = "http://localhost:8000"
        self.llm: LLM = LLM(model="gpt-4", api_key=SecretStr("test-key"))
        self.agent: Agent = Agent(llm=self.llm, tools=[])
        self.workspace: RemoteWorkspace = RemoteWorkspace(
            host=self.host, working_dir="/tmp"
        )

    def setup_mock_client(self, conversation_id: str | None = None):
        """Set up mock client for the workspace with default responses."""
        mock_client_instance = Mock()
        self.workspace._client = mock_client_instance

        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        mock_conv_response = Mock()
        mock_conv_response.status_code = 200
        mock_conv_response.raise_for_status.return_value = None
        mock_conv_response.json.return_value = {
            "id": conversation_id,
            "conversation_id": conversation_id,
        }

        mock_events_response = Mock()
        mock_events_response.status_code = 200
        mock_events_response.raise_for_status.return_value = None
        mock_events_response.json.return_value = {
            "items": [],
            "next_page_id": None,
        }

        def request_side_effect(method, url, **kwargs):
            if method == "POST" and url == "/api/conversations":
                return mock_conv_response
            elif method == "GET" and "/api/conversations/" in url and "/events" in url:
                return mock_events_response
            elif method == "GET" and url.startswith("/api/conversations/"):
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                conv_info = mock_conv_response.json.return_value.copy()
                conv_info["execution_status"] = "finished"
                response.json.return_value = conv_info
                return response
            else:
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {}
                return response

        mock_client_instance.request.side_effect = request_side_effect
        return mock_client_instance

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    @patch("openhands.sdk.conversation.base.start_active_span")
    @patch("openhands.sdk.conversation.base.should_enable_observability")
    def test_remote_conversation_does_not_start_observability_span(
        self, mock_should_enable, mock_start_span, mock_ws_client
    ):
        """Test that RemoteConversation does NOT call start_active_span.

        For remote conversations, the observability span should be started on
        the server side (in EventService via LocalConversation), not on the
        client side. This ensures proper session_id tracking in Laminar/OTEL.
        """
        # Enable observability
        mock_should_enable.return_value = True

        # Set up mock client
        conversation_id = str(uuid.uuid4())
        self.setup_mock_client(conversation_id=conversation_id)

        # Mock WebSocket client
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create RemoteConversation
        conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
        )

        # Verify start_active_span was NOT called
        mock_start_span.assert_not_called()

        # Clean up
        conversation.close()

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    @patch("openhands.sdk.conversation.base.end_active_span")
    @patch("openhands.sdk.conversation.base.should_enable_observability")
    def test_remote_conversation_does_not_end_observability_span_on_close(
        self, mock_should_enable, mock_end_span, mock_ws_client
    ):
        """Test that RemoteConversation does NOT call end_active_span on close.

        For remote conversations, the observability span should be ended on
        the server side (in EventService via LocalConversation), not on the
        client side.
        """
        # Enable observability
        mock_should_enable.return_value = True

        # Set up mock client
        conversation_id = str(uuid.uuid4())
        self.setup_mock_client(conversation_id=conversation_id)

        # Mock WebSocket client
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create RemoteConversation
        conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
        )

        # Close the conversation
        conversation.close()

        # Verify end_active_span was NOT called
        mock_end_span.assert_not_called()

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    @patch("openhands.sdk.conversation.base.end_active_span")
    @patch("openhands.sdk.conversation.base.start_active_span")
    @patch("openhands.sdk.conversation.base.should_enable_observability")
    def test_remote_conversation_no_span_operations_when_observability_disabled(
        self, mock_should_enable, mock_start_span, mock_end_span, mock_ws_client
    ):
        """Test that no span operations occur when observability is disabled."""
        # Disable observability
        mock_should_enable.return_value = False

        # Set up mock client
        conversation_id = str(uuid.uuid4())
        self.setup_mock_client(conversation_id=conversation_id)

        # Mock WebSocket client
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create RemoteConversation
        conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
        )

        # Close the conversation
        conversation.close()

        # Verify no span operations were called
        mock_start_span.assert_not_called()
        mock_end_span.assert_not_called()
