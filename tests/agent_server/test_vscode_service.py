"""Tests for VSCode service."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.agent_server.vscode_service import (
    VSCodeService,
    get_vscode_service,
)


@pytest.fixture
def vscode_service(tmp_path):
    """Create a VSCode service instance for testing."""
    return VSCodeService(
        port=8001,
    )


@pytest.fixture
def mock_openvscode_binary(tmp_path):
    """Create a mock VSCode binary for testing."""
    openvscode_root = tmp_path / "openhands" / ".openvscode-server"
    openvscode_root.mkdir(parents=True)

    bin_dir = openvscode_root / "bin"
    bin_dir.mkdir()

    binary = bin_dir / "openvscode-server"
    binary.write_text("#!/bin/bash\necho 'mock vscode server'")
    binary.chmod(0o755)

    return openvscode_root


def test_vscode_service_initialization(tmp_path):
    """Test VSCode service initialization."""
    service = VSCodeService(port=8002)

    assert service.port == 8002
    assert service.connection_token is None
    assert service.server_base_path is None
    assert service.process is None


def test_vscode_service_initialization_with_server_base_path():
    """Test VSCode service initialization with server_base_path."""
    service = VSCodeService(port=8002, server_base_path="/test/vscode")

    assert service.port == 8002
    assert service.server_base_path == "/test/vscode"
    assert service.connection_token is None
    assert service.process is None


def test_check_vscode_available_false(vscode_service, tmp_path):
    """Test VSCode availability check when binary doesn't exist."""
    # Set a non-existent path
    vscode_service.openvscode_server_root = tmp_path / "nonexistent"
    assert not vscode_service._check_vscode_available()


def test_check_vscode_available_true(vscode_service, mock_openvscode_binary):
    """Test VSCode availability check when binary exists."""
    vscode_service.openvscode_server_root = mock_openvscode_binary
    assert vscode_service._check_vscode_available()


@pytest.mark.asyncio
async def test_is_port_available_true(tmp_path):
    """Test port availability check when port is free."""
    service = VSCodeService(port=0)  # Use port 0 to get any available port
    assert await service._is_port_available()


@pytest.mark.asyncio
async def test_is_port_available_false(tmp_path):
    """Test port availability check when port is occupied."""
    # Start a server on a specific port
    server = await asyncio.start_server(lambda r, w: None, "localhost", 0)
    port = server.sockets[0].getsockname()[1]

    service = VSCodeService(port=port)
    assert not await service._is_port_available()

    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_start_success(vscode_service, mock_openvscode_binary, tmp_path):
    """Test successful VSCode service start."""
    vscode_service.openvscode_server_root = mock_openvscode_binary

    with (
        patch.object(vscode_service, "_is_port_available", return_value=True),
        patch.object(vscode_service, "_start_vscode_process") as mock_start,
    ):
        result = await vscode_service.start()

        assert result is True
        assert vscode_service.connection_token is not None
        mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_start_no_binary(vscode_service, tmp_path):
    """Test VSCode service start when binary is not available."""
    # Set a non-existent path
    vscode_service.openvscode_server_root = tmp_path / "nonexistent"
    result = await vscode_service.start()

    assert result is False
    assert vscode_service.connection_token is None


@pytest.mark.asyncio
async def test_start_port_unavailable(vscode_service, mock_openvscode_binary):
    """Test VSCode service start when port is unavailable."""
    vscode_service.openvscode_server_root = mock_openvscode_binary

    with patch.object(vscode_service, "_is_port_available", return_value=False):
        result = await vscode_service.start()

        assert result is False
        assert (
            vscode_service.connection_token is not None
        )  # Token is generated before port check


@pytest.mark.asyncio
async def test_stop_with_process(vscode_service):
    """Test stopping VSCode service with running process."""
    mock_process = AsyncMock()
    mock_process.wait = AsyncMock()
    mock_process.terminate = MagicMock()  # Regular method, not async
    vscode_service.process = mock_process

    await vscode_service.stop()

    mock_process.terminate.assert_called_once()
    mock_process.wait.assert_called_once()
    assert vscode_service.process is None


@pytest.mark.asyncio
async def test_stop_with_timeout(vscode_service):
    """Test stopping VSCode service with timeout."""
    mock_process = AsyncMock()
    # First call to wait() should timeout, second call should succeed
    mock_process.wait.side_effect = [TimeoutError(), None]
    mock_process.terminate = MagicMock()  # Regular method, not async
    mock_process.kill = MagicMock()  # Regular method, not async
    vscode_service.process = mock_process

    await vscode_service.stop()

    mock_process.terminate.assert_called_once()
    mock_process.kill.assert_called_once()
    assert mock_process.wait.call_count == 2


@pytest.mark.asyncio
async def test_stop_no_process(vscode_service):
    """Test stopping VSCode service with no running process."""
    await vscode_service.stop()  # Should not raise any exceptionz


def test_get_vscode_url_no_token(vscode_service):
    """Test getting VSCode URL without token."""
    url = vscode_service.get_vscode_url()
    assert url is None


def test_get_vscode_url_with_token(vscode_service):
    """Test getting VSCode URL with token."""
    vscode_service.connection_token = "test-token-123"

    # Test with default base_url (should use configured port)
    url = vscode_service.get_vscode_url()
    expected_url = (
        f"http://localhost:{vscode_service.port}/?tkn=test-token-123&folder=workspace"
    )
    assert url == expected_url

    # Test with custom base_url
    custom_url = vscode_service.get_vscode_url(base_url="http://example.com:9000")
    assert custom_url == "http://example.com:9000/?tkn=test-token-123&folder=workspace"


def test_get_vscode_url_with_custom_port():
    """Test getting VSCode URL with custom port."""
    service = VSCodeService(port=9001)
    service.connection_token = "test-token-456"

    url = service.get_vscode_url()
    assert url == "http://localhost:9001/?tkn=test-token-456&folder=workspace"


def test_is_running_false(vscode_service):
    """Test is_running when no process."""
    assert not vscode_service.is_running()


def test_is_running_true(vscode_service):
    """Test is_running with active process."""
    mock_process = MagicMock()
    mock_process.returncode = None
    vscode_service.process = mock_process

    assert vscode_service.is_running()


def test_is_running_finished_process(vscode_service):
    """Test is_running with finished process."""
    mock_process = MagicMock()
    mock_process.returncode = 0
    vscode_service.process = mock_process

    assert not vscode_service.is_running()


@pytest.mark.asyncio
async def test_start_vscode_process(vscode_service, tmp_path):
    """Test starting VSCode process."""
    vscode_service.connection_token = "test-token"

    mock_process = AsyncMock()
    mock_process.stdout = AsyncMock()

    with (
        patch(
            "asyncio.create_subprocess_shell", return_value=mock_process
        ) as mock_create,
        patch.object(vscode_service, "_wait_for_startup") as mock_wait,
    ):
        await vscode_service._start_vscode_process()

        mock_create.assert_called_once()
        mock_wait.assert_called_once()
        assert vscode_service.process == mock_process


@pytest.mark.asyncio
async def test_start_vscode_process_with_server_base_path():
    """Test starting VSCode process with server_base_path includes the arg."""
    service = VSCodeService(
        port=8001, connection_token="test-token", server_base_path="/runtime/vscode"
    )

    mock_process = AsyncMock()
    mock_process.stdout = AsyncMock()

    with (
        patch(
            "asyncio.create_subprocess_shell", return_value=mock_process
        ) as mock_create,
        patch.object(service, "_wait_for_startup"),
    ):
        await service._start_vscode_process()

        # Verify the command includes --server-base-path
        cmd = mock_create.call_args[0][0]
        assert "--server-base-path /runtime/vscode" in cmd


@pytest.mark.asyncio
async def test_start_vscode_process_without_server_base_path():
    """Test starting VSCode process without server_base_path excludes the arg."""
    service = VSCodeService(port=8001, connection_token="test-token")

    mock_process = AsyncMock()
    mock_process.stdout = AsyncMock()

    with (
        patch(
            "asyncio.create_subprocess_shell", return_value=mock_process
        ) as mock_create,
        patch.object(service, "_wait_for_startup"),
    ):
        await service._start_vscode_process()

        # Verify the command does not include --server-base-path
        cmd = mock_create.call_args[0][0]
        assert "--server-base-path" not in cmd


@pytest.mark.asyncio
async def test_wait_for_startup_success(vscode_service):
    """Test waiting for VSCode startup with success message."""
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(
        side_effect=[
            b"Starting server...\n",
            b"Web UI available at http://localhost:8001\n",
            b"",
        ]
    )

    mock_process = AsyncMock()
    mock_process.stdout = mock_stdout
    mock_process.returncode = None
    vscode_service.process = mock_process

    await vscode_service._wait_for_startup()

    assert mock_stdout.readline.call_count >= 2


@pytest.mark.asyncio
async def test_wait_for_startup_timeout(vscode_service):
    """Test waiting for VSCode startup with timeout."""
    mock_stdout = AsyncMock()
    # Mock readline to raise TimeoutError a few times,
    # then return empty bytes to break the loop
    mock_stdout.readline = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), b""])

    mock_process = AsyncMock()
    mock_process.stdout = mock_stdout
    mock_process.returncode = None
    vscode_service.process = mock_process

    # Should not raise exception, just return
    await vscode_service._wait_for_startup()


# Tests for get_vscode_service with enable_vscode configuration


def test_get_vscode_service_enabled(tmp_path):
    """Test get_vscode_service returns VSCodeService when enabled."""
    with (
        patch("openhands.agent_server.config.get_default_config") as mock_config,
        patch("openhands.agent_server.vscode_service._vscode_service", None),
    ):
        mock_config.return_value.enable_vscode = True
        mock_config.return_value.vscode_port = 8001
        mock_config.return_value.vscode_base_path = None
        mock_config.return_value.session_api_keys = []

        service = get_vscode_service()

        assert isinstance(service, VSCodeService)


def test_get_vscode_service_disabled():
    """Test get_vscode_service returns None when disabled."""
    with (
        patch("openhands.agent_server.config.get_default_config") as mock_config,
        patch("openhands.agent_server.vscode_service._vscode_service", None),
    ):
        mock_config.return_value.enable_vscode = False

        service = get_vscode_service()

        assert service is None


def test_get_vscode_service_singleton():
    """Test get_vscode_service returns the same instance on multiple calls."""
    with (
        patch("openhands.agent_server.config.get_default_config") as mock_config,
        patch("openhands.agent_server.vscode_service._vscode_service", None),
    ):
        mock_config.return_value.enable_vscode = True
        mock_config.return_value.vscode_port = 8001
        mock_config.return_value.vscode_base_path = None
        mock_config.return_value.session_api_keys = []

        service1 = get_vscode_service()
        service2 = get_vscode_service()

        assert service1 is service2
        assert isinstance(service1, VSCodeService)


def test_get_vscode_service_with_custom_port():
    """Test get_vscode_service uses the configured port."""
    with (
        patch("openhands.agent_server.config.get_default_config") as mock_config,
        patch("openhands.agent_server.vscode_service._vscode_service", None),
    ):
        mock_config.return_value.enable_vscode = True
        mock_config.return_value.vscode_port = 9001
        mock_config.return_value.vscode_base_path = None
        mock_config.return_value.session_api_keys = []

        service = get_vscode_service()

        assert isinstance(service, VSCodeService)
        assert service.port == 9001


def test_get_vscode_service_with_base_path():
    """Test get_vscode_service passes vscode_base_path from config."""
    with (
        patch("openhands.agent_server.config.get_default_config") as mock_config,
        patch("openhands.agent_server.vscode_service._vscode_service", None),
    ):
        mock_config.return_value.enable_vscode = True
        mock_config.return_value.vscode_port = 8001
        mock_config.return_value.vscode_base_path = "/runtime-123/vscode"
        mock_config.return_value.session_api_keys = []

        service = get_vscode_service()

        assert isinstance(service, VSCodeService)
        assert service.server_base_path == "/runtime-123/vscode"


def test_vscode_service_with_different_ports():
    """Test VSCode service initialization with different ports."""
    service1 = VSCodeService(port=8001)
    service2 = VSCodeService(port=9001)

    assert service1.port == 8001
    assert service2.port == 9001


def test_vscode_port_configuration():
    """Test that vscode_port configuration is properly used."""
    import os

    from openhands.agent_server.config import Config, from_env

    # Test default value
    config = Config()
    assert config.vscode_port == 8001

    # Test environment variable override
    with patch.dict(os.environ, {"OH_VSCODE_PORT": "9999"}):
        config = from_env(Config, "OH")
        assert config.vscode_port == 9999


def test_vscode_base_path_configuration():
    """Test that vscode_base_path configuration is properly used."""
    import os

    from openhands.agent_server.config import Config, from_env

    # Test default value is None
    config = Config()
    assert config.vscode_base_path is None

    # Test environment variable override
    with patch.dict(os.environ, {"OH_VSCODE_BASE_PATH": "/runtime-abc/vscode"}):
        config = from_env(Config, "OH")
        assert config.vscode_base_path == "/runtime-abc/vscode"
