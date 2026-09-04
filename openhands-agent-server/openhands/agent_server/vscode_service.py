"""VSCode service for managing OpenVSCode Server in the agent server."""

import asyncio
import hashlib
import hmac
import os
import shutil
import tempfile
from pathlib import Path

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env


logger = get_logger(__name__)


# Domain separator for `derive_connection_token`. Anything that wants to build
# the editor URL without asking the agent server for it must derive the token
# the same way, so this string is part of the contract with those callers and
# cannot change without changing the `:v1` suffix.
VSCODE_TOKEN_DERIVATION_INFO = b"openhands-agent-server:vscode-connection-token:v1"


def derive_connection_token(session_api_key: str) -> str:
    """Derive the editor's connection token from a session API key.

    The editor's token travels in the URL's ``?tkn=`` query parameter, so it
    lands in browser history, in ``Referer`` headers from the pages the
    workbench renders, and in the access log of anything proxying the editor.
    Using the session API key itself there means each of those is a disclosure
    of the credential that authenticates every ``/api/*`` call on this server.

    Deriving instead keeps the property that made sharing attractive in the
    first place (see #793): a caller holding the session API key can still
    compute the editor URL on its own, with no round trip to the agent server.
    What it loses is the reverse direction — the derived token is a one-way
    function of the key, so disclosing it no longer discloses API access.

    A hex digest also always satisfies the ``^[0-9A-Za-z_-]+$`` that
    openvscode-server enforces on connection tokens, which an arbitrary session
    API key does not: a key containing so much as a ``.`` currently makes the
    editor refuse to start with a token parse error.
    """
    return hmac.new(
        session_api_key.encode("utf-8"),
        VSCODE_TOKEN_DERIVATION_INFO,
        hashlib.sha256,
    ).hexdigest()


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
        self.openvscode_server_root: Path = Path("/openhands/.openvscode-server")
        self.extensions_dir: Path = self.openvscode_server_root / "extensions"
        self._token_dir: Path | None = None

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
            # The token file outlives a failed start otherwise: nothing will
            # call stop() for a server that never came up.
            self._remove_connection_token_file()
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
        self._remove_connection_token_file()

    def get_vscode_url(
        self,
        base_url: str | None = None,
        workspace_dir: str = "workspace",
    ) -> str | None:
        """Get the VSCode URL with authentication token.

        When ``server_base_path`` is configured, the server only answers under
        that prefix (it is passed to openvscode-server as
        ``--server-base-path``), so the prefix is included in the returned URL.
        Without it, path-based-routing deployments are advertised a root URL
        that the server does not serve.

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

        base = base_url.rstrip("/")
        if self.server_base_path:
            base = f"{base}/{self.server_base_path.strip('/')}"

        return f"{base}/?tkn={self.connection_token}&folder={workspace_dir}"

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

    def _write_connection_token_file(self) -> Path:
        """Write the connection token to a file only this user can read.

        openvscode-server's own option documentation recommends
        ``--connection-token-file`` over ``--connection-token`` on a multi-user
        system precisely because the latter "can be seen by other users using
        ``ps`` or similar commands". That applies here: the agent runs bash in
        this same container, so anything in the server's argv is readable by the
        agent itself.

        Returns:
            Path of the token file, inside a directory owned by this process.
        """
        if self.connection_token is None:
            raise ValueError("Cannot write a connection token file without a token")

        # A restart must not leave the previous run's file behind.
        self._remove_connection_token_file()

        # mkdtemp is 0o700 and O_EXCL|0o600 creates the file with its final
        # permissions, so there is no window in which either is readable by
        # another user.
        token_dir = Path(tempfile.mkdtemp(prefix="openhands-vscode-"))
        token_file = token_dir / "connection-token"
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(self.connection_token)
        self._token_dir = token_dir
        return token_file

    def _remove_connection_token_file(self) -> None:
        """Remove the token file and its directory, if one was written."""
        if self._token_dir is None:
            return
        shutil.rmtree(self._token_dir, ignore_errors=True)
        self._token_dir = None

    async def _start_vscode_process(self) -> None:
        """Start the VSCode server process."""
        # An argument list rather than a shell string: `server_base_path` and
        # the extensions path are configuration, and interpolating them into a
        # command line means a value containing a space or a `;` is a broken
        # server at best. There is no shell to `exec` past either, so
        # `self.process` is the server itself and `terminate()` reaches it.
        argv = [
            str(self.openvscode_server_root / "bin" / "openvscode-server"),
            "--host",
            "0.0.0.0",
            "--connection-token-file",
            str(self._write_connection_token_file()),
            "--port",
            str(self.port),
        ]
        if self.extensions_dir.exists():
            argv += ["--extensions-dir", str(self.extensions_dir)]
        if self.server_base_path:
            argv += ["--server-base-path", self.server_base_path]
        argv.append("--disable-workspace-trust")

        # Start the process
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=sanitized_env(),
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
            # Derived, not copied. A caller that knows the session API key can
            # still build the editor URL without calling this server — see
            # `derive_connection_token` — but the token in that URL is no longer
            # usable as an API credential.
            connection_token = None
            if config.session_api_keys:
                connection_token = derive_connection_token(config.session_api_keys[0])
            _vscode_service = VSCodeService(
                port=config.vscode_port,
                connection_token=connection_token,
                server_base_path=config.vscode_base_path,
            )
    return _vscode_service
