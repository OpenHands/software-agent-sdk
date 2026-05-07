"""Unit tests for RemoteWorkspace class."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from openhands.sdk.workspace.models import CommandResult, FileOperationResult
from openhands.sdk.workspace.remote.base import RemoteWorkspace


class MockHTTPResponse:
    """Mock HTTP response for urlopen."""

    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def test_remote_workspace_initialization():
    """Test RemoteWorkspace can be initialized with required parameters."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    assert workspace.host == "http://localhost:8000"
    assert workspace.working_dir == "/tmp"
    assert workspace.api_key == "test-key"


def test_remote_workspace_initialization_without_api_key():
    """Test RemoteWorkspace can be initialized without API key."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    assert workspace.host == "http://localhost:8000"
    assert workspace.working_dir == "/tmp"
    assert workspace.api_key is None


def test_remote_workspace_host_normalization():
    """Test that host URL is normalized by removing trailing slash."""
    workspace = RemoteWorkspace(host="http://localhost:8000/", working_dir="/tmp")

    assert workspace.host == "http://localhost:8000"


def test_client_property_lazy_initialization():
    """Test that client property creates httpx.Client lazily."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    # Client should be None initially
    assert workspace._client is None

    # Accessing client should create it
    client = workspace.client
    assert isinstance(client, httpx.Client)
    assert workspace._client is client

    # Subsequent access should return same client
    assert workspace.client is client


def test_headers_property_with_api_key():
    """Test _headers property includes API key when present."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    headers = workspace._headers
    assert headers == {"X-Session-API-Key": "test-key"}


def test_headers_property_without_api_key():
    """Test _headers property is empty when no API key."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    headers = workspace._headers
    assert headers == {}


def test_execute_method():
    """Test _execute method handles generator protocol correctly."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    # Mock client
    mock_client = MagicMock()
    mock_response = Mock()
    mock_client.request.return_value = mock_response
    workspace._client = mock_client

    # Create a simple generator that yields request kwargs and returns a result
    def test_generator():
        yield {"method": "GET", "url": "http://test.com"}
        return "test_result"

    result = workspace._execute(test_generator())

    assert result == "test_result"
    mock_client.request.assert_called_once_with(method="GET", url="http://test.com")


@patch("openhands.sdk.workspace.remote.base.RemoteWorkspace._execute")
def test_execute_command(mock_execute):
    """Test execute_command method calls _execute with correct generator."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    expected_result = CommandResult(
        command="echo hello",
        exit_code=0,
        stdout="hello\n",
        stderr="",
        timeout_occurred=False,
    )
    mock_execute.return_value = expected_result

    result = workspace.execute_command("echo hello", cwd="/tmp", timeout=30.0)

    assert result == expected_result
    mock_execute.assert_called_once()

    # Verify the generator was created correctly
    generator_arg = mock_execute.call_args[0][0]
    assert hasattr(generator_arg, "__next__")


@patch("openhands.sdk.workspace.remote.base.RemoteWorkspace._execute")
def test_file_upload(mock_execute):
    """Test file_upload method calls _execute with correct generator."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    expected_result = FileOperationResult(
        success=True,
        source_path="/local/file.txt",
        destination_path="/remote/file.txt",
        file_size=100,
    )
    mock_execute.return_value = expected_result

    result = workspace.file_upload("/local/file.txt", "/remote/file.txt")

    assert result == expected_result
    mock_execute.assert_called_once()

    # Verify the generator was created correctly
    generator_arg = mock_execute.call_args[0][0]
    assert hasattr(generator_arg, "__next__")


@patch("openhands.sdk.workspace.remote.base.RemoteWorkspace._execute")
def test_file_download(mock_execute):
    """Test file_download method calls _execute with correct generator."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    expected_result = FileOperationResult(
        success=True,
        source_path="/remote/file.txt",
        destination_path="/local/file.txt",
        file_size=100,
    )
    mock_execute.return_value = expected_result

    result = workspace.file_download("/remote/file.txt", "/local/file.txt")

    assert result == expected_result
    mock_execute.assert_called_once()

    # Verify the generator was created correctly
    generator_arg = mock_execute.call_args[0][0]
    assert hasattr(generator_arg, "__next__")


def test_execute_command_with_path_objects():
    """Test execute_command works with Path objects for cwd."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    with patch.object(workspace, "_execute") as mock_execute:
        expected_result = CommandResult(
            command="ls",
            exit_code=0,
            stdout="file1.txt\n",
            stderr="",
            timeout_occurred=False,
        )
        mock_execute.return_value = expected_result

        result = workspace.execute_command("ls", cwd=Path("/tmp/test"))

        assert result == expected_result
        mock_execute.assert_called_once()


def test_file_operations_with_path_objects():
    """Test file operations work with Path objects."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    with patch.object(workspace, "_execute") as mock_execute:
        expected_result = FileOperationResult(
            success=True,
            source_path="/local/file.txt",
            destination_path="/remote/file.txt",
            file_size=100,
        )
        mock_execute.return_value = expected_result

        # Test upload with Path objects
        result = workspace.file_upload(
            Path("/local/file.txt"), Path("/remote/file.txt")
        )
        assert result == expected_result

        # Test download with Path objects
        result = workspace.file_download(
            Path("/remote/file.txt"), Path("/local/file.txt")
        )
        assert result == expected_result


def test_context_manager_protocol():
    """Test RemoteWorkspace supports context manager protocol."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    # Test entering context
    with workspace as ctx:
        assert ctx is workspace

    # Test that __exit__ doesn't raise exceptions
    # (RemoteWorkspace doesn't override __exit__, so it uses BaseWorkspace's
    # no-op implementation)


def test_inheritance():
    """Test RemoteWorkspace inherits from correct base classes."""
    from openhands.sdk.workspace.base import BaseWorkspace
    from openhands.sdk.workspace.remote.remote_workspace_mixin import (
        RemoteWorkspaceMixin,
    )

    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    assert isinstance(workspace, BaseWorkspace)
    assert isinstance(workspace, RemoteWorkspaceMixin)


def test_execute_with_exception_handling():
    """Test _execute method handles exceptions in generator correctly."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    # Mock client to raise an exception
    mock_client = MagicMock()
    mock_client.request.side_effect = httpx.RequestError("Connection failed")
    workspace._client = mock_client

    def failing_generator():
        yield {"method": "GET", "url": "http://test.com"}
        return "should_not_reach_here"

    # The generator should handle the exception and not return the result
    # Since the exception occurs during client.request(), the generator will
    # not complete normally
    with pytest.raises(httpx.RequestError):
        workspace._execute(failing_generator())


def test_execute_generator_completion():
    """Test _execute method properly handles StopIteration to get return value."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    # Mock client
    mock_client = MagicMock()
    mock_response = Mock()
    mock_client.request.return_value = mock_response
    workspace._client = mock_client

    def test_generator():
        # First yield - get response
        yield {"method": "GET", "url": "http://test1.com"}
        # Second yield - get another response
        yield {"method": "POST", "url": "http://test2.com"}
        # Return final result
        return "final_result"

    result = workspace._execute(test_generator())

    assert result == "final_result"
    assert mock_client.request.call_count == 2
    mock_client.request.assert_any_call(method="GET", url="http://test1.com")
    mock_client.request.assert_any_call(method="POST", url="http://test2.com")


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_returns_true_on_successful_health_check(mock_urlopen):
    """Test alive property returns True when health endpoint returns 2xx status."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_urlopen.return_value = MockHTTPResponse(status=200)

    result = workspace.alive

    assert result is True
    mock_urlopen.assert_called_once_with("http://localhost:8000/health", timeout=5.0)


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_returns_true_on_204_status(mock_urlopen):
    """Test alive property returns True when health endpoint returns 204 No Content."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_urlopen.return_value = MockHTTPResponse(status=204)

    result = workspace.alive

    assert result is True


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_returns_false_on_server_error(mock_urlopen):
    """Test alive property returns False when health endpoint returns 5xx status."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_urlopen.return_value = MockHTTPResponse(status=500)

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_returns_false_on_client_error(mock_urlopen):
    """Test alive property returns False when health endpoint returns 4xx status."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_urlopen.return_value = MockHTTPResponse(status=404)

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_returns_false_on_connection_error(mock_urlopen):
    """Test alive property returns False when connection fails."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_urlopen.side_effect = Exception("Connection refused")

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_returns_false_on_timeout(mock_urlopen):
    """Test alive property returns False when request times out."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    from urllib.error import URLError

    mock_urlopen.side_effect = URLError("timed out")

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_constructs_correct_health_url(mock_urlopen):
    """Test alive property constructs correct health URL from host."""
    workspace = RemoteWorkspace(
        host="https://my-agent-server.example.com", working_dir="/tmp"
    )

    mock_urlopen.return_value = MockHTTPResponse(status=200)

    _ = workspace.alive

    mock_urlopen.assert_called_once_with(
        "https://my-agent-server.example.com/health", timeout=5.0
    )


@patch("openhands.sdk.workspace.remote.base.urlopen")
def test_alive_with_normalized_host(mock_urlopen):
    """Test alive property works correctly when host was normalized."""
    # Host with trailing slash gets normalized in model_post_init
    workspace = RemoteWorkspace(host="http://localhost:8000/", working_dir="/tmp")

    mock_urlopen.return_value = MockHTTPResponse(status=200)

    result = workspace.alive

    assert result is True
    # Should not have double slash
    mock_urlopen.assert_called_once_with("http://localhost:8000/health", timeout=5.0)


def test_alive_is_property():
    """Test that alive is a property, not a method."""
    assert isinstance(RemoteWorkspace.alive, property)


# ── Settings Methods Tests ────────────────────────────────────────────────


def test_get_llm_returns_configured_llm(monkeypatch):
    """Test get_llm returns an LLM with persisted settings."""
    from pydantic import SecretStr

    # Allow short context windows for testing
    monkeypatch.setenv("ALLOW_SHORT_CONTEXT_WINDOWS", "true")

    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {
        "agent_settings": {
            "llm": {
                "model": "gpt-4",
                "api_key": "sk-test-key",
                "base_url": "https://api.openai.com/v1",
            }
        },
        "conversation_settings": {},
        "llm_api_key_is_set": True,
    }
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    llm = workspace.get_llm()

    # Verify the LLM was created with correct settings
    assert llm.model == "gpt-4"
    # api_key can be str | SecretStr | None
    assert llm.api_key is not None
    if isinstance(llm.api_key, SecretStr):
        assert llm.api_key.get_secret_value() == "sk-test-key"
    else:
        assert llm.api_key == "sk-test-key"
    assert llm.base_url == "https://api.openai.com/v1"

    # Verify API was called with correct headers
    mock_client.get.assert_called_once()
    call_args = mock_client.get.call_args
    assert call_args[0][0] == "/api/settings"
    assert call_args[1]["headers"]["X-Expose-Secrets"] == "plaintext"
    assert call_args[1]["headers"]["X-Session-API-Key"] == "test-key"


def test_get_llm_with_kwargs_override(monkeypatch):
    """Test get_llm allows kwargs to override persisted settings."""
    from pydantic import SecretStr

    # Allow short context windows for testing
    monkeypatch.setenv("ALLOW_SHORT_CONTEXT_WINDOWS", "true")

    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {
        "agent_settings": {
            "llm": {
                "model": "gpt-3.5-turbo",
                "api_key": "sk-persisted-key",
            }
        },
        "conversation_settings": {},
        "llm_api_key_is_set": True,
    }
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    # Override model but use persisted API key
    llm = workspace.get_llm(model="gpt-4o")

    assert llm.model == "gpt-4o"  # Overridden
    # api_key can be str | SecretStr | None
    assert llm.api_key is not None
    if isinstance(llm.api_key, SecretStr):
        assert llm.api_key.get_secret_value() == "sk-persisted-key"
    else:
        assert llm.api_key == "sk-persisted-key"


def test_get_llm_raises_on_undefined_host():
    """Test get_llm raises RuntimeError when host is undefined."""
    workspace = RemoteWorkspace(host="undefined", working_dir="/tmp")

    with pytest.raises(RuntimeError, match="Workspace host is not set"):
        workspace.get_llm()


def test_get_secrets_returns_lookup_secrets():
    """Test get_secrets returns LookupSecret references."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {
        "secrets": [
            {"name": "GITHUB_TOKEN", "description": "GitHub personal access token"},
            {"name": "OPENAI_API_KEY", "description": None},
        ]
    }
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    secrets = workspace.get_secrets()

    assert len(secrets) == 2
    assert "GITHUB_TOKEN" in secrets
    assert "OPENAI_API_KEY" in secrets

    # Check LookupSecret structure
    gh_secret = secrets["GITHUB_TOKEN"]
    assert gh_secret.url == "http://localhost:8000/api/settings/secrets/GITHUB_TOKEN"
    assert gh_secret.headers == {"X-Session-API-Key": "test-key"}
    assert gh_secret.description == "GitHub personal access token"

    openai_secret = secrets["OPENAI_API_KEY"]
    assert openai_secret.description is None


def test_get_secrets_filters_by_names():
    """Test get_secrets filters secrets by names when provided."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {
        "secrets": [
            {"name": "GITHUB_TOKEN", "description": "GitHub token"},
            {"name": "OPENAI_API_KEY", "description": "OpenAI key"},
            {"name": "AWS_ACCESS_KEY", "description": "AWS key"},
        ]
    }
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    # Request only specific secrets
    secrets = workspace.get_secrets(names=["GITHUB_TOKEN", "AWS_ACCESS_KEY"])

    assert len(secrets) == 2
    assert "GITHUB_TOKEN" in secrets
    assert "AWS_ACCESS_KEY" in secrets
    assert "OPENAI_API_KEY" not in secrets


def test_get_secrets_returns_empty_dict_when_no_secrets():
    """Test get_secrets returns empty dict when no secrets exist."""
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {"secrets": []}
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    secrets = workspace.get_secrets()

    assert secrets == {}


def test_get_secrets_raises_on_undefined_host():
    """Test get_secrets raises RuntimeError when host is undefined."""
    workspace = RemoteWorkspace(host="undefined", working_dir="/tmp")

    with pytest.raises(RuntimeError, match="Workspace host is not set"):
        workspace.get_secrets()


def test_get_mcp_config_returns_config():
    """Test get_mcp_config returns MCP configuration."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {
        "agent_settings": {
            "mcp_config": {
                "mcpServers": {
                    "shttp_0": {
                        "url": "https://mcp.example.com/api",
                        "transport": "streamable-http",
                    }
                }
            }
        },
        "conversation_settings": {},
        "llm_api_key_is_set": True,
    }
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    config = workspace.get_mcp_config()

    assert "mcpServers" in config
    assert "shttp_0" in config["mcpServers"]
    assert config["mcpServers"]["shttp_0"]["url"] == "https://mcp.example.com/api"

    # Verify API was called with correct headers
    call_args = mock_client.get.call_args
    assert call_args[1]["headers"]["X-Expose-Secrets"] == "plaintext"


def test_get_mcp_config_returns_empty_dict_when_no_config(monkeypatch):
    """Test get_mcp_config returns empty dict when no MCP config exists."""
    monkeypatch.setenv("ALLOW_SHORT_CONTEXT_WINDOWS", "true")
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {
        "agent_settings": {"llm": {"model": "gpt-4"}},
        "conversation_settings": {},
        "llm_api_key_is_set": True,
    }
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    config = workspace.get_mcp_config()

    assert config == {}


def test_get_mcp_config_returns_empty_dict_when_mcp_config_is_none(monkeypatch):
    """Test get_mcp_config returns empty dict when mcp_config is None."""
    monkeypatch.setenv("ALLOW_SHORT_CONTEXT_WINDOWS", "true")
    workspace = RemoteWorkspace(host="http://localhost:8000", working_dir="/tmp")

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.json.return_value = {
        "agent_settings": {"llm": {"model": "gpt-4"}, "mcp_config": None},
        "conversation_settings": {},
        "llm_api_key_is_set": True,
    }
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    config = workspace.get_mcp_config()

    assert config == {}


def test_get_mcp_config_raises_on_undefined_host():
    """Test get_mcp_config raises RuntimeError when host is undefined."""
    workspace = RemoteWorkspace(host="undefined", working_dir="/tmp")

    with pytest.raises(RuntimeError, match="Workspace host is not set"):
        workspace.get_mcp_config()


# ── Tests for Repository Cloning Methods ─────────────────────────────


def test_get_secret_value_returns_secret():
    """Test _get_secret_value fetches secret from agent server."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.text = "secret-token-value"
    mock_response.raise_for_status = Mock()
    mock_client.get.return_value = mock_response
    workspace._client = mock_client

    result = workspace._get_secret_value("github_token")

    assert result == "secret-token-value"
    mock_client.get.assert_called_once_with(
        "/api/settings/secrets/github_token",
        headers={"X-Session-API-Key": "test-key"},
    )


def test_get_secret_value_returns_none_on_404():
    """Test _get_secret_value returns None when secret not found."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    mock_client = MagicMock()
    mock_response = Mock()
    mock_response.status_code = 404
    mock_client.get.side_effect = httpx.HTTPStatusError(
        "Not Found", request=Mock(), response=mock_response
    )
    workspace._client = mock_client

    result = workspace._get_secret_value("nonexistent_secret")

    assert result is None


def test_get_secret_value_returns_none_when_host_undefined():
    """Test _get_secret_value returns None when host is undefined."""
    workspace = RemoteWorkspace(host="undefined", working_dir="/tmp")

    result = workspace._get_secret_value("github_token")

    assert result is None


def test_get_secret_value_validates_secret_name():
    """Test _get_secret_value validates secret name to prevent path traversal."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/tmp", api_key="test-key"
    )

    # Names with slashes should be rejected
    assert workspace._get_secret_value("../etc/passwd") is None
    assert workspace._get_secret_value("secrets/github") is None

    # Empty name should be rejected
    assert workspace._get_secret_value("") is None


def test_clone_repos_calls_helper():
    """Test clone_repos delegates to helper function."""
    from openhands.sdk.workspace.repo import CloneResult, RepoMapping

    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    with patch("openhands.sdk.workspace.remote.base._clone_repos_helper") as mock_clone:
        expected_result = CloneResult(
            success_count=1,
            failed_repos=[],
            repo_mappings={
                "https://github.com/owner/repo": RepoMapping(
                    url="https://github.com/owner/repo",
                    dir_name="repo",
                    local_path="/workspace/repo",
                )
            },
        )
        mock_clone.return_value = expected_result

        result = workspace.clone_repos(["https://github.com/owner/repo"])

        assert result == expected_result
        mock_clone.assert_called_once()

        # Verify token_fetcher is workspace's _get_secret_value
        call_kwargs = mock_clone.call_args[1]
        assert call_kwargs["token_fetcher"] == workspace._get_secret_value


def test_clone_repos_normalizes_input_formats():
    """Test clone_repos accepts strings, dicts, and RepoSource objects."""
    from openhands.sdk.workspace.repo import CloneResult, RepoSource

    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    with patch("openhands.sdk.workspace.remote.base._clone_repos_helper") as mock_clone:
        mock_clone.return_value = CloneResult(0, [], {})

        # Mix of input formats
        workspace.clone_repos(
            [
                "https://github.com/owner/repo1",  # string
                {"url": "https://gitlab.com/owner/repo2", "ref": "main"},  # dict
                RepoSource(url="https://bitbucket.org/owner/repo3"),  # RepoSource
            ]
        )

        # Verify all inputs were normalized to RepoSource
        call_kwargs = mock_clone.call_args[1]
        repos = call_kwargs["repos"]
        assert len(repos) == 3
        assert all(isinstance(r, RepoSource) for r in repos)


def test_clone_repos_uses_custom_target_dir():
    """Test clone_repos respects custom target directory."""
    from openhands.sdk.workspace.repo import CloneResult

    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    with patch("openhands.sdk.workspace.remote.base._clone_repos_helper") as mock_clone:
        mock_clone.return_value = CloneResult(0, [], {})

        workspace.clone_repos(
            ["https://github.com/owner/repo"],
            target_dir="/custom/path",
        )

        call_kwargs = mock_clone.call_args[1]
        assert call_kwargs["target_dir"] == Path("/custom/path")


def test_get_repos_context_delegates_to_helper():
    """Test get_repos_context delegates to helper function."""
    from openhands.sdk.workspace.repo import RepoMapping

    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    mappings = {
        "https://github.com/owner/repo": RepoMapping(
            url="https://github.com/owner/repo",
            dir_name="repo",
            local_path="/workspace/repo",
            ref="main",
        )
    }

    context = workspace.get_repos_context(mappings)

    assert "## Cloned Repositories" in context
    assert "https://github.com/owner/repo" in context
    assert "/workspace/repo" in context


def test_get_repos_context_empty_mappings():
    """Test get_repos_context returns empty string for empty mappings."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    context = workspace.get_repos_context({})

    assert context == ""


# ── Tests for Skill Loading Methods ──────────────────────────────────


def test_load_skills_from_agent_server_raises_when_not_initialized():
    """Test load_skills_from_agent_server raises when host is not set."""
    workspace = RemoteWorkspace(host="undefined", working_dir="/workspace")

    with pytest.raises(RuntimeError, match="Workspace not initialized"):
        workspace.load_skills_from_agent_server()


def test_load_skills_from_agent_server_calls_api():
    """Test load_skills_from_agent_server calls the agent server API."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    mock_response = Mock()
    mock_response.json.return_value = {
        "skills": [
            {
                "name": "test-skill",
                "content": "Test content",
                "description": "A test skill",
                "triggers": ["test"],
            }
        ],
        "sources": {"public": 1},
    }
    mock_response.raise_for_status = Mock()

    with patch.object(workspace.client, "post", return_value=mock_response):
        skills, context = workspace.load_skills_from_agent_server()

        assert len(skills) == 1
        assert skills[0].name == "test-skill"
        assert skills[0].content == "Test content"
        assert context.load_public_skills is False  # Skills were loaded


def test_load_skills_from_agent_server_falls_back_when_no_skills():
    """Test load_skills falls back to public skills when none loaded."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    mock_response = Mock()
    mock_response.json.return_value = {"skills": [], "sources": {}}
    mock_response.raise_for_status = Mock()

    with patch.object(workspace.client, "post", return_value=mock_response):
        skills, context = workspace.load_skills_from_agent_server()

        assert len(skills) == 0
        assert context.load_public_skills is True  # Fall back to public


def test_load_skills_from_agent_server_with_project_dirs():
    """Test load_skills_from_agent_server loads skills from multiple directories."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace", api_key="test-key"
    )

    # Return different skills for different calls
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        response = Mock()
        if call_count == 1:
            # Global skills call
            response.json.return_value = {
                "skills": [{"name": "global-skill", "content": "Global"}],
                "sources": {},
            }
        else:
            # Project-specific call
            response.json.return_value = {
                "skills": [
                    {"name": f"project-skill-{call_count}", "content": "Project"}
                ],
                "sources": {},
            }
        response.raise_for_status = Mock()
        return response

    with patch.object(workspace.client, "post", side_effect=side_effect) as mock_post:
        skills, context = workspace.load_skills_from_agent_server(
            project_dirs=["/workspace/repo1", "/workspace/repo2"]
        )

        # Should have loaded global skills + 2 project dirs = 3 calls
        assert mock_post.call_count == 3
        assert len(skills) >= 1  # At least the global skill
