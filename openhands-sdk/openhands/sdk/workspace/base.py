import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Annotated, Any

import httpx
from pydantic import BeforeValidator, Field, PrivateAttr

from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.models import DiscriminatedUnionMixin
from openhands.sdk.workspace.models import CommandResult, FileOperationResult


logger = get_logger(__name__)


def _convert_path_to_str(v: str | Path) -> str:
    """Convert Path objects to string for working_dir."""
    if isinstance(v, Path):
        return str(v)
    return v


class BaseWorkspace(DiscriminatedUnionMixin, ABC):
    """Abstract base class for workspace implementations.

    Workspaces provide a sandboxed environment where agents can execute commands,
    read/write files, and perform other operations. All workspace implementations
    support the context manager protocol for safe resource management.

    Example:
        ```python
        with workspace:
            result = workspace.execute_command("echo 'hello'")
            content = workspace.read_file("example.txt")
        ```
    """

    working_dir: Annotated[
        str,
        BeforeValidator(_convert_path_to_str),
        Field(
            description=(
                "The working directory for agent operations and tool execution. "
                "Accepts both string paths and Path objects. "
                "Path objects are automatically converted to strings."
            )
        ),
    ]

    _conversation_id: str | None = PrivateAttr(default=None)
    _accumulated_cost: float | None = PrivateAttr(default=None)
    _task_outcome: dict[str, Any] | None = PrivateAttr(default=None)

    def __enter__(self) -> "BaseWorkspace":
        """Enter the workspace context.

        Returns:
            Self for use in with statements
        """
        return self

    def register_cost(self, cost: float) -> None:
        """Register the accumulated LLM cost for this workspace's run.

        Called by the conversation on close, once the conversation has
        finished, so no caller opt-in is required. The cost is included in the
        completion callback sent to the automation service.

        Args:
            cost: Accumulated LLM cost in USD
        """
        self._accumulated_cost = cost
        logger.debug(f"Registered cost: {cost}")

    @property
    def accumulated_cost(self) -> float | None:
        """Get the most recently registered accumulated LLM cost.

        Returns:
            The cost in USD if one has been registered, None otherwise.
        """
        return self._accumulated_cost

    def register_task_outcome(self, task_outcome: dict[str, Any]) -> None:
        """Register the latest task outcome for automation callbacks.

        The callback payload includes this as ``agent_outcome`` only when the
        automation service advertises support via
        ``AUTOMATION_CALLBACK_SUPPORTS_AGENT_OUTCOME``. Older automation
        services reject unknown callback fields, so this is opt-in.
        """
        self._task_outcome = task_outcome
        logger.debug("Registered task outcome: %s", task_outcome.get("status"))

    @property
    def task_outcome(self) -> dict[str, Any] | None:
        """Get the most recently registered task outcome."""
        return self._task_outcome

    def _callback_supports_agent_outcome(self) -> bool:
        value = os.environ.get("AUTOMATION_CALLBACK_SUPPORTS_AGENT_OUTCOME", "")
        return value.lower() in {"1", "true", "yes", "on"}

    def _send_completion_callback(
        self, exc_type: type | None, exc_val: BaseException | None
    ) -> None:
        """POST completion status to the automation service (best-effort).

        Call this from ``__exit__`` before any cleanup. Does nothing when
        ``AUTOMATION_CALLBACK_URL`` env var is not set.

        Reads configuration from environment variables:
          - ``AUTOMATION_CALLBACK_URL`` — URL to POST completion status to
          - ``AUTOMATION_CALLBACK_API_KEY`` — Bearer token for callback auth (optional)
          - ``AUTOMATION_RUN_ID`` — Run ID to include in callback payload (optional)

        Includes ``conversation_id`` in the payload if one was registered, and
        ``cost`` if one was registered via ``register_cost()``.

        Args:
            exc_type: Exception type if an exception was raised, None otherwise
            exc_val: Exception value if an exception was raised, None otherwise
        """
        callback_url = os.environ.get("AUTOMATION_CALLBACK_URL")
        if not callback_url:
            return

        callback_api_key = os.environ.get("AUTOMATION_CALLBACK_API_KEY")
        run_id = os.environ.get("AUTOMATION_RUN_ID")

        status = "COMPLETED" if exc_type is None else "FAILED"
        payload: dict[str, Any] = {"status": status}
        if run_id:
            payload["run_id"] = run_id
        if exc_val is not None:
            payload["error"] = str(exc_val)

        # Include conversation_id if one was registered
        if self._conversation_id is not None:
            payload["conversation_id"] = self._conversation_id

        # Include accumulated LLM cost if one was registered
        if self._accumulated_cost is not None:
            payload["cost"] = self._accumulated_cost

        if self._task_outcome is not None and self._callback_supports_agent_outcome():
            payload["agent_outcome"] = self._task_outcome

        try:
            headers: dict[str, str] = {}
            if callback_api_key:
                headers["Authorization"] = f"Bearer {callback_api_key}"
            with httpx.Client(timeout=10.0) as cb_client:
                resp = cb_client.post(callback_url, json=payload, headers=headers)
                logger.info(f"Completion callback sent ({status}): {resp.status_code}")
        except Exception as e:
            logger.warning(f"Completion callback failed: {e}")

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the workspace context and cleanup resources.

        Default implementation performs no cleanup. Subclasses should override
        to add cleanup logic (e.g., stopping containers, closing connections).

        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred
        """
        pass

    @abstractmethod
    def execute_command(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 30.0,
    ) -> CommandResult:
        """Execute a bash command on the system.

        Args:
            command: The bash command to execute
            cwd: Working directory for the command (optional)
            timeout: Timeout in seconds (defaults to 30.0)

        Returns:
            CommandResult: Result containing stdout, stderr, exit_code, and other
                metadata

        Raises:
            Exception: If command execution fails
        """
        ...

    @abstractmethod
    def file_upload(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Upload a file to the system.

        Args:
            source_path: Path to the source file
            destination_path: Path where the file should be uploaded

        Returns:
            FileOperationResult: Result containing success status and metadata

        Raises:
            Exception: If file upload fails
        """
        ...

    @abstractmethod
    def file_download(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Download a file from the system.

        Args:
            source_path: Path to the source file on the system
            destination_path: Path where the file should be downloaded

        Returns:
            FileOperationResult: Result containing success status and metadata

        Raises:
            Exception: If file download fails
        """
        ...

    @abstractmethod
    def git_changes(self, path: str | Path) -> list[GitChange]:
        """Get the git changes for the repository at the path given.

        Args:
            path: Path to the git repository

        Returns:
            list[GitChange]: List of changes

        Raises:
            Exception: If path is not a git repository or getting changes failed
        """

    @abstractmethod
    def git_diff(self, path: str | Path) -> GitDiff:
        """Get the git diff for the file at the path given.

        Args:
            path: Path to the file

        Returns:
            GitDiff: Git diff

        Raises:
            Exception: If path is not a git repository or getting diff failed
        """

    def pause(self) -> None:
        """Pause the workspace to conserve resources.

        For local workspaces, this is a no-op.
        For container-based workspaces, this pauses the container.

        Raises:
            NotImplementedError: If the workspace type does not support pausing.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support pause()")

    def resume(self) -> None:
        """Resume a paused workspace.

        For local workspaces, this is a no-op.
        For container-based workspaces, this resumes the container.

        Raises:
            NotImplementedError: If the workspace type does not support resuming.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support resume()")
