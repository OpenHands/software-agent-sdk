"""VSCode service for managing Coder code-server in the agent server."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env


logger = get_logger(__name__)


class VSCodeService:
    """Service to manage VSCode server startup and token generation."""

    def __init__(
        self,
        port: int = 8001,
        connection_token: str | None = None,
        server_base_path: str | None = None,
    ):
        """Initialize VSCode service.

        Args:
            port: Port to run VSCode server on (default: 8001)
            workspace_path: Path to the workspace directory
            create_workspace: Whether to create the workspace directory if it doesn't
                exist
            server_base_path: Base path for the server (used in path-based routing)
        """
        self.port: int = port
        self.connection_token: str | None = connection_token
        self.server_base_path: str | None = server_base_path
        self.process: asyncio.subprocess.Process | None = None
        self._output_task: asyncio.Task | None = None
        self.openvscode_server_root: Path = Path(
            os.environ.get("CODE_SERVER_ROOT", "/openhands/.code-server")
        )
        self.extensions_dir: Path = (
            self.openvscode_server_root / "lib" / "vscode" / "extensions"
        )

    async def start(self) -> bool:
        """Start the VSCode server.

        Returns:
            True if started successfully, False otherwise
        """
        try:
            # Check if VSCode server binary exists
            if not self._check_vscode_available():
                logger.warning(
                    "VSCode server binary not found, VSCode will be disabled"
                )
                return False

            # Generate connection token if not already set
            if self.connection_token is None:
                self.connection_token = os.urandom(32).hex()

            # Check if port is available
            if not await self._is_port_available():
                logger.warning(
                    f"Port {self.port} is not available, VSCode will be disabled"
                )
                return False

            # Start VSCode server with extensions
            await self._start_vscode_process()

            logger.info(f"VSCode server started successfully on port {self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to start VSCode server: {e}")
            await self.stop()
            return False

    async def stop(self) -> None:
        """Stop the VSCode server."""
        if self.process:
            try:
                self._signal_process_group(signal.SIGTERM)
                await asyncio.wait_for(self._wait_for_exit(), timeout=5.0)
                logger.info("VSCode server stopped successfully")
            except TimeoutError:
                logger.warning("VSCode server did not stop gracefully, killing process")
                self._signal_process_group(signal.SIGKILL)
                await self.process.wait()
            except Exception as e:
                logger.error(f"Error stopping VSCode server: {e}")
            finally:
                if self._output_task:
                    self._output_task.cancel()
                    await asyncio.gather(self._output_task, return_exceptions=True)
                    self._output_task = None
                self.process = None

    async def set_connection_token(self, connection_token: str) -> None:
        """Restart a running server with a new connection token.

        The editor adapter reads its token only at startup, so the server is
        stopped and started again with the new one. A server that is not
        running keeps its token, so ``get_vscode_url`` doesn't advertise a
        server that isn't there (for example, in images without VSCode).
        """
        if not self.is_running() or connection_token == self.connection_token:
            return
        self.connection_token = connection_token
        await self.stop()
        await self.start()

    def get_vscode_url(
        self,
        base_url: str | None = None,
        workspace_dir: str = "workspace",
    ) -> str | None:
        """Get the VSCode URL with authentication token.

        When ``server_base_path`` is configured, the server only answers under
        that prefix (the adapter strips it before proxying to code-server),
        so the prefix is included in the returned URL.
        Without it, path-based-routing deployments are advertised a root URL
        that the server does not serve.

        Args:
            base_url: Base URL for the VSCode server
            workspace_dir: Path to workspace directory

        Returns:
            VSCode URL with token, or None if the server is not running
        """
        # A keyed server seeds the token before start() runs, so the token
        # alone does not mean a server is there to open.
        if self.connection_token is None or not self.is_running():
            return None

        if base_url is None:
            base_url = f"http://localhost:{self.port}"

        base = base_url.rstrip("/")
        if self.server_base_path:
            base = f"{base}/{self.server_base_path.strip('/')}"

        # Encode the values so a path with '&', '#', '?' or spaces decodes back
        # to the same folder; '/' stays readable.
        query = urlencode(
            {"tkn": self.connection_token, "folder": workspace_dir},
            quote_via=quote,
            safe="/",
        )
        return f"{base}/?{query}"

    def is_running(self) -> bool:
        """Check if VSCode server is running.

        Returns:
            True if running, False otherwise
        """
        return self.process is not None and self.process.returncode is None

    def _check_vscode_available(self) -> bool:
        """Check if VSCode server binary is available.

        Returns:
            True if available, False otherwise
        """
        vscode_binary = self.openvscode_server_root / "bin" / "code-server"
        return vscode_binary.exists() and vscode_binary.is_file()

    async def _is_port_available(self) -> bool:
        """Check if the specified port is available.

        Returns:
            True if port is available, False otherwise
        """
        try:
            # Try to bind to the port
            server = await asyncio.start_server(
                lambda _r, _w: None, "localhost", self.port
            )
            server.close()
            await server.wait_closed()
            return True
        except OSError:
            return False

    def _signal_process_group(self, sig: signal.Signals) -> None:
        """Signal the launcher script and the node server it runs.

        The launcher runs node as a child instead of exec'ing it, so signalling
        the script alone leaves node running and holding the port. The script
        leads its own process group (``start_new_session``), so its pid is the
        group id even after the script itself has exited.
        """
        if self.process is None:
            return
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass

    async def _wait_for_exit(self) -> None:
        """Wait for the launcher to exit and for node to release the port."""
        if self.process is None:
            return
        await self.process.wait()
        while not await self._is_port_available():
            await asyncio.sleep(0.1)

    def _proxy_script_path(self) -> Path:
        # In the frozen (PyInstaller) binary, __file__ points inside the PYZ
        # archive; bundled data files live under sys._MEIPASS instead.
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return (
                Path(bundle_root)
                / "openhands"
                / "agent_server"
                / ("code_server_proxy.cjs")
            )
        return Path(__file__).with_name("code_server_proxy.cjs")

    async def _start_vscode_process(self) -> None:
        """Start the VSCode server process."""
        config = json.dumps(
            {
                "root": str(self.openvscode_server_root),
                "port": self.port,
                "token": self.connection_token,
                "basePath": self.server_base_path,
            }
        ).encode()
        self.process = await asyncio.create_subprocess_exec(
            str(self.openvscode_server_root / "lib" / "node"),
            str(self._proxy_script_path()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=sanitized_env(),
            start_new_session=True,
        )
        assert self.process.stdin is not None
        self.process.stdin.write(config)
        await self.process.stdin.drain()
        self.process.stdin.close()

        # Wait for server to start (look for startup message)
        await self._wait_for_startup()
        self._output_task = asyncio.create_task(self._drain_output())

    async def _drain_output(self) -> None:
        if self.process and self.process.stdout:
            async for _line in self.process.stdout:
                pass

    async def _wait_for_startup(self) -> None:
        """Wait for VSCode server to start up."""
        if not self.process or not self.process.stdout:
            return

        try:
            # Read output until we see the server is ready
            timeout = 30  # 30 second timeout
            start_time = asyncio.get_event_loop().time()

            while (
                self.process.returncode is None
                and (asyncio.get_event_loop().time() - start_time) < timeout
            ):
                try:
                    line_bytes = await asyncio.wait_for(
                        self.process.stdout.readline(), timeout=1.0
                    )
                    if not line_bytes:
                        break

                    line = line_bytes.decode("utf-8", errors="ignore").strip()
                    logger.debug(f"VSCode server output: {line}")

                    # Look for startup indicators
                    if "OpenHands editor ready" in line:
                        logger.info("VSCode server startup detected")
                        return

                except TimeoutError:
                    continue

        except Exception as e:
            raise RuntimeError("Failed waiting for code-server startup") from e
        raise RuntimeError("code-server exited or timed out before startup")


# Global VSCode service instance
_vscode_service: VSCodeService | None = None


def get_vscode_service() -> VSCodeService | None:
    """Get the global VSCode service instance.

    Returns:
        VSCode service instance if enabled, None if disabled
    """
    global _vscode_service
    if _vscode_service is None:
        from openhands.agent_server.config import (
            get_default_config,
        )

        config = get_default_config()

        if not config.enable_vscode:
            logger.info("VSCode is disabled in configuration")
            return None
        else:
            connection_token = None
            if config.session_api_keys:
                connection_token = config.session_api_keys[0]
            _vscode_service = VSCodeService(
                port=config.vscode_port,
                connection_token=connection_token,
                server_base_path=config.vscode_base_path,
            )
    return _vscode_service
