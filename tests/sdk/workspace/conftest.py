"""Fixtures for workspace tests."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock

import httpx
import pytest

from openhands.sdk.workspace.models import CommandResult, FileOperationResult


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx.Client for testing."""
    return MagicMock(spec=httpx.Client)


@pytest.fixture
def mock_httpx_async_client():
    """Create a mock httpx.AsyncClient for testing."""
    return MagicMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_httpx_response():
    """Create a mock httpx.Response for testing."""
    response = Mock(spec=httpx.Response)
    response.raise_for_status = Mock()
    response.json = Mock()
    response.content = b"test content"
    return response


@pytest.fixture
def sample_command_result():
    """Create a sample CommandResult for testing."""
    return CommandResult(
        command="echo 'hello'",
        exit_code=0,
        stdout="hello\n",
        stderr="",
        timeout_occurred=False,
    )


@pytest.fixture
def sample_file_operation_result():
    """Create a sample FileOperationResult for testing."""
    return FileOperationResult(
        success=True,
        source_path="/tmp/source.txt",
        destination_path="/tmp/dest.txt",
        file_size=100,
        error=None,
    )


@pytest.fixture
def temp_file():
    """Create a temporary file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content")
        temp_path = Path(f.name)

    yield temp_path

    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)
