"""VSCode service for managing OpenVSCode Server in the agent server."""

import asyncio
import os
from pathlib import Path

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class VSCodeService:
    """Service to manage VSCode server startup and token generation."""

    def __init__(
        self,
        port: int = 8001,
        connection_token: str | None = None,
    ):
        """Initialize VSCode service.

        Args:
            port: Port to run VSCode server on (default: 8001)
            workspace_path: Path to the workspace directory
            create_workspace: Whether to create the workspace directory if it doesn't
                exist
        """
        self.port: int = port
        self.connection_token: str | None = connection_token
        self.process: asyncio.subprocess.Process | None = None
        self.openvscode_server_root: Path = Path("/openhands/.openvscode-server")
        self.extensions_dir: Path = self.openvscode_server_root / "extensions"

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
            return False

    async def stop(self) -> None:
        """Stop the VSCode server."""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                logger.info("VSCode server stopped successfully")
            except TimeoutError:
                logger.warning("VSCode server did not stop gracefully, killing process")
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.error(f"Error stopping VSCode server: {e}")
            finally:
                self.process = None

    def get_vscode_url(
        self,
        base_url: str | None = None,
        workspace_dir: str = "workspace",
    ) -> str | None:
        """Get the VSCode URL with authentication token.

        Args:
            base_url: Base URL for the VSCode server
            workspace_dir: Path to workspace directory

        Returns:
            VSCode URL with token, or None if not available
        """
        if self.connection_token is None:
            return None

        if base_url is None:
            base_url = f"http://localhost:{self.port}"

        return f"{base_url}/?tkn={self.connection_token}&folder={workspace_dir}"

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
        vscode_binary = self.openvscode_server_root / "bin" / "openvscode-server"
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

    async def _start_vscode_process(self) -> None:
        """Start the VSCode server process."""
        extensions_arg = (
            f"--extensions-dir {self.extensions_dir} "
            if self.extensions_dir.exists()
            else ""
        )
        cmd = (
            f"exec {self.openvscode_server_root}/bin/openvscode-server "
            f"--host 0.0.0.0 "
            f"--connection-token {self.connection_token} "
            f"--port {self.port} "
            f"{extensions_arg}"
            f"--disable-workspace-trust\n"
        )

        # Start the process
        self.process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        # Wait for server to start (look for startup message)
        await self._wait_for_startup()

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
                    if "Web UI available at" in line or "Server bound to" in line:
                        logger.info("VSCode server startup detected")
                        break

                except TimeoutError:
                    continue

        except Exception as e:
            logger.warning(f"Error waiting for VSCode startup: {e}")


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
                port=config.vscode_port, connection_token=connection_token
            )
    return _vscode_service
