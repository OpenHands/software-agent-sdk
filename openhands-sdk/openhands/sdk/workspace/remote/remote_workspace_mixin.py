import logging
import time
from collections.abc import Generator
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.utils.path import to_posix_path
from openhands.sdk.workspace.models import CommandResult, FileOperationResult


_logger = logging.getLogger(__name__)


def _remote_path(path: str | Path) -> str:
    return to_posix_path(path)


def _join_remote_path(base: str | Path, path: str | Path) -> str:
    path_str = _remote_path(path)
    if path_str.startswith("/") or PureWindowsPath(path_str).is_absolute():
        return path_str

    base_str = _remote_path(base)
    prefix = "/" if base_str.startswith("/") else ""
    base_parts = [part for part in base_str.split("/") if part]
    path_parts = [part for part in path_str.split("/") if part]
    return prefix + "/".join(base_parts + path_parts)


class RemoteWorkspaceMixin(BaseModel):
    """Mixin providing remote workspace operations.
    This allows the same code to be used for sync and async."""

    host: str = Field(description="The remote host URL for the workspace.")
    api_key: str | None = Field(
        default=None, description="API key for authenticating with the remote host."
    )
    working_dir: str = Field(
        description="The working directory for agent operations and tool execution."
    )
    read_timeout: float = Field(
        default=600.0,
        description="Timeout in seconds for reading operations of httpx.Client.",
    )
    max_connections: int | None = Field(
        default=None,
        description="Maximum number of connections for httpx.Client. "
        "None means no limit, useful for running many conversations in parallel.",
    )

    runtime_conversation_id: UUID | None = Field(
        default=None,
        frozen=True,
        description="Conversation runtime scope; None uses the host workspace.",
    )

    @property
    def api_prefix(self) -> str:
        """The immutable runtime scope used by file, command, and Git operations."""
        if self.runtime_conversation_id is None:
            return "/api"
        return f"/api/conversations/{self.runtime_conversation_id}"

    def model_post_init(self, context: Any) -> None:
        # Set up remote host
        self.host = self.host.rstrip("/")
        return super().model_post_init(context)

    @property
    def _headers(self):
        headers = {}
        if self.api_key:
            headers["X-Session-API-Key"] = self.api_key
        return headers

    def _start_command_generator(
        self,
        command: str,
        cwd: str | Path | None,
        timeout: float,
    ) -> Generator[dict[str, Any], httpx.Response, str]:
        payload: dict[str, Any] = {"command": command, "timeout": int(timeout)}
        if cwd is not None:
            payload["cwd"] = _remote_path(cwd)
        response = yield {
            "method": "POST",
            "url": f"{self.host}{self.api_prefix}/bash/start_bash_command",
            "json": payload,
            "headers": self._headers,
            "timeout": timeout + 5,
        }
        response.raise_for_status()
        command_id = response.json().get("id")
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("Agent Server returned no background command ID")
        return command_id

    def _search_command_output_generator(
        self,
        command_id: str | None,
        *,
        after_order: int | None = None,
        timeout: float = 60,
    ) -> Generator[dict[str, Any], httpx.Response, dict[str, Any]]:
        params: dict[str, str | int] = {
            "kind__eq": "BashOutput",
            "sort_order": "TIMESTAMP_DESC" if after_order is None else "TIMESTAMP",
            "limit": 1 if after_order is None else 100,
        }
        if command_id is not None:
            params["command_id__eq"] = command_id
        if after_order is not None and after_order >= 0:
            params["order__gt"] = after_order
        response = yield {
            "method": "GET",
            "url": f"{self.host}{self.api_prefix}/bash/bash_events/search",
            "params": params,
            "headers": self._headers,
            "timeout": timeout,
        }
        response.raise_for_status()
        return response.json()

    def _get_command_output_generator(
        self,
        command_id: str | None,
    ) -> Generator[dict[str, Any], httpx.Response, dict[str, Any] | None]:
        page = yield from self._search_command_output_generator(command_id)
        return next(iter(page.get("items", [])), None)

    def _runtime_lifecycle_generator(
        self,
        *,
        release: bool,
    ) -> Generator[dict[str, Any], httpx.Response, str | None]:
        if self.runtime_conversation_id is None:
            raise ValueError("Runtime lifecycle requires a conversation scope")
        response = yield {
            "method": "DELETE" if release else "POST",
            "url": f"{self.host}{self.api_prefix}/runtime"
            + ("" if release else "/credentials"),
            "headers": self._headers,
            "timeout": 60,
        }
        if release and response.status_code == 404:
            return None
        response.raise_for_status()
        if not release:
            key = response.json().get("session_api_key")
            if not isinstance(key, str) or not key:
                raise ValueError("Runtime returned an empty session credential")
            return key
        return None

    def _execute_command_generator(
        self,
        command: str,
        cwd: str | Path | None,
        timeout: float,
    ) -> Generator[dict[str, Any], httpx.Response, CommandResult]:
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
        _logger.debug("Executing remote command")

        try:
            command_id = yield from self._start_command_generator(command, cwd, timeout)

            _logger.debug(f"Started command with ID: {command_id}")

            # Step 2: Poll for output until command completes
            start_time = time.time()
            stdout_parts = []
            stderr_parts = []
            exit_code = None
            last_order = -1  # Track highest order seen to fetch only new events
            seen_event_ids: set[str] = set()  # Track seen IDs to detect duplicates

            while time.time() - start_time < timeout:
                search_result = yield from self._search_command_output_generator(
                    command_id,
                    after_order=last_order,
                    timeout=timeout,
                )

                # Process BashOutput events
                for event in search_result.get("items", []):
                    if event.get("kind") == "BashOutput":
                        # Check for duplicates - safety check in case caller
                        # forgets to add kind__eq filter or API has a bug
                        event_id = event.get("id")
                        if event_id is not None:
                            if event_id in seen_event_ids:
                                raise RuntimeError(
                                    f"Duplicate event received: {event_id}. "
                                    "This should not happen with order__gt "
                                    "filtering and kind filtering."
                                )
                            seen_event_ids.add(event_id)

                        # Track the highest order we've seen
                        event_order = event.get("order")
                        if event_order is not None and event_order > last_order:
                            last_order = event_order

                        if event.get("stdout"):
                            stdout_parts.append(event["stdout"])
                        if event.get("stderr"):
                            stderr_parts.append(event["stderr"])
                        if event.get("exit_code") is not None:
                            exit_code = event["exit_code"]

                # If we have an exit code, the command is complete
                if exit_code is not None:
                    break

                # Wait a bit before polling again
                time.sleep(0.1)

            # If we timed out waiting for completion
            if exit_code is None:
                _logger.warning(
                    "Command timed out after %s seconds (command_id=%s)",
                    timeout,
                    command_id,
                )
                exit_code = -1
                stderr_parts.append(f"Command timed out after {timeout} seconds")

            # Combine all output parts
            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)

            return CommandResult(
                command=command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timeout_occurred=exit_code == -1 and "timed out" in stderr,
            )

        except Exception as e:
            _logger.error(
                "Remote command execution failed (error_type=%s)",
                type(e).__name__,
            )
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"Remote execution error: {str(e)}",
                timeout_occurred=False,
            )

    def _file_upload_generator(
        self,
        source_path: str | Path | bytes,
        destination_path: str | Path,
    ) -> Generator[dict[str, Any], httpx.Response, FileOperationResult]:
        """Upload a file to the remote system.

        Reads the local file and sends it to the remote system via HTTP API.

        Args:
            source_path: Path to the local source file
            destination_path: Path where the file should be uploaded on remote system

        Returns:
            FileOperationResult: Result with success status and metadata
        """
        source = Path("upload") if isinstance(source_path, bytes) else Path(source_path)
        destination = Path(destination_path)
        destination_remote = _remote_path(destination_path)

        _logger.debug(f"Remote file upload: {source} -> {destination}")

        try:
            # Read the file content
            if isinstance(source_path, bytes):
                file_content = source_path
            else:
                with open(source, "rb") as f:
                    file_content = f.read()

            # Prepare the upload
            files = {"file": (source.name, file_content)}

            # Make HTTP call using query parameter for path
            response: httpx.Response = yield {
                "method": "POST",
                "url": f"{self.host}{self.api_prefix}/file/upload",
                "params": {"path": destination_remote},
                "files": files,
                "headers": self._headers,
                "timeout": 60.0,
            }
            response.raise_for_status()
            result_data = response.json()

            # Convert the API response to our model
            return FileOperationResult(
                success=result_data.get("success", True),
                source_path=str(source),
                destination_path=destination_remote,
                file_size=result_data.get("file_size"),
                error=result_data.get("error"),
            )

        except Exception as e:
            _logger.error(f"Remote file upload failed: {e}")
            return FileOperationResult(
                success=False,
                source_path=str(source),
                destination_path=destination_remote,
                error=str(e),
            )

    def _file_download_generator(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> Generator[dict[str, Any], httpx.Response, FileOperationResult]:
        """Download a file from the remote system.

        Requests the file from the remote system via HTTP API and saves it locally.

        Args:
            source_path: Path to the source file on remote system
            destination_path: Path where the file should be saved locally

        Returns:
            FileOperationResult: Result with success status and metadata
        """
        source = Path(source_path)
        destination = Path(destination_path)
        source_remote = _remote_path(source_path)

        _logger.debug(f"Remote file download: {source} -> {destination}")

        try:
            # Make HTTP call using query parameter for path
            response = yield {
                "method": "GET",
                "url": "/api/file/download",
                "params": {"path": source_remote},
                "headers": self._headers,
                "timeout": 60.0,
            }
            response.raise_for_status()

            # Ensure destination directory exists
            destination.parent.mkdir(parents=True, exist_ok=True)

            # Write the file content
            with open(destination, "wb") as f:
                f.write(response.content)

            return FileOperationResult(
                success=True,
                source_path=source_remote,
                destination_path=str(destination),
                file_size=len(response.content),
            )

        except Exception as e:
            _logger.error(f"Remote file download failed: {e}")
            return FileOperationResult(
                success=False,
                source_path=source_remote,
                destination_path=str(destination),
                error=str(e),
            )

    def _git_changes_generator(
        self,
        path: str | Path,
    ) -> Generator[dict[str, Any], httpx.Response, list[GitChange]]:
        """Get the git changes for the repository at the path given.

        Args:
            path: Path to the git repository

        Returns:
            list[GitChange]: List of changes

        Raises:
            Exception: If path is not a git repository or getting changes failed
        """
        remote_path = _join_remote_path(self.working_dir, path)
        response = yield {
            "method": "GET",
            "url": "/api/git/changes",
            "params": {"path": remote_path},
            "headers": self._headers,
            "timeout": 60.0,
        }
        response.raise_for_status()
        type_adapter = TypeAdapter(list[GitChange])
        changes = type_adapter.validate_python(response.json())
        return changes

    def _git_diff_generator(
        self,
        path: str | Path,
    ) -> Generator[dict[str, Any], httpx.Response, GitDiff]:
        """Get the git diff for the file at the path given.

        Args:
            path: Path to the file

        Returns:
            GitDiff: Git diff

        Raises:
            Exception: If path is not a git repository or getting diff failed
        """
        remote_path = _join_remote_path(self.working_dir, path)
        response = yield {
            "method": "GET",
            "url": "/api/git/diff",
            "params": {"path": remote_path},
            "headers": self._headers,
            "timeout": 60.0,
        }
        response.raise_for_status()
        diff = GitDiff.model_validate(response.json())
        return diff
