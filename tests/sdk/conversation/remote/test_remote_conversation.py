"""Tests for RemoteConversation."""

import uuid
from unittest.mock import Mock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.conversation.secret_registry import SecretValue
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.workspace import RemoteWorkspace


class TestRemoteConversation:
    """Test RemoteConversation functionality."""

    def setup_method(self):
        """Set up test environment."""
        self.host: str = "http://localhost:8000"
        self.llm: LLM = LLM(model="gpt-4", api_key=SecretStr("test-key"))
        self.agent: Agent = Agent(llm=self.llm, tools=[])
        self.mock_client: Mock = Mock(spec=httpx.Client)
        self.workspace: RemoteWorkspace = RemoteWorkspace(
            host=self.host, working_dir="/tmp"
        )

    def setup_mock_client(self, conversation_id: str | None = None):
        """Set up mock client for the workspace with default responses."""
        mock_client_instance = Mock()
        self.workspace._client = mock_client_instance

        # Default conversation ID
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        # Create default responses
        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        # Mock the request method to return appropriate responses
        def request_side_effect(method, url, **kwargs):
            if method == "POST" and url == "/api/conversations":
                return mock_conv_response
            elif method == "GET" and "/api/conversations/" in url and "/events" in url:
                return mock_events_response
            elif method == "GET" and url.startswith("/api/conversations/"):
                # Return conversation info response with finished status
                # (needed for run() polling to complete)
                response = Mock()
                response.raise_for_status.return_value = None
                conv_info = mock_conv_response.json.return_value.copy()
                conv_info["execution_status"] = "finished"
                response.json.return_value = conv_info
                return response
            elif method == "POST" and "/events" in url:
                # POST to events endpoint (send_message)
                response = Mock()
                response.raise_for_status.return_value = None
                response.json.return_value = {}
                return response
            elif method == "POST" and "/run" in url:
                # POST to run endpoint
                response = Mock()
                response.raise_for_status.return_value = None
                response.status_code = 200
                response.json.return_value = {}
                return response
            elif method == "POST" or method == "PUT":
                # Default success response for other POST/PUT requests
                response = Mock()
                response.raise_for_status.return_value = None
                response.json.return_value = {}
                return response
            else:
                response = Mock()
                response.raise_for_status.return_value = None
                return response

        mock_client_instance.request.side_effect = request_side_effect
        return mock_client_instance

    def create_mock_conversation_response(self, conversation_id: str | None = None):
        """Create mock conversation creation response."""
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "id": conversation_id,
            "conversation_id": conversation_id,
        }
        return mock_response

    def create_mock_events_response(self, events: list | None = None):
        """Create mock events API response."""
        if events is None:
            events = []

        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "items": events,
            "next_page_id": None,
        }
        return mock_response

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_initialization_new_conversation(self, mock_ws_client):
        """Test RemoteConversation initialization with new conversation."""
        # Set up mock client
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        # Mock WebSocket client
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create RemoteConversation
        conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
            max_iteration_per_run=100,
            stuck_detection=True,
        )

        # Verify WebSocket client was created and started
        mock_ws_client.assert_called_once()
        mock_ws_instance.start.assert_called_once()

        # Verify conversation properties
        assert conversation.id == uuid.UUID(conversation_id)
        assert conversation.workspace.host == self.host
        assert conversation.max_iteration_per_run == 100

        # Verify POST was called to create the conversation
        post_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST" and call[0][1] == "/api/conversations"
        ]
        assert len(post_calls) == 1, (
            "Should have made exactly one POST call to create conversation"
        )

        # Verify GET was called to fetch events (RemoteEventsList initialization)
        # This happens in RemoteEventsList._do_full_sync() which is called
        # during RemoteState initialization
        get_events_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "GET" and "/events/search" in call[0][1]
        ]
        assert len(get_events_calls) >= 1, (
            "Should have made at least one GET call to /events/search "
            "to fetch initial events"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_initialization_existing_conversation(
        self, mock_ws_client
    ):
        """Test RemoteConversation initialization with existing conversation."""
        # Mock the workspace client directly
        conversation_id = uuid.uuid4()
        mock_client_instance = self.setup_mock_client(
            conversation_id=str(conversation_id)
        )

        # Mock WebSocket client
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create RemoteConversation with existing ID
        conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
            conversation_id=conversation_id,
        )

        # Verify conversation ID is set correctly
        assert conversation.id == conversation_id

        # Verify no POST call was made to create a new conversation
        post_create_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST" and call[0][1] == "/api/conversations"
        ]
        assert len(post_create_calls) == 0, (
            "Should not create a new conversation when ID is provided"
        )

        # Verify GET call was made to validate existing conversation
        get_conversation_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "GET"
            and call[0][1] == f"/api/conversations/{conversation_id}"
        ]
        assert len(get_conversation_calls) == 1, (
            "Should have made exactly one GET call to validate existing conversation"
        )

        # Verify GET was called to fetch events (RemoteEventsList initialization)
        get_events_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "GET" and "/events/search" in call[0][1]
        ]
        assert len(get_events_calls) >= 1, (
            "Should have made at least one GET call to /events/search "
            "to fetch initial events"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_send_message_string(self, mock_ws_client):
        """Test sending a string message."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and send message
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.send_message("Hello, world!")

        # Verify message API call was made (the exact payload structure may vary)
        # Check that a POST was made to the events endpoint
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/events" in call[0][1]
        ]
        assert len(request_calls) >= 1, (
            "Should have made a POST call to events endpoint"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_send_message_object(self, mock_ws_client):
        """Test sending a Message object."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and send message
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)

        message = Message(
            role="user",
            content=[TextContent(text="Hello from message object!")],
        )
        conversation.send_message(message)

        # Verify message API call was made (the exact payload structure may vary)
        # Check that a POST was made to the events endpoint
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/events" in call[0][1]
        ]
        assert len(request_calls) >= 1, (
            "Should have made a POST call to events endpoint"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_send_message_invalid_role(self, mock_ws_client):
        """Test sending a message with invalid role raises assertion error."""
        # Setup mocks
        mock_client_instance = self.setup_mock_client()

        conversation_id = str(uuid.uuid4())
        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        mock_client_instance.post.return_value = mock_conv_response
        mock_client_instance.get.return_value = mock_events_response

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)

        # Try to send message with invalid role
        invalid_message = Message(
            role="assistant",  # Only "user" role is allowed
            content=[TextContent(text="Invalid role message")],
        )

        with pytest.raises(AssertionError, match="Only user messages are allowed"):
            conversation.send_message(invalid_message)

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run(self, mock_ws_client):
        """Test running the conversation."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.run()

        # Verify run API call
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/run" in call[0][1]
        ]
        assert len(request_calls) >= 1, "Should have made a POST call to run endpoint"

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_already_running(self, mock_ws_client):
        """Test running when conversation is already running (409 response)."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        # Override the default request side_effect to return 409 for /run endpoint
        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "POST" and "/run" in url:
                mock_run_response = Mock()
                mock_run_response.status_code = 409  # Already running
                mock_run_response.raise_for_status.return_value = None
                return mock_run_response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        # With blocking=True (default), it will poll until finished
        conversation.run()  # Should not raise an exception

        # Verify run API call was made
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/run" in call[0][1]
        ]
        assert len(request_calls) >= 1, "Should have made a POST call to run endpoint"

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_non_blocking(self, mock_ws_client):
        """Test running the conversation with blocking=False returns immediately."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run with blocking=False
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.run(blocking=False)

        # Verify run API call was made
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/run" in call[0][1]
        ]
        assert len(request_calls) == 1, "Should have made exactly one POST call"

        # Verify NO polling GET calls were made (only the initial events fetch)
        get_conversation_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "GET"
            and call[0][1] == f"/api/conversations/{conversation_id}"
        ]
        # Should be 0 because blocking=False skips polling
        assert len(get_conversation_calls) == 0, (
            "Should not poll for status when blocking=False"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_blocking_polls_until_finished(
        self, mock_ws_client
    ):
        """Test that blocking=True polls until status is not running."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        # Track poll count and return "running" for first 2 polls, then "finished"
        poll_count = [0]
        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                poll_count[0] += 1
                response = Mock()
                response.raise_for_status.return_value = None
                if poll_count[0] <= 2:
                    response.json.return_value = {
                        "id": conversation_id,
                        "execution_status": "running",
                    }
                else:
                    response.json.return_value = {
                        "id": conversation_id,
                        "execution_status": "finished",
                    }
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run with blocking=True
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.run(blocking=True, poll_interval=0.01)  # Fast polling for test

        # Verify polling happened multiple times
        assert poll_count[0] == 3, (
            f"Should have polled 3 times (2 running + 1 finished), got {poll_count[0]}"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_timeout(self, mock_ws_client):
        """Test that run() raises ConversationRunError on timeout."""
        from openhands.sdk.conversation.exceptions import ConversationRunError

        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        # Always return "running" status to trigger timeout
        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                response = Mock()
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": conversation_id,
                    "execution_status": "running",
                }
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run with very short timeout
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)

        with pytest.raises(ConversationRunError) as exc_info:
            conversation.run(blocking=True, poll_interval=0.01, timeout=0.05)

        # Verify the error contains timeout information
        assert "timed out" in str(exc_info.value).lower()

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_set_confirmation_policy(self, mock_ws_client):
        """Test setting confirmation policy."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and set policy
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        policy = AlwaysConfirm()
        conversation.set_confirmation_policy(policy)

        # Verify policy API call
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/confirmation_policy"
            in call[0][1]
        ]
        assert len(request_calls) >= 1, (
            "Should have made a POST call to confirmation_policy endpoint"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_reject_pending_actions(self, mock_ws_client):
        """Test rejecting pending actions."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and reject actions
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.reject_pending_actions("Custom rejection reason")

        # Verify reject API call
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/events/respond_to_confirmation"
            in call[0][1]
        ]
        assert len(request_calls) >= 1, (
            "Should have made a POST call to respond_to_confirmation endpoint"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_pause(self, mock_ws_client):
        """Test pausing the conversation."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and pause
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.pause()

        # Verify pause API call
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/pause" in call[0][1]
        ]
        assert len(request_calls) >= 1, "Should have made a POST call to pause endpoint"

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_update_secrets(self, mock_ws_client):
        """Test updating secrets."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and update secrets
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)

        # Test with string secrets
        from typing import cast

        from openhands.sdk.conversation.secret_registry import SecretValue

        secrets = cast(
            dict[str, SecretValue],
            {
                "api_key": "secret_value",
                "token": "another_secret",
            },
        )
        conversation.update_secrets(secrets)

        # Verify secrets API call
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/secrets" in call[0][1]
        ]
        assert len(request_calls) >= 1, (
            "Should have made a POST call to secrets endpoint"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_update_secrets_callable(self, mock_ws_client):
        """Test updating secrets with callable values."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and update secrets with callable
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)

        def get_secret():
            return "callable_secret_value"

        secrets: dict[str, SecretValue] = {
            "api_key": "string_secret",
            "callable_secret": get_secret,  # type: ignore[dict-item]
        }
        conversation.update_secrets(secrets)

        # Verify secrets API call with resolved callable
        request_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/secrets" in call[0][1]
        ]
        assert len(request_calls) >= 1, (
            "Should have made a POST call to secrets endpoint"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_close(self, mock_ws_client):
        """Test closing the conversation."""
        # Setup mocks
        mock_client_instance = self.setup_mock_client()

        conversation_id = str(uuid.uuid4())
        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        mock_client_instance.post.return_value = mock_conv_response
        mock_client_instance.get.return_value = mock_events_response

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and close
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.close()

        # Verify WebSocket client was stopped
        mock_ws_instance.stop.assert_called_once()

        # Verify HTTP client was NOT closed because it's shared with the workspace.
        # The workspace owns the client and will close it during its own cleanup.
        mock_client_instance.close.assert_not_called()

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_stuck_detector_not_implemented(self, mock_ws_client):
        """Test that stuck_detector property raises NotImplementedError."""
        # Setup mocks
        mock_client_instance = self.setup_mock_client()

        conversation_id = str(uuid.uuid4())
        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        mock_client_instance.post.return_value = mock_conv_response
        mock_client_instance.get.return_value = mock_events_response

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)

        # Accessing stuck_detector should raise NotImplementedError
        with pytest.raises(
            NotImplementedError, match="stuck detection is not available"
        ):
            _ = conversation.stuck_detector

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_with_callbacks(self, mock_ws_client):
        """Test RemoteConversation with custom callbacks."""
        # Setup mocks
        mock_client_instance = self.setup_mock_client()

        conversation_id = str(uuid.uuid4())
        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        mock_client_instance.post.return_value = mock_conv_response
        mock_client_instance.get.return_value = mock_events_response

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create custom callback
        callback_calls = []

        def custom_callback(event):
            callback_calls.append(event)

        # Create conversation with callback
        _conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
            callbacks=[custom_callback],
        )

        # Verify WebSocket client was created with callback
        # The callback should be a composed callback that includes the custom callback
        mock_ws_client.assert_called_once()
        call_args = mock_ws_client.call_args
        assert "callback" in call_args[1]  # Should have a callback parameter

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_with_visualize(self, mock_ws_client):
        """Test RemoteConversation with visualizer=DefaultConversationVisualizer()."""
        # Setup mocks
        mock_client_instance = self.setup_mock_client()

        conversation_id = str(uuid.uuid4())
        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        mock_client_instance.post.return_value = mock_conv_response
        mock_client_instance.get.return_value = mock_events_response

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create a custom visualizer instance
        custom_visualizer = DefaultConversationVisualizer()

        # Create conversation with visualizer=DefaultConversationVisualizer()
        conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
            visualizer=custom_visualizer,
        )

        # Verify the custom visualizer instance is used directly
        assert conversation._visualizer is custom_visualizer

        # Verify the visualizer's on_event callback is in the callbacks list
        assert custom_visualizer.on_event in conversation._callbacks

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_host_url_normalization(self, mock_ws_client):
        """Test that host URL is normalized correctly."""
        # Setup mocks
        mock_client_instance = self.setup_mock_client()

        conversation_id = str(uuid.uuid4())
        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        mock_client_instance.post.return_value = mock_conv_response
        mock_client_instance.get.return_value = mock_events_response

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Test with trailing slash
        host_with_slash = "http://localhost:8000/"
        workspace_with_slash = RemoteWorkspace(host=host_with_slash, working_dir="/tmp")
        workspace_with_slash._client = mock_client_instance
        conversation = RemoteConversation(
            agent=self.agent, workspace=workspace_with_slash
        )

        # Verify trailing slash was removed and workspace host was normalized
        assert conversation.workspace.host == "http://localhost:8000"
