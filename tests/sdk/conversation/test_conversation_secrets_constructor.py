"""Tests for Conversation constructor with secrets parameter."""

import tempfile
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.llm import LLM
from openhands.sdk.secret import SecretSource
from openhands.sdk.workspace import RemoteWorkspace

from .conftest import create_mock_http_client


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def test_local_conversation_constructor_with_secrets():
    """Test LocalConversation constructor accepts and initializes secrets."""
    agent = create_test_agent()

    # Test secrets as dict[str, str]
    test_secrets = {
        "API_KEY": "test-api-key-123",
        "DATABASE_URL": "postgresql://localhost/test",
        "AUTH_TOKEN": "bearer-token-456",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent, workspace=tmpdir, persistence_dir=tmpdir, secrets=test_secrets
        )

        # Verify it's a LocalConversation
        assert isinstance(conv, LocalConversation)

        # Verify secrets were initialized
        secret_registry = conv.state.secret_registry
        assert secret_registry is not None

        # Verify secrets are accessible through the secret registry
        env_vars = secret_registry.get_secrets_as_env_vars("echo $API_KEY")
        assert env_vars == {"API_KEY": "test-api-key-123"}

        env_vars = secret_registry.get_secrets_as_env_vars("echo $DATABASE_URL")
        assert env_vars == {"DATABASE_URL": "postgresql://localhost/test"}

        # Test multiple secrets in one command
        env_vars = secret_registry.get_secrets_as_env_vars(
            "export API_KEY=$API_KEY && export AUTH_TOKEN=$AUTH_TOKEN"
        )
        assert env_vars == {
            "API_KEY": "test-api-key-123",
            "AUTH_TOKEN": "bearer-token-456",
        }


def test_local_conversation_constructor_with_callable_secrets():
    """Test LocalConversation constructor with callable secrets."""
    agent = create_test_agent()

    class MyLocalConversationConstructorDynamicTokenSource(SecretSource):
        def get_value(self):
            return "dynamic-token-789"

    class MyLocalConversationConstructorApiKeySource(SecretSource):
        def get_value(self):
            return "callable-api-key"

    test_secrets = {
        "STATIC_KEY": "static-value",
        "DYNAMIC_TOKEN": MyLocalConversationConstructorDynamicTokenSource(),
        "API_KEY": MyLocalConversationConstructorApiKeySource(),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent, workspace=tmpdir, persistence_dir=tmpdir, secrets=test_secrets
        )

        # Verify it's a LocalConversation
        assert isinstance(conv, LocalConversation)

        # Verify callable secrets work
        secret_registry = conv.state.secret_registry

        env_vars = secret_registry.get_secrets_as_env_vars("echo $DYNAMIC_TOKEN")
        assert env_vars == {"DYNAMIC_TOKEN": "dynamic-token-789"}

        env_vars = secret_registry.get_secrets_as_env_vars("echo $API_KEY")
        assert env_vars == {"API_KEY": "callable-api-key"}

        env_vars = secret_registry.get_secrets_as_env_vars("echo $STATIC_KEY")
        assert env_vars == {"STATIC_KEY": "static-value"}


def test_local_conversation_constructor_without_secrets():
    """Test LocalConversation constructor works without secrets parameter."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            workspace=tmpdir,
            persistence_dir=tmpdir,
            # No secrets parameter
        )

        # Verify it's a LocalConversation
        assert isinstance(conv, LocalConversation)

        # Verify secrets manager exists but is empty
        secret_registry = conv.state.secret_registry
        assert secret_registry is not None

        # Should return empty dict for any command
        env_vars = secret_registry.get_secrets_as_env_vars("echo $API_KEY")
        assert env_vars == {}


def test_local_conversation_constructor_with_empty_secrets():
    """Test LocalConversation constructor with empty secrets dict."""
    agent = create_test_agent()

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent,
            workspace=tmpdir,
            persistence_dir=tmpdir,
            secrets={},  # Empty dict
        )

        # Verify it's a LocalConversation
        assert isinstance(conv, LocalConversation)

        # Verify secrets manager exists but is empty
        secret_registry = conv.state.secret_registry
        assert secret_registry is not None

        # Should return empty dict for any command
        env_vars = secret_registry.get_secrets_as_env_vars("echo $API_KEY")
        assert env_vars == {}


@pytest.mark.parametrize("api_key", [None, "test-api-key"])
def test_remote_conversation_constructor_with_secrets(api_key):
    """Test RemoteConversation constructor accepts and initializes secrets."""
    agent = create_test_agent()

    # Mock httpx client
    mock_client_instance = create_mock_http_client()

    test_secrets = {
        "API_KEY": "test-api-key-123",
        "DATABASE_URL": "postgresql://localhost/test",
    }

    with (
        patch("httpx.Client", return_value=mock_client_instance),
        patch(
            "openhands.sdk.conversation.impl.remote_conversation"
            ".WebSocketCallbackClient"
        ),
    ):
        # Create a RemoteWorkspace
        workspace = RemoteWorkspace(
            host="http://localhost:3000",
            api_key=api_key,
            working_dir="/workspace/project",
        )

        # Replace workspace client with mock to ensure all HTTP calls use the mock
        workspace._client = mock_client_instance

        conv = Conversation(agent=agent, workspace=workspace, secrets=test_secrets)

        # Verify it's a RemoteConversation
        assert isinstance(conv, RemoteConversation)

        # Verify that update_secrets was called during initialization
        # The RemoteConversation should have made a POST request to update secrets
        mock_client_instance.request.assert_any_call(
            "POST",
            "/api/conversations/12345678-1234-5678-9abc-123456789abc/secrets",
            json={"secrets": test_secrets},
        )


def test_remote_conversation_constructor_with_callable_secrets():
    """Test RemoteConversation constructor with callable secrets."""
    agent = create_test_agent()

    # Mock httpx client
    mock_client_instance = create_mock_http_client()

    def get_dynamic_token():
        return "dynamic-token-789"

    test_secrets = {"STATIC_KEY": "static-value", "DYNAMIC_TOKEN": get_dynamic_token}

    with (
        patch("httpx.Client", return_value=mock_client_instance),
        patch(
            "openhands.sdk.conversation.impl.remote_conversation"
            ".WebSocketCallbackClient"
        ),
    ):
        # Create a RemoteWorkspace
        workspace = RemoteWorkspace(
            host="http://localhost:3000",
            api_key="test-api-key",
            working_dir="/workspace/project",
        )

        # Replace workspace client with mock to ensure all HTTP calls use the mock
        workspace._client = mock_client_instance

        conv = Conversation(agent=agent, workspace=workspace, secrets=test_secrets)

        # Verify it's a RemoteConversation
        assert isinstance(conv, RemoteConversation)

        # Verify that callable secrets were resolved and sent to server
        expected_serialized_secrets = {
            "STATIC_KEY": "static-value",
            "DYNAMIC_TOKEN": "dynamic-token-789",  # Callable was invoked
        }

        mock_client_instance.request.assert_any_call(
            "POST",
            "/api/conversations/12345678-1234-5678-9abc-123456789abc/secrets",
            json={"secrets": expected_serialized_secrets},
        )


def test_remote_conversation_constructor_without_secrets():
    """Test RemoteConversation constructor works without secrets parameter."""
    agent = create_test_agent()

    # Mock httpx client
    mock_client_instance = create_mock_http_client()

    with (
        patch("httpx.Client", return_value=mock_client_instance),
        patch(
            "openhands.sdk.conversation.impl.remote_conversation"
            ".WebSocketCallbackClient"
        ),
    ):
        # Create a RemoteWorkspace
        workspace = RemoteWorkspace(
            host="http://localhost:3000",
            api_key="test-api-key",
            working_dir="/workspace/project",
        )

        # Replace workspace client with mock to ensure all HTTP calls use the mock
        workspace._client = mock_client_instance

        conv = Conversation(
            agent=agent,
            workspace=workspace,
            # No secrets parameter
        )

        # Verify it's a RemoteConversation
        assert isinstance(conv, RemoteConversation)

        # Verify that no secrets update call was made
        secrets_calls = [
            call
            for call in mock_client_instance.request.call_args_list
            if "/secrets" in str(call)
        ]
        assert len(secrets_calls) == 0


def test_conversation_factory_routing_with_secrets():
    """Test that Conversation factory correctly routes to Local/Remote with secrets."""
    agent = create_test_agent()
    test_secrets = {"API_KEY": "test-key"}

    # Test LocalConversation routing
    with tempfile.TemporaryDirectory() as tmpdir:
        local_conv = Conversation(agent=agent, workspace=tmpdir, secrets=test_secrets)
        assert isinstance(local_conv, LocalConversation)

    # Test RemoteConversation routing
    # Mock httpx client
    mock_client_instance = create_mock_http_client()

    with (
        patch("httpx.Client", return_value=mock_client_instance),
        patch(
            "openhands.sdk.conversation.impl.remote_conversation"
            ".WebSocketCallbackClient"
        ),
    ):
        workspace = RemoteWorkspace(
            host="http://localhost:3000",
            api_key="test-api-key",
            working_dir="/workspace/project",
        )

        # Replace workspace client with mock to ensure all HTTP calls use the mock
        workspace._client = mock_client_instance

        remote_conv = Conversation(
            agent=agent, workspace=workspace, secrets=test_secrets
        )
        assert isinstance(remote_conv, RemoteConversation)


def test_secrets_parameter_type_validation():
    """Test that secrets parameter accepts correct types."""
    agent = create_test_agent()

    # Test with valid dict[str, str]
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, workspace=tmpdir, secrets={"KEY": "value"})
        assert isinstance(conv, LocalConversation)

    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(
            agent=agent, workspace=tmpdir, secrets={"KEY": "secret-value"}
        )  # type: ignore[dict-item]
        assert isinstance(conv, LocalConversation)

    # Test with None (should work)
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = Conversation(agent=agent, workspace=tmpdir, secrets=None)
        assert isinstance(conv, LocalConversation)
