"""Tests for RemoteConversation."""

import time
import uuid
from unittest.mock import Mock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.conversation.secret_registry import SecretValue
from openhands.sdk.conversation.visualizer import DefaultConversationVisualizer
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_state import (
    FULL_STATE_KEY,
    ConversationStateUpdateEvent,
)
from openhands.sdk.event.llm_completion_log import LLMCompletionLogEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.workspace import RemoteWorkspace


class TestRemoteConversation:
    """Test RemoteConversation functionality."""

    def setup_method(self):
        """Set up test environment."""
        self.host: str = "http://localhost:8000"
        self.llm: LLM = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"))
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
                response.status_code = 200
                response.raise_for_status.return_value = None
                conv_info = mock_conv_response.json.return_value.copy()
                conv_info["execution_status"] = "finished"
                response.json.return_value = conv_info
                return response
            elif method == "POST" and "/events" in url:
                # POST to events endpoint (send_message)
                response = Mock()
                response.status_code = 200
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
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {}
                return response
            else:
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                return response

        mock_client_instance.request.side_effect = request_side_effect
        return mock_client_instance

    def create_mock_conversation_response(self, conversation_id: str | None = None):
        """Create mock conversation creation response."""
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        mock_response = Mock()
        mock_response.status_code = 200
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
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "items": events,
            "next_page_id": None,
        }
        return mock_response

    @staticmethod
    def full_state_event(status: str, **values):
        return ConversationStateUpdateEvent(
            key=FULL_STATE_KEY,
            value={"execution_status": status, **values},
        )

    def install_post_run_full_state(
        self,
        mock_client_instance,
        conversation_id: str,
        status: str = "finished",
        **values,
    ):
        """Install a side effect that fires a full-state WS event on the first
        REST GET poll after POST /run.

        The event is fired from the GET side effect (inside
        _wait_for_run_completion, after _run_armed is set) rather than from the
        POST side effect. Firing from POST races _run_armed.set(), which follows
        the POST return, so the event would be silently discarded by
        run_complete_callback's arming guard.
        """
        ws_callback = [lambda event: None]
        original_side_effect = mock_client_instance.request.side_effect
        fired = [False]

        def custom_side_effect(method, url, **kwargs):
            resp = original_side_effect(method, url, **kwargs)
            if (
                not fired[0]
                and method == "GET"
                and url == f"/api/conversations/{conversation_id}"
            ):
                fired[0] = True
                ws_callback[0](self.full_state_event(status, **values))
            return resp

        mock_client_instance.request.side_effect = custom_side_effect
        return ws_callback

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
    def test_remote_conversation_sends_observability_fields(self, mock_ws_client):
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        mock_ws_client.return_value = Mock()

        RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
            observability_metadata={"repo": "OpenHands/software-agent-sdk"},
            observability_tags=["sdk", "remote"],
            observability_span_name="pr_review_evaluation",
            user_id="test-user-42",
        )

        create_call = next(
            (
                call
                for call in mock_client_instance.request.call_args_list
                if call[0][0] == "POST" and call[0][1] == "/api/conversations"
            ),
            None,
        )
        assert create_call is not None, "No POST /api/conversations call found"
        payload = create_call.kwargs["json"]
        assert payload["observability_metadata"] == {
            "repo": "OpenHands/software-agent-sdk"
        }
        assert payload["observability_tags"] == ["sdk", "remote"]
        assert payload["observability_span_name"] == "pr_review_evaluation"
        assert payload["user_id"] == "test-user-42"

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_plugin_source_redacted_placeholder_kept(
        self, mock_ws_client
    ):
        """The create payload masks inline plugin-source creds but keeps ${VAR}
        placeholders, so the server clones private plugins via secret expansion
        without raw credentials crossing the wire."""
        from openhands.sdk.plugin import PluginSource

        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        mock_ws_client.return_value = Mock()

        placeholder = "https://x-token-auth:${MY_TOKEN}@host/org/repo.git"
        RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
            plugins=[
                PluginSource(source="https://oauth2:LEAKME@host/org/priv.git"),
                PluginSource(source=placeholder),
            ],
        )

        create_call = next(
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST" and call[0][1] == "/api/conversations"
        )
        sources = [p["source"] for p in create_call.kwargs["json"]["plugins"]]
        assert "https://****@host/org/priv.git" in sources
        assert placeholder in sources
        assert "LEAKME" not in str(create_call.kwargs["json"]["plugins"])

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_user_id_none_sends_explicit_null(self, mock_ws_client):
        """user_id=None sends an explicit null key (not omitted) so the server
        receives a consistent payload regardless of whether user_id was supplied."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        mock_ws_client.return_value = Mock()

        RemoteConversation(agent=self.agent, workspace=self.workspace)

        create_call = next(
            (
                call
                for call in mock_client_instance.request.call_args_list
                if call[0][0] == "POST" and call[0][1] == "/api/conversations"
            ),
            None,
        )
        assert create_call is not None, "No POST /api/conversations call found"
        payload = create_call.kwargs["json"]
        assert "user_id" in payload
        assert payload["user_id"] is None

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_llm_completion_log_callback_writes_utf8(self, mock_ws_client, tmp_path):
        llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            log_completions=True,
            log_completions_folder=str(tmp_path),
        )
        agent = Agent(llm=llm, tools=[])
        conversation_id = str(uuid.uuid4())
        self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_client.return_value = Mock()
        conversation = RemoteConversation(agent=agent, workspace=self.workspace)
        callback = conversation._create_llm_completion_log_callback()

        real_open = open
        open_calls = []

        def capture_open(*args, **kwargs):
            open_calls.append((args, kwargs))
            return real_open(*args, **kwargs)

        event = LLMCompletionLogEvent(
            filename="completion.json",
            log_data='{"message": "hello 🔐"}',
            usage_id=llm.usage_id,
        )
        with patch("builtins.open", side_effect=capture_open):
            callback(event)

        assert open_calls
        assert open_calls[0][1]["encoding"] == "utf-8"
        assert (tmp_path / "completion.json").read_text(encoding="utf-8") == (
            event.log_data
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_acp_remote_conversation_uses_unified_endpoint(self, mock_ws_client):
        acp_agent = ACPAgent(acp_command=["echo", "test"])
        conversation_id = str(uuid.uuid4())
        mock_client_instance = Mock()
        self.workspace._client = mock_client_instance

        mock_conv_response = self.create_mock_conversation_response(conversation_id)
        mock_events_response = self.create_mock_events_response()

        def request_side_effect(method, url, **kwargs):
            if method == "POST" and url == "/api/conversations":
                return mock_conv_response
            if method == "GET" and "/api/conversations/" in url and "/events" in url:
                return mock_events_response
            if method == "GET" and url.startswith("/api/conversations/"):
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                conv_info = mock_conv_response.json.return_value.copy()
                conv_info["execution_status"] = "finished"
                conv_info["agent"] = {
                    "kind": "ACPAgent",
                    "acp_command": ["echo", "test"],
                }
                response.json.return_value = conv_info
                return response
            response = Mock()
            response.status_code = 200
            response.raise_for_status.return_value = None
            response.json.return_value = {}
            return response

        mock_client_instance.request.side_effect = request_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        RemoteConversation(agent=acp_agent, workspace=self.workspace)

        post_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST" and call[0][1] == "/api/conversations"
        ]
        assert len(post_calls) == 1

        get_events_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "GET" and "/api/conversations/" in call[0][1]
        ]
        assert len(get_events_calls) >= 1

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
    def test_remote_conversation_initialization_nonexistent_conversation_creates_new(
        self, mock_ws_client
    ):
        """Test RemoteConversation creates conversation when ID doesn't exist."""
        conversation_id = uuid.uuid4()
        mock_client_instance = Mock()
        self.workspace._client = mock_client_instance

        mock_conv_response = self.create_mock_conversation_response(
            str(conversation_id)
        )
        mock_events_response = self.create_mock_events_response()

        def request_side_effect(method, url, **kwargs):
            # GET for specific conversation returns 404
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                response = Mock()
                response.status_code = 404
                response.raise_for_status.side_effect = None
                return response
            elif method == "POST" and url == "/api/conversations":
                return mock_conv_response
            elif method == "GET" and "/events/search" in url:
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

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create RemoteConversation with a non-existent ID
        conversation = RemoteConversation(
            agent=self.agent,
            workspace=self.workspace,
            conversation_id=conversation_id,
        )

        # Verify conversation ID is set correctly
        assert conversation.id == conversation_id

        # Verify GET call was made to check if conversation exists
        get_conversation_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "GET"
            and call[0][1] == f"/api/conversations/{conversation_id}"
        ]
        assert len(get_conversation_calls) == 1, (
            "Should have made exactly one GET call to check if conversation exists"
        )

        # Verify POST call was made to create the conversation
        post_create_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST" and call[0][1] == "/api/conversations"
        ]
        assert len(post_create_calls) == 1, (
            "Should have made exactly one POST call to create the conversation"
        )

        # Verify the POST payload contains the conversation_id
        post_call = post_create_calls[0]
        payload = post_call[1].get("json", {})
        assert payload.get("conversation_id") == str(conversation_id), (
            "POST payload should contain the specified conversation_id"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_existing_different_agent_kind_raises_clear_error(
        self, mock_ws_client
    ):
        conversation_id = uuid.uuid4()
        mock_client_instance = Mock()
        self.workspace._client = mock_client_instance

        def request_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": str(conversation_id),
                    "execution_status": "idle",
                    "agent": {
                        "kind": "ACPAgent",
                        "acp_command": ["echo", "test"],
                    },
                }
                return response
            response = Mock()
            response.status_code = 200
            response.raise_for_status.return_value = None
            response.json.return_value = {}
            return response

        mock_client_instance.request.side_effect = request_side_effect

        with pytest.raises(ValueError, match="different agent kind"):
            RemoteConversation(
                agent=self.agent,
                workspace=self.workspace,
                conversation_id=conversation_id,
            )

        mock_ws_client.assert_not_called()
        post_create_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
        ]
        assert post_create_calls == []

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
        "openhands.sdk.conversation.impl.remote_conversation.generate_conversation_title"
    )
    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_generate_title_reconciles_locally(
        self, mock_ws_client, mock_generate_title
    ):
        """generate_title uses reconciled local events instead of a REST endpoint."""
        conversation_id = str(uuid.uuid4())
        user_event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="Hello from remote title")]
            ),
        )
        synced_events: list[dict] = []

        mock_client_instance = Mock()
        self.workspace._client = mock_client_instance
        mock_conv_response = self.create_mock_conversation_response(conversation_id)

        def request_side_effect(method, url, **kwargs):
            if method == "POST" and url == "/api/conversations":
                return mock_conv_response
            if (
                method == "GET"
                and "/api/conversations/" in url
                and "/events/search" in url
            ):
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "items": list(synced_events),
                    "next_page_id": None,
                }
                return response
            if method == "GET" and url.startswith("/api/conversations/"):
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                conv_info = mock_conv_response.json.return_value.copy()
                conv_info["execution_status"] = "finished"
                response.json.return_value = conv_info
                return response
            if method == "POST" and url.endswith("/events"):
                synced_events[:] = [user_event.model_dump(mode="json")]
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {}
                return response
            response = Mock()
            response.status_code = 200
            response.raise_for_status.return_value = None
            response.json.return_value = {}
            return response

        mock_client_instance.request.side_effect = request_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance
        mock_generate_title.return_value = "Remote title"

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.send_message("Hello from remote title")

        title = conversation.generate_title(max_length=60)

        assert title == "Remote title"
        mock_generate_title.assert_called_once()
        call_kwargs = mock_generate_title.call_args.kwargs
        assert call_kwargs["llm"] == self.agent.llm
        assert call_kwargs["max_length"] == 60
        reconciled_events = list(call_kwargs["events"])
        assert len(reconciled_events) == 1
        assert (
            reconciled_events[0].llm_message.content[0].text
            == "Hello from remote title"
        )
        assert not any(
            call[0][0] == "POST" and call[0][1].endswith("/generate_title")
            for call in mock_client_instance.request.call_args_list
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run(self, mock_ws_client):
        """Test running the conversation."""
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        ws_callback = self.install_post_run_full_state(
            mock_client_instance, conversation_id
        )

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]
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
        ws_callback = [lambda event: None]

        # Override the default request side_effect to return 409 for /run endpoint.
        # The full-state completion event fires on the first GET poll (after arming)
        # rather than inline with the POST, since _run_armed is set after POST returns.
        original_side_effect = mock_client_instance.request.side_effect
        fired = [False]

        def custom_side_effect(method, url, **kwargs):
            if method == "POST" and "/run" in url:
                mock_run_response = Mock()
                mock_run_response.status_code = 409  # Already running
                mock_run_response.raise_for_status.return_value = None
                return mock_run_response
            resp = original_side_effect(method, url, **kwargs)
            if (
                not fired[0]
                and method == "GET"
                and url == f"/api/conversations/{conversation_id}"
            ):
                fired[0] = True
                ws_callback[0](self.full_state_event("finished"))
            return resp

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]
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
        """Test that blocking=True waits for the post-run state snapshot.

        REST FINISHED is only a health signal; the server's full-state
        ConversationStateUpdateEvent is the authoritative run-complete signal.
        """
        # Setup mocks
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        # Track poll count and return "running" for first 2 polls, then "finished"
        poll_count = [0]
        original_side_effect = mock_client_instance.request.side_effect
        ws_callback = [lambda event: None]

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
                    if poll_count[0] == 5:
                        ws_callback[0](self.full_state_event("finished"))
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Create conversation and run with blocking=True
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]
        conversation.run(blocking=True, poll_interval=0.01)  # Fast polling for test

        # Verify REST FINISHED alone did not complete the run; the run returned
        # only after the post-run full-state snapshot was delivered.
        assert poll_count[0] == 5, (
            f"Should have polled until the post-run snapshot arrived, got "
            f"{poll_count[0]} poll(s)"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_returns_on_waiting_for_confirmation_snapshot(
        self, mock_ws_client
    ):
        """A post-run non-running full-state snapshot completes blocking run()."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        ws_callback = [lambda event: None]
        original_side_effect = mock_client_instance.request.side_effect
        first_poll = [True]

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                if first_poll[0]:
                    # Fire the full-state event on the first REST poll, which runs
                    # inside _wait_for_run_completion() after _run_armed is set.
                    first_poll[0] = False
                    ws_callback[0](self.full_state_event("waiting_for_confirmation"))
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": conversation_id,
                    "execution_status": "waiting_for_confirmation",
                    "stats": {"usage_to_metrics": {}},
                }
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]

        conversation.run(blocking=True, poll_interval=0.01)

        assert conversation.state.execution_status.value == "waiting_for_confirmation"

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_preserves_post_run_snapshot_after_running_poll(
        self, mock_ws_client
    ):
        """An in-flight RUNNING REST poll must not discard a post-run snapshot."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        ws_callback = [lambda event: None]
        poll_count = [0]
        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                poll_count[0] += 1
                ws_callback[0](self.full_state_event("finished"))
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": conversation_id,
                    "execution_status": "running",
                    "stats": {"usage_to_metrics": {}},
                }
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]

        conversation.run(blocking=True, poll_interval=0.01, timeout=0.1)

        assert poll_count[0] == 1

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_ws_finished_is_only_a_hint_not_terminal(
        self, mock_ws_client
    ):
        """A WS-delivered FINISHED status must NOT terminate ``run()`` on its own.

        Regression test for the stop-hook race we observed in retry-16
        (run 25497962453, conversation dd86d184…, agourlay/zip-password-finder):

        Server-side timeline within a single ``LocalConversation.run`` loop:
          1. ``agent.step()`` sets ``execution_status = FINISHED``; that
             status update event is broadcast over the WebSocket.
          2. **Lock released** at end of iteration. Client observes
             FINISHED via WS.
          3. Next loop iteration acquires lock, runs stop hooks, hook
             returns rc=2, status reverts to RUNNING, ``continue``.

        With the old implementation, step 2 caused the client's
        ``_wait_for_run_completion`` to ``return`` immediately on the
        WS-delivered FINISHED — racing the server's hook eval and tearing
        down the agent-server pod (via ``workspace_keepalive`` exit) before
        the agent could consume its iteration budget.

        The fix: per-field WS FINISHED is ignored for completion. Only the
        post-run full-state snapshot is authoritative.
        """
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        # REST poll script: the first 3 polls show the server has flipped
        # *back* to RUNNING (the stop-hook revert); subsequent polls show
        # the agent's second finish, which should be honored.
        rest_script = [
            "running",
            "running",
            "running",
            "finished",
            "finished",
            "finished",
            "finished",
        ]
        poll_count = [0]
        original_side_effect = mock_client_instance.request.side_effect
        ws_callback = [lambda event: None]

        def custom_side_effect(method, url, **kwargs):
            if method == "POST" and url == f"/api/conversations/{conversation_id}/run":
                response = original_side_effect(method, url, **kwargs)
                ws_callback[0](
                    ConversationStateUpdateEvent(
                        key="execution_status", value="finished"
                    )
                )
                return response
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                idx = min(poll_count[0], len(rest_script) - 1)
                status = rest_script[idx]
                poll_count[0] += 1
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": conversation_id,
                    "execution_status": status,
                    "stats": {"usage_to_metrics": {}},
                }
                if poll_count[0] >= len(rest_script):
                    ws_callback[0](self.full_state_event("finished"))
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]

        conversation.run(blocking=True, poll_interval=0.01)

        # Must have polled past the 3 RUNNING REST responses (race window),
        # then waited for the post-run full-state snapshot. Pre-fix this would
        # have returned on the WS FINISHED injected after the /run trigger with
        # poll_count == 0.
        assert poll_count[0] == len(rest_script), (
            f"Run() returned before the post-run snapshot. poll_count={poll_count[0]}"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_rest_finished_revert_waits_for_full_state(
        self, mock_ws_client
    ):
        """Do not return from REST FINISHED when a hook can still veto it."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        rest_script = [
            "finished",
            "finished",
            "finished",
            "running",
            "running",
            "finished",
            "finished",
            "finished",
            "finished",
        ]
        poll_count = [0]
        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                idx = min(poll_count[0], len(rest_script) - 1)
                status = rest_script[idx]
                poll_count[0] += 1
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": conversation_id,
                    "execution_status": status,
                    "stats": {"usage_to_metrics": {}},
                }
                if poll_count[0] >= len(rest_script):
                    ws_callback[0](self.full_state_event("finished"))
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance
        ws_callback = [lambda event: None]

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]

        conversation.run(blocking=True, poll_interval=0.01)

        assert poll_count[0] >= len(rest_script), (
            f"Run() returned before the post-run full-state snapshot. "
            f"poll_count={poll_count[0]}"
        )

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_ws_error_still_terminates_immediately(
        self, mock_ws_client
    ):
        """ERROR via WS still raises immediately (not subject to hook reverts)."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_client.return_value = Mock()
        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation._get_last_error_detail = Mock(return_value="boom")
        ws_callback = mock_ws_client.call_args.kwargs["callback"]

        original_side_effect = mock_client_instance.request.side_effect

        def post_run_seeds_error(method, url, **kwargs):
            resp = original_side_effect(method, url, **kwargs)
            if method == "POST" and url.endswith("/run"):
                ws_callback(
                    ConversationStateUpdateEvent(key="execution_status", value="error")
                )
            return resp

        mock_client_instance.request.side_effect = post_run_seeds_error

        with pytest.raises(Exception) as excinfo:
            conversation.run(blocking=True, poll_interval=10.0)

        assert "boom" in str(excinfo.value) or "error" in str(excinfo.value).lower()

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_stale_pre_run_snapshot_is_ignored(
        self, mock_ws_client
    ):
        """A full-state WS snapshot received before run() POST must not complete run().

        The WS subscription delivers an initial full-state snapshot during
        connect(). If that snapshot carries a non-RUNNING status (e.g. "idle"),
        it must NOT be treated as the post-run completion signal — _run_armed
        is not yet set at that point. run() should only complete once a
        full-state snapshot arrives after the POST /run call.
        """
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        ws_callback = [lambda event: None]
        original_side_effect = mock_client_instance.request.side_effect
        poll_count = [0]

        def custom_side_effect(method, url, **kwargs):
            resp = original_side_effect(method, url, **kwargs)
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                poll_count[0] += 1
                if poll_count[0] == 1:
                    # Fire the real post-run snapshot on the first REST poll (armed).
                    ws_callback[0](self.full_state_event("finished"))
            return resp

        mock_client_instance.request.side_effect = custom_side_effect
        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]

        # Inject a stale "idle" snapshot directly into the queue as if it
        # arrived from the initial subscription, before run() is called.
        # _run_armed is not set yet, so run_complete_callback would discard it,
        # but simulating a direct queue put lets us verify the guard works end-to-end.
        ws_callback[0](self.full_state_event("idle"))
        assert conversation._terminal_status_queue.empty(), (
            "Stale pre-run snapshot must not enter the queue (_run_armed not set)"
        )

        # run() should complete via the first REST poll's full-state event, not
        # the stale pre-run snapshot.
        conversation.run(blocking=True, poll_interval=0.01)
        assert poll_count[0] >= 1

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_rest_hard_fallback_when_ws_silent(
        self, mock_ws_client
    ):
        """run() completes via REST hard-fallback when WS snapshot never arrives.

        When the post-run WS full-state snapshot is never delivered (e.g. socket
        dropped after the run finished), the client should not hang until the
        overall timeout. After TERMINAL_HARD_FALLBACK_SECS of consecutive REST
        terminal polls it must accept the status and return.

        time.monotonic is patched to advance 10 s per call so the 30 s threshold
        is crossed after ~3 REST polls (real wall time ~poll_interval * 3).
        """
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)
        poll_count = [0]
        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            resp = original_side_effect(method, url, **kwargs)
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                poll_count[0] += 1
                # Never fire the post-run WS snapshot — simulate silent socket.
            return resp

        mock_client_instance.request.side_effect = custom_side_effect
        mock_ws_client.return_value = Mock()

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)

        # Patch time.monotonic to advance 10 s per call so the 30 s hard-fallback
        # threshold is crossed after ~3 REST polls.
        call_counter = [0]
        base = time.monotonic()

        def fast_monotonic() -> float:
            call_counter[0] += 1
            return base + call_counter[0] * 10.0

        with patch(
            "openhands.sdk.conversation.impl.remote_conversation.time.monotonic",
            side_effect=fast_monotonic,
        ):
            conversation.run(blocking=True, poll_interval=0.01)

        assert poll_count[0] >= 1, f"Expected at least 1 REST poll, got {poll_count[0]}"

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_full_state_updates_cached_state(
        self, mock_ws_client
    ):
        """Post-run full-state snapshots update cached state before run() returns."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        stale_info = {
            "id": conversation_id,
            "execution_status": "finished",
            "stats": {"usage_to_metrics": {}},
        }
        final_info = {
            "id": conversation_id,
            "execution_status": "finished",
            "stats": {
                "usage_to_metrics": {
                    "test-llm": {
                        "model_name": "gpt-4o-mini",
                        "accumulated_cost": 1.25,
                        "accumulated_token_usage": {
                            "model": "gpt-4o-mini",
                            "prompt_tokens": 120,
                            "completion_tokens": 30,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                            "reasoning_tokens": 0,
                            "context_window": 200000,
                            "per_turn_token": 150,
                            "response_id": "",
                        },
                    }
                }
            },
        }

        poll_count = [0]
        original_side_effect = mock_client_instance.request.side_effect
        ws_callback = [lambda event: None]

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                poll_count[0] += 1
                response = Mock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                if poll_count[0] <= 2:
                    response.json.return_value = {
                        "id": conversation_id,
                        "execution_status": "running",
                        "stats": {"usage_to_metrics": {}},
                    }
                elif poll_count[0] <= 4:
                    response.json.return_value = stale_info
                else:
                    response.json.return_value = final_info
                    ws_callback[0](
                        ConversationStateUpdateEvent(
                            key=FULL_STATE_KEY,
                            value=final_info,
                        )
                    )
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        ws_callback[0] = mock_ws_client.call_args.kwargs["callback"]
        conversation.state._cached_state = {
            "id": conversation_id,
            "execution_status": "running",
            "stats": {"usage_to_metrics": {}},
        }

        conversation.run(blocking=True, poll_interval=0.01)

        assert poll_count[0] >= 1
        assert "test-llm" in conversation.state.stats.usage_to_metrics
        assert conversation.state.stats.usage_to_metrics[
            "test-llm"
        ].accumulated_cost == pytest.approx(1.25)

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_error_status_raises(self, mock_ws_client):
        """Test that error status raises ConversationRunError."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                response = Mock()
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": conversation_id,
                    "execution_status": "error",
                }
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        with pytest.raises(ConversationRunError) as exc_info:
            conversation.run(poll_interval=0.01)
        assert "error" in str(exc_info.value).lower()

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_stuck_status_raises(self, mock_ws_client):
        """Test that stuck status raises ConversationRunError."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                response = Mock()
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "id": conversation_id,
                    "execution_status": "stuck",
                }
                return response
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        with pytest.raises(ConversationRunError) as exc_info:
            conversation.run(poll_interval=0.01)
        assert "stuck" in str(exc_info.value).lower()

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_run_404_raises(self, mock_ws_client):
        """Test that 404s during polling raise ConversationRunError."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        original_side_effect = mock_client_instance.request.side_effect

        def custom_side_effect(method, url, **kwargs):
            if method == "GET" and url == f"/api/conversations/{conversation_id}":
                request = httpx.Request("GET", f"http://localhost{url}")
                return httpx.Response(404, request=request, text="Not Found")
            return original_side_effect(method, url, **kwargs)

        mock_client_instance.request.side_effect = custom_side_effect

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        with pytest.raises(ConversationRunError) as exc_info:
            conversation.run(poll_interval=0.01)
        assert "not found" in str(exc_info.value).lower()

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
    def test_remote_conversation_interrupt(self, mock_ws_client):
        """interrupt() must POST to /interrupt, not degrade to /pause."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.interrupt()

        posts = [
            call[0][1]
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
        ]
        assert any(
            f"/api/conversations/{conversation_id}/interrupt" in url for url in posts
        ), "Should have made a POST call to interrupt endpoint"
        assert not any(
            f"/api/conversations/{conversation_id}/pause" in url for url in posts
        ), "interrupt() must not degrade to the pause endpoint"

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_load_plugin(self, mock_ws_client):
        """load_plugin() POSTs the plugin reference to the server."""
        conversation_id = str(uuid.uuid4())
        mock_client_instance = self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        conversation = RemoteConversation(agent=self.agent, workspace=self.workspace)
        conversation.load_plugin("review-bot@team")

        matching_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if call[0][0] == "POST"
            and f"/api/conversations/{conversation_id}/load_plugin" in call[0][1]
        ]
        assert len(matching_calls) == 1
        assert matching_calls[0].kwargs["json"] == {"plugin_ref": "review-bot@team"}

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

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_execute_tool_not_implemented(self, mock_ws_client):
        """Test that execute_tool raises NotImplementedError for RemoteConversation."""
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

        # Create a dummy action (using a simple mock)
        from unittest.mock import MagicMock

        mock_action = MagicMock()

        # Verify execute_tool raises NotImplementedError
        with pytest.raises(NotImplementedError) as exc_info:
            conversation.execute_tool("any_tool", mock_action)

        assert "not yet supported for RemoteConversation" in str(exc_info.value)

    @patch(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient"
    )
    def test_remote_conversation_calls_register_conversation(self, mock_ws_client):
        """Test RemoteConversation.__init__ calls workspace.register_conversation."""
        conversation_id = str(uuid.uuid4())
        self.setup_mock_client(conversation_id=conversation_id)

        mock_ws_instance = Mock()
        mock_ws_client.return_value = mock_ws_instance

        # Patch register_conversation at the class level to verify it gets called
        with patch.object(RemoteWorkspace, "register_conversation") as mock_register:
            # Create RemoteConversation - this should call register_conversation
            _conversation = RemoteConversation(
                agent=self.agent,
                workspace=self.workspace,
            )

            # Verify register_conversation was called with the conversation ID
            mock_register.assert_called_once_with(conversation_id)
