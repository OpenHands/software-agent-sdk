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
                "https://cloud.example.com/api/v1/sandboxes/sandbox-123",
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


# --- local_agent_server_mode tests ---

_CLOUD_URL = "https://app.all-hands.dev"
_CLOUD_KEY = "test-key"


def _make_local_workspace(**overrides):
    """Helper to create an OpenHandsCloudWorkspace in local_agent_server_mode."""
    from openhands.workspace import OpenHandsCloudWorkspace

    kwargs = {
        "local_agent_server_mode": True,
        "cloud_api_url": _CLOUD_URL,
        "cloud_api_key": _CLOUD_KEY,
        **overrides,
    }
    return OpenHandsCloudWorkspace(**kwargs)


def test_local_agent_server_mode_skips_sandbox_creation():
    """In local_agent_server_mode, no sandbox is created or resumed."""
    workspace = _make_local_workspace()

    assert workspace.local_agent_server_mode is True
    assert workspace.host == "http://localhost:60000"
    # Without SANDBOX_ID env var or constructor param, _sandbox_id is None
    assert workspace._sandbox_id is None

    workspace.cleanup()


def test_local_agent_server_mode_sandbox_id_from_constructor():
    """sandbox_id constructor param populates _sandbox_id in local_agent_server_mode."""
    workspace = _make_local_workspace(sandbox_id="sb-123")

    assert workspace._sandbox_id == "sb-123"
    workspace.cleanup()


def test_local_agent_server_mode_sandbox_id_from_env(monkeypatch):
    """SANDBOX_ID env var populates _sandbox_id in local_agent_server_mode."""
    monkeypatch.setenv("SANDBOX_ID", "sb-env-456")
    workspace = _make_local_workspace()

    assert workspace._sandbox_id == "sb-env-456"
    workspace.cleanup()


def test_local_agent_server_mode_session_key_from_env(monkeypatch):
    """SESSION_API_KEY populates _session_api_key and api_key."""
    monkeypatch.setenv("SESSION_API_KEY", "sess-key-abc")
    workspace = _make_local_workspace()

    assert workspace._session_api_key == "sess-key-abc"
    # api_key must also be set so the shared HTTP client includes X-Session-API-Key
    assert workspace.api_key == "sess-key-abc"
    workspace.cleanup()


def test_local_agent_server_mode_session_key_fallback(monkeypatch):
    """Falls back to OH_SESSION_API_KEYS_0 if SESSION_API_KEY is unset."""
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "oh-key-xyz")
    workspace = _make_local_workspace()

    assert workspace._session_api_key == "oh-key-xyz"
    assert workspace.api_key == "oh-key-xyz"
    workspace.cleanup()


def test_local_agent_server_mode_custom_port():
    """Custom agent_server_port is reflected in host URL."""
    workspace = _make_local_workspace(agent_server_port=9999)

    assert workspace.host == "http://localhost:9999"
    workspace.cleanup()


def test_local_agent_server_mode_port_from_env(monkeypatch):
    """AGENT_SERVER_PORT env var overrides agent_server_port."""
    monkeypatch.setenv("AGENT_SERVER_PORT", "7777")
    workspace = _make_local_workspace()

    assert workspace.host == "http://localhost:7777"
    workspace.cleanup()


def test_local_agent_server_mode_cloud_credentials_available():
    """Cloud API fields are available for get_llms / get_secrets."""
    workspace = _make_local_workspace(
        cloud_api_url="https://app.all-hands.dev/",
        cloud_api_key="my-key",
    )

    assert workspace.cloud_api_url == "https://app.all-hands.dev"
    assert workspace._api_headers == {"Authorization": "Bearer my-key"}
    workspace.cleanup()


def test_local_agent_server_mode_cleanup_does_not_delete_sandbox():
    """cleanup() in local_agent_server_mode should not call any Cloud API."""
    workspace = _make_local_workspace()

    with patch.object(workspace, "_send_api_request") as mock_req:
        workspace.cleanup()
        mock_req.assert_not_called()


def test_local_agent_server_mode_context_manager():
    """Context manager works in local_agent_server_mode without side effects."""
    with _make_local_workspace() as ws:
        assert ws.host == "http://localhost:60000"


# --- completion callback tests ---


def test_callback_on_successful_exit(monkeypatch):
    """__exit__ POSTs COMPLETED status to callback URL on clean exit."""
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    monkeypatch.setenv("AUTOMATION_RUN_ID", "run-42")
    ws = _make_local_workspace()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        ws.__exit__(None, None, None)

        mock_client.post.assert_called_once()
        (url,) = mock_client.post.call_args.args
        payload = mock_client.post.call_args.kwargs["json"]
        assert url == "https://svc.test/complete"
        assert payload["status"] == "COMPLETED"
        assert payload["run_id"] == "run-42"
        assert "error" not in payload


def test_callback_on_exception_exit(monkeypatch):
    """__exit__ POSTs FAILED status with error detail on exception."""
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    monkeypatch.setenv("AUTOMATION_RUN_ID", "run-99")
    ws = _make_local_workspace()

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        exc = RuntimeError("script crashed")
        ws.__exit__(RuntimeError, exc, None)

        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["status"] == "FAILED"
        assert payload["run_id"] == "run-99"
        assert "script crashed" in payload["error"]


def test_no_callback_when_url_not_set():
    """No HTTP call when AUTOMATION_CALLBACK_URL env var is not set."""
    ws = _make_local_workspace()
    assert ws._automation_callback_url is None

    with patch("httpx.Client") as MockClient:
        ws.__exit__(None, None, None)
        MockClient.assert_not_called()


def test_callback_failure_does_not_raise(monkeypatch):
    """Callback errors are swallowed — cleanup still runs."""
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    ws = _make_local_workspace()

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        # Should not raise
        ws.__exit__(None, None, None)
