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
