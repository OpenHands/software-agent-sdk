"""Test OpenHandsCloudWorkspace implementation."""

from unittest.mock import MagicMock, patch

import httpx


def test_api_timeout_is_used_in_client():
    """Test that api_timeout parameter is used for the HTTP client timeout."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        custom_timeout = 300.0
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
            api_timeout=custom_timeout,
        )

        # Set up for client initialization
        workspace._sandbox_id = "sandbox-123"
        workspace._session_api_key = "session-key"
        workspace.host = "https://agent.example.com"
        workspace.api_key = "session-key"

        client = workspace.client

        assert isinstance(client, httpx.Client)
        assert client.timeout.read == custom_timeout
        assert client.timeout.connect == 10.0
        assert client.timeout.write == 10.0
        assert client.timeout.pool == 10.0

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_api_timeout_default_value():
    """Test that the default api_timeout is 60 seconds."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
        )

        # Set up for client initialization
        workspace._sandbox_id = "sandbox-123"
        workspace._session_api_key = "session-key"
        workspace.host = "https://agent.example.com"
        workspace.api_key = "session-key"

        client = workspace.client

        assert client.timeout.read == 60.0

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_api_headers_uses_bearer_token():
    """Test that _api_headers uses Bearer token authentication."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
        )

        headers = workspace._api_headers
        assert headers == {"Authorization": "Bearer test-api-key"}

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_get_agent_server_url_extracts_correct_url():
    """Test that _get_agent_server_url extracts AGENT_SERVER URL."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
        )

        workspace._exposed_urls = [
            {"name": "OTHER_SERVICE", "url": "https://other.example.com", "port": 9000},
            {"name": "AGENT_SERVER", "url": "https://agent.example.com", "port": 8080},
        ]

        url = workspace._get_agent_server_url()
        assert url == "https://agent.example.com"

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_get_agent_server_url_returns_none_when_not_found():
    """Test that _get_agent_server_url returns None when AGENT_SERVER not found."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
        )

        workspace._exposed_urls = [
            {"name": "OTHER_SERVICE", "url": "https://other.example.com", "port": 9000},
        ]

        url = workspace._get_agent_server_url()
        assert url is None

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_get_agent_server_url_returns_none_when_empty():
    """Test that _get_agent_server_url returns None when exposed_urls is empty."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
        )

        workspace._exposed_urls = None

        url = workspace._get_agent_server_url()
        assert url is None

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_cleanup_deletes_sandbox():
    """Test that cleanup deletes the sandbox."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="api-key",
            keep_alive=False,
        )

        workspace._sandbox_id = "sandbox-123"
        workspace._session_api_key = "session-key"
        workspace._exposed_urls = []

        with patch.object(workspace, "_send_api_request") as mock_request:
            workspace.cleanup()

            mock_request.assert_called_once_with(
                "DELETE",
                "https://cloud.example.com/api/v1/sandboxes",
                params={"sandbox_id": "sandbox-123"},
                timeout=30.0,
            )
            assert workspace._sandbox_id is None
            assert workspace._session_api_key is None


def test_cleanup_keeps_sandbox_alive_when_configured():
    """Test that cleanup keeps sandbox alive when keep_alive is True."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="api-key",
            keep_alive=True,
        )

        workspace._sandbox_id = "sandbox-123"
        workspace._session_api_key = "session-key"
        workspace._exposed_urls = []

        with patch.object(workspace, "_send_api_request") as mock_request:
            workspace.cleanup()

            # Should not call DELETE when keep_alive is True
            mock_request.assert_not_called()


def test_cleanup_handles_missing_sandbox_id():
    """Test that cleanup handles missing sandbox_id gracefully."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="api-key",
            keep_alive=False,
        )

        workspace._sandbox_id = None
        workspace._session_api_key = None
        workspace._exposed_urls = None

        with patch.object(workspace, "_send_api_request") as mock_request:
            # Should not raise an exception
            workspace.cleanup()
            mock_request.assert_not_called()


def test_send_api_request_includes_bearer_token():
    """Test that _send_api_request includes Bearer token header."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.request.return_value = mock_response
            mock_client_class.return_value = mock_client

            workspace._send_api_request("GET", "https://cloud.example.com/api/v1/test")

            mock_client.request.assert_called_once()
            call_kwargs = mock_client.request.call_args
            assert call_kwargs[1]["headers"]["Authorization"] == "Bearer test-api-key"

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_context_manager_calls_cleanup():
    """Test that context manager calls cleanup on exit."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="api-key",
            keep_alive=False,
        )

        workspace._sandbox_id = "sandbox-123"
        workspace._session_api_key = "session-key"
        workspace._exposed_urls = []

        with patch.object(workspace, "_send_api_request"):
            with workspace:
                pass

            assert workspace._sandbox_id is None


def test_cloud_api_url_trailing_slash_removed():
    """Test that trailing slash is removed from cloud_api_url."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com/",
            cloud_api_key="test-api-key",
        )

        assert workspace.cloud_api_url == "https://cloud.example.com"

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_sandbox_id_field_is_public():
    """Test that sandbox_id is a public field that can be set."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
            sandbox_id="existing-sandbox-123",
        )

        assert workspace.sandbox_id == "existing-sandbox-123"

        # Clean up
        workspace._sandbox_id = None
        workspace.cleanup()


def test_sandbox_id_triggers_resume_instead_of_create():
    """Test that providing sandbox_id calls resume endpoint instead of create."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
            sandbox_id="existing-sandbox-123",
        )

    # Mock the methods - use class-level patch for reset_client
    with (
        patch.object(workspace, "_resume_sandbox") as mock_resume,
        patch.object(workspace, "_create_new_sandbox") as mock_create,
        patch.object(workspace, "_wait_until_sandbox_ready"),
        patch.object(workspace, "_get_agent_server_url") as mock_get_url,
        patch.object(OpenHandsCloudWorkspace, "reset_client"),
    ):
        mock_get_url.return_value = "https://agent.example.com"
        workspace._start_sandbox()

        # Should call resume, not create
        mock_resume.assert_called_once()
        mock_create.assert_not_called()
        assert workspace._sandbox_id == "existing-sandbox-123"

    # Clean up
    workspace._sandbox_id = None
    workspace.cleanup()


def test_no_sandbox_id_creates_new_sandbox():
    """Test that without sandbox_id, a new sandbox is created."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
        )

    # Mock the methods - use class-level patch for reset_client
    with (
        patch.object(workspace, "_resume_sandbox") as mock_resume,
        patch.object(workspace, "_create_new_sandbox") as mock_create,
        patch.object(workspace, "_wait_until_sandbox_ready"),
        patch.object(workspace, "_get_agent_server_url") as mock_get_url,
        patch.object(OpenHandsCloudWorkspace, "reset_client"),
    ):
        mock_get_url.return_value = "https://agent.example.com"
        workspace._start_sandbox()

        # Should call create, not resume
        mock_create.assert_called_once()
        mock_resume.assert_not_called()

    # Clean up
    workspace._sandbox_id = None
    workspace.cleanup()


def test_resume_existing_sandbox_sets_internal_id():
    """Test that _resume_existing_sandbox sets _sandbox_id from sandbox_id."""
    from openhands.workspace import OpenHandsCloudWorkspace

    with patch.object(OpenHandsCloudWorkspace, "_start_sandbox"):
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://cloud.example.com",
            cloud_api_key="test-api-key",
            sandbox_id="my-sandbox-id",
        )

    with patch.object(workspace, "_send_api_request"):
        workspace._resume_existing_sandbox()

        assert workspace._sandbox_id == "my-sandbox-id"

    # Clean up
    workspace._sandbox_id = None
    workspace.cleanup()
