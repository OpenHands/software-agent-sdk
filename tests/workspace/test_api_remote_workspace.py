"""Test APIRemoteWorkspace timeout configuration."""

import os
from unittest.mock import MagicMock, patch

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


def test_forward_env_default_is_empty():
    """Test that forward_env defaults to an empty list."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_init:
        mock_init.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
        )

        assert workspace.forward_env == []

        workspace._runtime_id = None
        workspace.cleanup()


def test_forward_env_is_included_in_start_runtime_payload():
    """Test that forward_env variables are included in the runtime start payload."""
    from openhands.workspace import APIRemoteWorkspace

    # Set up test environment variables
    test_env = {
        "TEST_VAR_1": "value1",
        "TEST_VAR_2": "value2",
        "UNSET_VAR": None,  # This one won't be in os.environ
    }

    with patch.dict(os.environ, {k: v for k, v in test_env.items() if v is not None}):
        with patch.object(
            APIRemoteWorkspace, "_start_or_attach_to_runtime"
        ) as mock_attach:
            mock_attach.return_value = None

            workspace = APIRemoteWorkspace(
                runtime_api_url="https://example.com",
                runtime_api_key="test-key",
                server_image="test-image",
                forward_env=["TEST_VAR_1", "TEST_VAR_2", "UNSET_VAR"],
            )

            # Mock the API request method to capture the payload
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "runtime_id": "test-id",
                "url": "https://test-runtime.com",
                "session_api_key": "test-key",
            }

            with patch.object(
                workspace, "_send_api_request", return_value=mock_response
            ) as mock_request:
                workspace._start_runtime()

                # Verify the API was called with the correct payload
                mock_request.assert_called_once()
                call_kwargs = mock_request.call_args
                payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")

                # Check that environment contains the forwarded variables
                assert "environment" in payload
                assert payload["environment"]["TEST_VAR_1"] == "value1"
                assert payload["environment"]["TEST_VAR_2"] == "value2"
                # UNSET_VAR should not be in environment since it's not in os.environ
                assert "UNSET_VAR" not in payload["environment"]

            workspace._runtime_id = None
            workspace.cleanup()


def test_forward_env_empty_list_results_in_empty_environment():
    """Test that an empty forward_env results in an empty environment dict."""
    from openhands.workspace import APIRemoteWorkspace

    with patch.object(APIRemoteWorkspace, "_start_or_attach_to_runtime") as mock_attach:
        mock_attach.return_value = None

        workspace = APIRemoteWorkspace(
            runtime_api_url="https://example.com",
            runtime_api_key="test-key",
            server_image="test-image",
            forward_env=[],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "runtime_id": "test-id",
            "url": "https://test-runtime.com",
            "session_api_key": "test-key",
        }

        with patch.object(
            workspace, "_send_api_request", return_value=mock_response
        ) as mock_request:
            workspace._start_runtime()

            call_kwargs = mock_request.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")

            assert payload["environment"] == {}

        workspace._runtime_id = None
        workspace.cleanup()
