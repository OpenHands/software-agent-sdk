"""Test APIRemoteWorkspace timeout configuration."""

from unittest.mock import patch

import httpx


def test_api_timeout_is_used_in_client():
    """Test that api_timeout parameter is used for the HTTP client timeout."""
    from openhands.workspace import APIRemoteWorkspace

    # Mock the entire initialization process
    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        # Create a workspace with custom api_timeout
        custom_timeout = 300.0
        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
            api_timeout=custom_timeout,
        )

        # The runtime properties need to be set for client initialization
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "test-session-key"
        workspace.host = workspace._runtime_url

        # Access the client property to trigger initialization
        client = workspace.client

        # Verify that the client's timeout uses the custom api_timeout
        assert isinstance(client, httpx.Client)
        assert client.timeout.read == custom_timeout
        assert client.timeout.connect == 10.0
        assert client.timeout.write == 10.0
        assert client.timeout.pool == 10.0

        # Clean up
        workspace._runtime_id = None  # Prevent cleanup from trying to stop runtime
        workspace.cleanup()


def test_api_timeout_default_value():
    """Test that the default api_timeout is 60 seconds."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # The runtime properties need to be set for client initialization
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "test-session-key"
        workspace.host = workspace._runtime_url

        # Access the client property to trigger initialization
        client = workspace.client

        # Verify default timeout is 60 seconds
        assert client.timeout.read == 60.0

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()


def test_different_timeout_values():
    """Test that different api_timeout values are correctly applied."""
    from openhands.workspace import APIRemoteWorkspace

    test_timeouts = [30.0, 120.0, 600.0]

    for timeout_value in test_timeouts:
        with patch.object(
            APIRemoteWorkspace, "_start_or_attach_to_runtime"
        ) as mock_init:
            mock_init.return_value = None

            workspace = APIRemoteWorkspace(
                runtime_api_url="https://example.com",
                runtime_api_key="test-key",
                server_image="test-image",
                api_timeout=timeout_value,
            )

            workspace._runtime_id = "test-runtime-id"
            workspace._runtime_url = "https://test-runtime.com"
            workspace._session_api_key = "test-session-key"
            workspace.host = workspace._runtime_url

            client = workspace.client

            assert client.timeout.read == timeout_value, (
                f"Expected timeout {timeout_value}, got {client.timeout.read}"
            )

            workspace._runtime_id = None
            workspace.cleanup()


def test_client_recreates_when_api_key_changes():
    """Test that client automatically recreates when api_key changes."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # Set up initial state
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "initial-session-key"
        workspace.host = workspace._runtime_url
        workspace.api_key = "initial-session-key"

        # Get initial client
        client1 = workspace.client
        assert isinstance(client1, httpx.Client)
        initial_client_id = id(client1)

        # Change api_key
        workspace.api_key = "new-session-key"

        # Get client again - should be a new instance
        client2 = workspace.client
        assert isinstance(client2, httpx.Client)
        new_client_id = id(client2)

        # Verify it's a different client instance
        assert initial_client_id != new_client_id, "Client should have been recreated"

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()


def test_client_recreates_when_host_changes():
    """Test that client automatically recreates when host changes."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # Set up initial state
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime-1.com"
        workspace._session_api_key = "test-session-key"
        workspace.host = workspace._runtime_url
        workspace.api_key = "test-session-key"

        # Get initial client
        client1 = workspace.client
        initial_client_id = id(client1)
        initial_base_url = str(client1.base_url)

        # Change host
        workspace.host = "https://test-runtime-2.com"

        # Get client again - should be a new instance
        client2 = workspace.client
        new_client_id = id(client2)
        new_base_url = str(client2.base_url)

        # Verify it's a different client with new base_url
        assert initial_client_id != new_client_id, "Client should have been recreated"
        assert initial_base_url != new_base_url, "Base URL should have changed"
        assert "test-runtime-2.com" in new_base_url

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()


def test_client_not_recreated_when_credentials_unchanged():
    """Test that client is reused when credentials don't change."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # Set up state
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "test-session-key"
        workspace.host = workspace._runtime_url
        workspace.api_key = "test-session-key"

        # Get client multiple times without changing credentials
        client1 = workspace.client
        client2 = workspace.client
        client3 = workspace.client

        # All should be the same instance
        assert id(client1) == id(client2) == id(client3), (
            "Client should be reused when credentials don't change"
        )

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()


def test_client_headers_updated_with_new_api_key():
    """Test that client headers use the new api_key after change."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # Set up initial state
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "old-key"
        workspace.host = workspace._runtime_url
        workspace.api_key = "old-key"

        # Get initial client and check headers
        client1 = workspace.client
        # The _headers property should return headers with old key
        headers1 = workspace._headers
        assert headers1.get("X-Session-API-Key") == "old-key"

        # Change api_key
        workspace.api_key = "new-key"

        # Get new client and check headers
        client2 = workspace.client
        headers2 = workspace._headers
        assert headers2.get("X-Session-API-Key") == "new-key"

        # Verify it's a new client
        assert id(client1) != id(client2)

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()


def test_reset_client_still_works():
    """Test that manual reset_client() works."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # Set up state
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime.com"
        workspace._session_api_key = "test-session-key"
        workspace.host = workspace._runtime_url
        workspace.api_key = "test-session-key"

        # Get initial client
        client1 = workspace.client
        initial_client_id = id(client1)

        # Manually reset client
        workspace.reset_client()

        # Verify cache is cleared
        assert workspace._cached_api_key is None
        assert workspace._cached_host is None

        # Get client again - should be a new instance
        client2 = workspace.client
        new_client_id = id(client2)

        # Verify it's a different client
        assert initial_client_id != new_client_id, (
            "Client should be recreated after reset_client()"
        )

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()


def test_client_recreates_when_both_host_and_api_key_change():
    """Test that client recreates when both host and api_key change."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        # Set up initial state
        workspace._runtime_id = "test-runtime-id"
        workspace._runtime_url = "https://test-runtime-1.com"
        workspace._session_api_key = "old-key"
        workspace.host = workspace._runtime_url
        workspace.api_key = "old-key"

        # Get initial client
        client1 = workspace.client
        initial_client_id = id(client1)

        # Change both host and api_key
        workspace.host = "https://test-runtime-2.com"
        workspace.api_key = "new-key"

        # Get client again - should be recreated
        client2 = workspace.client
        new_client_id = id(client2)

        # Verify it's a new client
        assert initial_client_id != new_client_id, (
            "Client should be recreated when credentials change"
        )

        # Verify new values are used
        assert "test-runtime-2.com" in str(client2.base_url)
        assert workspace._headers.get("X-Session-API-Key") == "new-key"

        # Clean up
        workspace._runtime_id = None
        workspace.cleanup()
