from collections.abc import Generator
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.request import urlopen

import httpx
from pydantic import PrivateAttr

from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.workspace.models import CommandResult, FileOperationResult
from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin


class AsyncRemoteWorkspace(RemoteWorkspaceMixin):
    """Async Remote Workspace Implementation."""

    _client: httpx.AsyncClient | None = PrivateAttr(default=None)

    async def __aenter__(self) -> Self:
        """Enter a workspace whose HTTP pool is closed on context exit."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client without releasing the server's runtime."""
        await self.reset_client()

    async def get_server_info(self) -> dict[str, Any]:
        """Return server metadata, matching RemoteWorkspace.get_server_info."""
        response = await self.client.get("/server_info")
        response.raise_for_status()
        return response.json()

    async def reset_client(self) -> None:
        """Reset the HTTP client to force re-initialization.

        This is useful when connection parameters (host, api_key) have changed
        and the client needs to be recreated with new values.
        """
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        client = self._client
        if client is None:
            # Configure reasonable timeouts for HTTP requests
            # - connect: 10 seconds to establish connection
            # - read: 60 seconds to read response (for LLM operations)
            # - write: 10 seconds to send request
            # - pool: 10 seconds to get connection from pool
            timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
            client = httpx.AsyncClient(
                base_url=self.host, timeout=timeout, headers=self._headers
            )
            self._client = client
        return client

    async def _execute(self, generator: Generator[dict[str, Any], httpx.Response, Any]):
        try:
            kwargs = next(generator)
            while True:
                response = await self.client.request(**kwargs)
                kwargs = generator.send(response)
        except StopIteration as e:
            return e.value

    async def start_command(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 30,
    ) -> str:
        """Start a command and return its ID without waiting for completion."""
        return await self._execute(self._start_command_generator(command, cwd, timeout))

    async def get_command_output(
        self, command_id: str | None = None
    ) -> dict[str, Any] | None:
        """Read the latest output; a missing exit code means it is still running."""
        return await self._execute(self._get_command_output_generator(command_id))

    async def get_runtime_session_key(self) -> str:
        """Get the scoped worker credential for this conversation runtime."""
        return await self._execute(self._runtime_lifecycle_generator(release=False))

    async def release_runtime(self) -> None:
        """Release execution resources while retaining conversation history."""
        await self._execute(self._runtime_lifecycle_generator(release=True))

    async def execute_command(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 30.0,
    ) -> CommandResult:
        """Execute a bash command on the remote system.

        This method starts a bash command via the remote agent server API,
        then polls for the output until the command completes.

        Args:
            command: The bash command to execute
            cwd: Working directory (optional)
            timeout: Timeout in seconds

        Returns:
            CommandResult: Result with stdout, stderr, exit_code, and other metadata
        """
        generator = self._execute_command_generator(command, cwd, timeout)
        result = await self._execute(generator)
        return result

    async def file_upload(
        self,
        source_path: str | Path | bytes,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Upload a file to the remote system.

        Reads the local file and sends it to the remote system via HTTP API.

        Args:
            source_path: Local file path or in-memory bytes
            destination_path: Path where the file should be uploaded on remote system

        Returns:
            FileOperationResult: Result with success status and metadata
        """
        generator = self._file_upload_generator(source_path, destination_path)
        result = await self._execute(generator)
        return result

    async def file_download(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Download a file from the remote system.

        Requests the file from the remote system via HTTP API and saves it locally.

        Args:
            source_path: Path to the source file on remote system
            destination_path: Path where the file should be saved locally

        Returns:
            FileOperationResult: Result with success status and metadata
        """
        generator = self._file_download_generator(source_path, destination_path)
        result = await self._execute(generator)
        return result

    async def git_changes(self, path: str | Path) -> list[GitChange]:
        """Get the git changes for the repository at the path given.

        Args:
            path: Path to the git repository

        Returns:
            list[GitChange]: List of changes

        Raises:
            Exception: If path is not a git repository or getting changes failed
        """
        generator = self._git_changes_generator(path)
        result = await self._execute(generator)
        return result

    async def git_diff(self, path: str | Path) -> GitDiff:
        """Get the git diff for the file at the path given.

        Args:
            path: Path to the file

        Returns:
            GitDiff: Git diff

        Raises:
            Exception: If path is not a git repository or getting diff failed
        """
        generator = self._git_diff_generator(path)
        result = await self._execute(generator)
        return result

    @property
    def alive(self) -> bool:
        """Check if the remote workspace is alive by querying the health endpoint.

        Returns:
            True if the health endpoint returns a successful response, False otherwise.
        """
        try:
            health_url = f"{self.host}/health"
            with urlopen(health_url, timeout=5.0) as resp:
                status = getattr(resp, "status", 200)
                return 200 <= status < 300
        except Exception:
            return False
