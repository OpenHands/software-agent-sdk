"""Tests for VSCode service."""

import asyncio
import stat
from pathlib import Path
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


def test_get_vscode_url_includes_server_base_path():
    """Test that a configured server_base_path appears in the URL.

    The base path is passed to openvscode-server as ``--server-base-path``, so
    the server only answers under that prefix — a root URL would not resolve.
    """
    service = VSCodeService(port=19000, server_base_path="/vscode")
    service.connection_token = "test-token-789"

    assert (
        service.get_vscode_url()
        == "http://localhost:19000/vscode/?tkn=test-token-789&folder=workspace"
    )


def test_get_vscode_url_base_path_with_caller_supplied_base_url():
    """Test that the base path is appended to a caller-supplied base_url.

    Callers behind a reverse proxy pass their own public origin; they still
    cannot know the server's base path, so it must be applied here. Base paths
    are normalized so '/vscode', 'vscode' and a trailing-slash base_url all
    produce the same single-slash URL.
    """
    service = VSCodeService(port=19000, server_base_path="vscode")
    service.connection_token = "test-token-789"

    expected = "https://example.com/vscode/?tkn=test-token-789&folder=%2Fsrv%2Fwork"
    assert (
        service.get_vscode_url(
            base_url="https://example.com", workspace_dir="%2Fsrv%2Fwork"
        )
        == expected
    )
    assert (
        service.get_vscode_url(
            base_url="https://example.com/", workspace_dir="%2Fsrv%2Fwork"
        )
        == expected
    )


def test_get_vscode_url_without_base_path_is_unchanged():
    """Test that URLs are byte-identical when no base path is configured.

    Downstream clients (OpenHands-CLI, app-server, Enterprise) that do not set
    ``vscode_base_path`` must see exactly the previous output.
    """
    service = VSCodeService(port=8001)
    service.connection_token = "test-token-000"

    assert (
        service.get_vscode_url()
        == "http://localhost:8001/?tkn=test-token-000&folder=workspace"
    )
    assert (
        service.get_vscode_url(base_url="http://example.com:9000")
        == "http://example.com:9000/?tkn=test-token-000&folder=workspace"
    )


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
            "asyncio.create_subprocess_exec", return_value=mock_process
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
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_create,
        patch.object(service, "_wait_for_startup"),
    ):
        await service._start_vscode_process()

        # Verify the command includes --server-base-path
        argv = list(mock_create.call_args[0])
        assert "--server-base-path" in argv
        assert argv[argv.index("--server-base-path") + 1] == "/runtime/vscode"


@pytest.mark.asyncio
async def test_start_vscode_process_without_server_base_path():
    """Test starting VSCode process without server_base_path excludes the arg."""
    service = VSCodeService(port=8001, connection_token="test-token")

    mock_process = AsyncMock()
    mock_process.stdout = AsyncMock()

    with (
        patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_create,
        patch.object(service, "_wait_for_startup"),
    ):
        await service._start_vscode_process()

        # Verify the command does not include --server-base-path
        assert "--server-base-path" not in list(mock_create.call_args[0])


@pytest.mark.asyncio
async def test_start_vscode_process_keeps_token_out_of_argv():
    """The token must never reach the process table.

    The agent runs bash in this same container, so an argument list readable by
    `ps` is readable by the agent. openvscode-server's own docs recommend
    `--connection-token-file` for exactly this reason.
    """
    service = VSCodeService(port=8001, connection_token="s3cr3t-token-value")

    mock_process = AsyncMock()
    mock_process.stdout = AsyncMock()

    with (
        patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_create,
        patch.object(service, "_wait_for_startup"),
    ):
        await service._start_vscode_process()

    argv = [str(arg) for arg in mock_create.call_args[0]]
    assert "--connection-token" not in argv
    assert not any("s3cr3t-token-value" in arg for arg in argv)

    token_file = Path(argv[argv.index("--connection-token-file") + 1])
    assert token_file.read_text() == "s3cr3t-token-value"

    service._remove_connection_token_file()


@pytest.mark.asyncio
async def test_connection_token_file_is_not_world_readable():
    """The token file is only useful if its permissions hold up."""
    service = VSCodeService(port=8001, connection_token="test-token")

    token_file = service._write_connection_token_file()
    try:
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(token_file.parent.stat().st_mode) == 0o700
    finally:
        service._remove_connection_token_file()


@pytest.mark.asyncio
async def test_connection_token_file_removed_on_stop():
    """Test that stopping the service cleans up the token file."""
    service = VSCodeService(port=8001, connection_token="test-token")
    token_file = service._write_connection_token_file()
    assert token_file.exists()

    await service.stop()

    assert not token_file.exists()
    assert not token_file.parent.exists()


@pytest.mark.asyncio
async def test_connection_token_file_removed_when_start_fails(mock_openvscode_binary):
    """A start that never completes still has to clean up after itself.

    Nothing calls stop() for a server that never came up, so the failure path
    owns the token file it wrote.
    """
    service = VSCodeService(port=8001, connection_token="test-token")
    service.openvscode_server_root = mock_openvscode_binary

    with (
        patch.object(service, "_is_port_available", return_value=True),
        patch.object(
            service, "_start_vscode_process", side_effect=OSError("no such binary")
        ),
    ):
        # The token file is written inside _start_vscode_process, so stand one
        # up first to represent the case where the failure comes after it.
        token_file = service._write_connection_token_file()
        assert await service.start() is False

    assert not token_file.exists()


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
