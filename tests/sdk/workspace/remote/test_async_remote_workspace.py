"""Unit tests for AsyncRemoteWorkspace class."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from openhands.sdk.workspace.models import CommandResult, FileOperationResult
from openhands.sdk.workspace.remote.async_remote_workspace import AsyncRemoteWorkspace


def test_async_remote_workspace_initialization():
    """Test AsyncRemoteWorkspace can be initialized with required parameters."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", api_key="test-key", working_dir="workspace"
    )

    assert workspace.host == "http://localhost:8000"
    assert workspace.api_key == "test-key"


def test_async_remote_workspace_initialization_without_api_key():
    """Test AsyncRemoteWorkspace can be initialized without API key."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    assert workspace.host == "http://localhost:8000"
    assert workspace.api_key is None


def test_async_remote_workspace_host_normalization():
    """Test that host URL is normalized by removing trailing slash."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000/", working_dir="workspace"
    )

    assert workspace.host == "http://localhost:8000"


def test_async_client_property_lazy_initialization():
    """Test that client property creates httpx.AsyncClient lazily."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    # Client should be None initially
    assert workspace._client is None

    # Accessing client should create it
    client = workspace.client
    assert isinstance(client, httpx.AsyncClient)
    assert workspace._client is client

    # Subsequent access should return same client
    assert workspace.client is client


def test_async_headers_property_with_api_key():
    """Test _headers property includes API key when present."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", api_key="test-key", working_dir="workspace"
    )

    headers = workspace._headers
    assert headers == {"X-Session-API-Key": "test-key"}


def test_async_headers_property_without_api_key():
    """Test _headers property is empty when no API key."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    headers = workspace._headers
    assert headers == {}


@pytest.mark.asyncio
async def test_async_execute_method():
    """Test _execute method handles async generator protocol correctly."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    # Mock async client
    mock_client = AsyncMock()
    mock_response = Mock()
    mock_client.request.return_value = mock_response
    workspace._client = mock_client

    # Create a simple generator that yields request kwargs and returns a result
    def test_generator():
        yield {"method": "GET", "url": "http://test.com"}
        return "test_result"

    result = await workspace._execute(test_generator())

    assert result == "test_result"
    mock_client.request.assert_called_once_with(method="GET", url="http://test.com")


@pytest.mark.asyncio
@patch(
    "openhands.sdk.workspace.remote.async_remote_workspace.AsyncRemoteWorkspace._execute"
)
async def test_async_execute_command(mock_execute):
    """Test execute_command method calls _execute with correct generator."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    expected_result = CommandResult(
        command="echo hello",
        exit_code=0,
        stdout="hello\n",
        stderr="",
        timeout_occurred=False,
    )
    mock_execute.return_value = expected_result

    result = await workspace.execute_command("echo hello", cwd="/tmp", timeout=30.0)

    assert result == expected_result
    mock_execute.assert_called_once()

    # Verify the generator was created correctly
    generator_arg = mock_execute.call_args[0][0]
    assert hasattr(generator_arg, "__next__")


@pytest.mark.asyncio
@patch(
    "openhands.sdk.workspace.remote.async_remote_workspace.AsyncRemoteWorkspace._execute"
)
async def test_async_file_upload(mock_execute):
    """Test file_upload method calls _execute with correct generator."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    expected_result = FileOperationResult(
        success=True,
        source_path="/local/file.txt",
        destination_path="/remote/file.txt",
        file_size=100,
    )
    mock_execute.return_value = expected_result

    result = await workspace.file_upload("/local/file.txt", "/remote/file.txt")

    assert result == expected_result
    mock_execute.assert_called_once()

    # Verify the generator was created correctly
    generator_arg = mock_execute.call_args[0][0]
    assert hasattr(generator_arg, "__next__")


@pytest.mark.asyncio
@patch(
    "openhands.sdk.workspace.remote.async_remote_workspace.AsyncRemoteWorkspace._execute"
)
async def test_async_file_download(mock_execute):
    """Test file_download method calls _execute with correct generator."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    expected_result = FileOperationResult(
        success=True,
        source_path="/remote/file.txt",
        destination_path="/local/file.txt",
        file_size=100,
    )
    mock_execute.return_value = expected_result

    result = await workspace.file_download("/remote/file.txt", "/local/file.txt")

    assert result == expected_result
    mock_execute.assert_called_once()

    # Verify the generator was created correctly
    generator_arg = mock_execute.call_args[0][0]
    assert hasattr(generator_arg, "__next__")


@pytest.mark.asyncio
async def test_async_execute_command_with_path_objects():
    """Test execute_command works with Path objects for cwd."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    with patch.object(workspace, "_execute") as mock_execute:
        expected_result = CommandResult(
            command="ls",
            exit_code=0,
            stdout="file1.txt\n",
            stderr="",
            timeout_occurred=False,
        )
        mock_execute.return_value = expected_result

        result = await workspace.execute_command("ls", cwd=Path("/tmp/test"))

        assert result == expected_result
        mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_async_file_operations_with_path_objects():
    """Test file operations work with Path objects."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    with patch.object(workspace, "_execute") as mock_execute:
        expected_result = FileOperationResult(
            success=True,
            source_path="/local/file.txt",
            destination_path="/remote/file.txt",
            file_size=100,
        )
        mock_execute.return_value = expected_result

        # Test upload with Path objects
        result = await workspace.file_upload(
            Path("/local/file.txt"), Path("/remote/file.txt")
        )
        assert result == expected_result

        # Test download with Path objects
        result = await workspace.file_download(
            Path("/remote/file.txt"), Path("/local/file.txt")
        )
        assert result == expected_result


def test_async_inheritance():
    """Test AsyncRemoteWorkspace inherits from correct base classes."""
    from openhands.sdk.workspace.remote.remote_workspace_mixin import (
        RemoteWorkspaceMixin,
    )

    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    assert isinstance(workspace, RemoteWorkspaceMixin)


@pytest.mark.asyncio
async def test_async_execute_with_exception_handling():
    """Test _execute method handles exceptions in generator correctly."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    # Mock async client to raise an exception
    mock_client = AsyncMock()
    mock_client.request.side_effect = httpx.RequestError("Connection failed")
    workspace._client = mock_client

    def failing_generator():
        yield {"method": "GET", "url": "http://test.com"}
        return "should_not_reach_here"

    # The generator should handle the exception and not return the result
    # Since the exception occurs during client.request(), the generator will
    # not complete normally
    with pytest.raises(httpx.RequestError):
        await workspace._execute(failing_generator())


@pytest.mark.asyncio
async def test_async_execute_generator_completion():
    """Test _execute method properly handles StopIteration to get return value."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    # Mock async client
    mock_client = AsyncMock()
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

    result = await workspace._execute(test_generator())

    assert result == "final_result"
    assert mock_client.request.call_count == 2
    mock_client.request.assert_any_call(method="GET", url="http://test1.com")
    mock_client.request.assert_any_call(method="POST", url="http://test2.com")


@pytest.mark.asyncio
async def test_async_execute_multiple_yields():
    """Test _execute method handles multiple yields correctly."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    # Mock async client
    mock_client = AsyncMock()
    responses = [Mock(), Mock(), Mock()]
    mock_client.request.side_effect = responses
    workspace._client = mock_client

    def multi_yield_generator():
        # Multiple yields to simulate complex API interactions
        yield {"method": "POST", "url": "http://start.com"}
        yield {"method": "GET", "url": "http://poll.com"}
        yield {"method": "GET", "url": "http://result.com"}
        return "complex_result"

    result = await workspace._execute(multi_yield_generator())

    assert result == "complex_result"
    assert mock_client.request.call_count == 3
    mock_client.request.assert_any_call(method="POST", url="http://start.com")
    mock_client.request.assert_any_call(method="GET", url="http://poll.com")
    mock_client.request.assert_any_call(method="GET", url="http://result.com")


@pytest.mark.asyncio
async def test_async_concurrent_operations():
    """Test that multiple async operations can run concurrently."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    with patch.object(workspace, "_execute") as mock_execute:
        # Mock different results for different operations
        command_result = CommandResult(
            command="echo test",
            exit_code=0,
            stdout="test\n",
            stderr="",
            timeout_occurred=False,
        )
        upload_result = FileOperationResult(
            success=True,
            source_path="/local/file1.txt",
            destination_path="/remote/file1.txt",
            file_size=50,
        )
        download_result = FileOperationResult(
            success=True,
            source_path="/remote/file2.txt",
            destination_path="/local/file2.txt",
            file_size=75,
        )

        mock_execute.side_effect = [command_result, upload_result, download_result]

        # Run operations concurrently
        tasks = [
            workspace.execute_command("echo test"),
            workspace.file_upload("/local/file1.txt", "/remote/file1.txt"),
            workspace.file_download("/remote/file2.txt", "/local/file2.txt"),
        ]

        results = await asyncio.gather(*tasks)

        assert results[0] == command_result
        assert results[1] == upload_result
        assert results[2] == download_result
        assert mock_execute.call_count == 3


class MockHTTPResponse:
    """Mock HTTP response for urlopen."""

    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_returns_true_on_successful_health_check(mock_urlopen):
    """Test alive property returns True when health endpoint returns 2xx status."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    mock_urlopen.return_value = MockHTTPResponse(status=200)

    result = workspace.alive

    assert result is True
    mock_urlopen.assert_called_once_with("http://localhost:8000/health", timeout=5.0)


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_returns_true_on_204_status(mock_urlopen):
    """Test alive property returns True when health endpoint returns 204 No Content."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    mock_urlopen.return_value = MockHTTPResponse(status=204)

    result = workspace.alive

    assert result is True


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_returns_false_on_server_error(mock_urlopen):
    """Test alive property returns False when health endpoint returns 5xx status."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    mock_urlopen.return_value = MockHTTPResponse(status=500)

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_returns_false_on_client_error(mock_urlopen):
    """Test alive property returns False when health endpoint returns 4xx status."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    mock_urlopen.return_value = MockHTTPResponse(status=404)

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_returns_false_on_connection_error(mock_urlopen):
    """Test alive property returns False when connection fails."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    mock_urlopen.side_effect = Exception("Connection refused")

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_returns_false_on_timeout(mock_urlopen):
    """Test alive property returns False when request times out."""
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000", working_dir="workspace"
    )

    from urllib.error import URLError

    mock_urlopen.side_effect = URLError("timed out")

    result = workspace.alive

    assert result is False


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_constructs_correct_health_url(mock_urlopen):
    """Test alive property constructs correct health URL from host."""
    workspace = AsyncRemoteWorkspace(
        host="https://my-agent-server.example.com", working_dir="workspace"
    )

    mock_urlopen.return_value = MockHTTPResponse(status=200)

    _ = workspace.alive

    mock_urlopen.assert_called_once_with(
        "https://my-agent-server.example.com/health", timeout=5.0
    )


@patch("openhands.sdk.workspace.remote.async_remote_workspace.urlopen")
def test_async_alive_with_normalized_host(mock_urlopen):
    """Test alive property works correctly when host was normalized."""
    # Host with trailing slash gets normalized in model_post_init
    workspace = AsyncRemoteWorkspace(
        host="http://localhost:8000/", working_dir="workspace"
    )

    mock_urlopen.return_value = MockHTTPResponse(status=200)

    result = workspace.alive

    assert result is True
    # Should not have double slash
    mock_urlopen.assert_called_once_with("http://localhost:8000/health", timeout=5.0)


def test_async_alive_is_property():
    """Test that alive is a property, not a method."""
    assert isinstance(AsyncRemoteWorkspace.alive, property)
