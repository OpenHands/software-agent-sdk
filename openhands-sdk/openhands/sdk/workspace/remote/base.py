from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.request import urlopen

import httpx
import tenacity
from pydantic import PrivateAttr

from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.logger import get_logger
from openhands.sdk.settings import SecretsListResponse, SettingsResponse
from openhands.sdk.workspace.base import BaseWorkspace
from openhands.sdk.workspace.models import CommandResult, FileOperationResult
from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.secret import LookupSecret
    from openhands.sdk.settings import OpenHandsAgentSettings
    from openhands.sdk.settings.model import ACPAgentSettings, LLMAgentSettings


logger = get_logger(__name__)

# Number of retry attempts for transient API failures
_MAX_RETRIES = 3


def _is_retryable_error(error: BaseException) -> bool:
    """Return True for transient errors that are worth retrying."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    return isinstance(error, (httpx.ConnectError, httpx.TimeoutException))


class RemoteWorkspace(RemoteWorkspaceMixin, BaseWorkspace):
    """Remote workspace implementation that connects to an OpenHands agent server.

    RemoteWorkspace provides access to a sandboxed environment running on a remote
    OpenHands agent server. This is the recommended approach for production deployments
    as it provides better isolation and security.

    Example:
        >>> workspace = RemoteWorkspace(
        ...     host="https://agent-server.example.com",
        ...     working_dir="/workspace"
        ... )
        >>> with workspace:
        ...     result = workspace.execute_command("ls -la")
        ...     content = workspace.read_file("README.md")
    """

    _client: httpx.Client | None = PrivateAttr(default=None)

    def reset_client(self) -> None:
        """Reset the HTTP client to force re-initialization.

        This is useful when connection parameters (host, api_key) have changed
        and the client needs to be recreated with new values.
        """
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None

    @property
    def client(self) -> httpx.Client:
        client = self._client
        if client is None:
            # Configure reasonable timeouts for HTTP requests
            # - connect: 10 seconds to establish connection
            # - read: 600 seconds (10 minutes) to read response (for LLM operations)
            # - write: 10 seconds to send request
            # - pool: 10 seconds to get connection from pool
            timeout = httpx.Timeout(
                connect=10.0, read=self.read_timeout, write=10.0, pool=10.0
            )
            client = httpx.Client(
                base_url=self.host,
                timeout=timeout,
                headers=self._headers,
                limits=httpx.Limits(max_connections=self.max_connections),
            )
            self._client = client
        return client

    def _execute(self, generator: Generator[dict[str, Any], httpx.Response, Any]):
        try:
            kwargs = next(generator)
            while True:
                response = self.client.request(**kwargs)
                kwargs = generator.send(response)
        except StopIteration as e:
            return e.value

    def get_server_info(self) -> dict[str, Any]:
        """Return server metadata from the agent-server.

        This is useful for debugging version mismatches between the local SDK and
        the remote agent-server image.

        Returns:
            A JSON-serializable dict returned by GET /server_info.
        """
        response = self.client.get("/server_info")
        response.raise_for_status()
        data = response.json()
        assert isinstance(data, dict)
        return data

    def execute_command(
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
        result = self._execute(generator)
        return result

    def file_upload(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Upload a file to the remote system.

        Reads the local file and sends it to the remote system via HTTP API.

        Args:
            source_path: Path to the local source file
            destination_path: Path where the file should be uploaded on remote system

        Returns:
            FileOperationResult: Result with success status and metadata
        """
        generator = self._file_upload_generator(source_path, destination_path)
        result = self._execute(generator)
        return result

    def file_download(
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
        result = self._execute(generator)
        return result

    def git_changes(self, path: str | Path) -> list[GitChange]:
        """Get the git changes for the repository at the path given.

        Args:
            path: Path to the git repository

        Returns:
            list[GitChange]: List of changes

        Raises:
            Exception: If path is not a git repository or getting changes failed
        """
        generator = self._git_changes_generator(path)
        result = self._execute(generator)
        return result

    def git_diff(self, path: str | Path) -> GitDiff:
        """Get the git diff for the file at the path given.

        Args:
            path: Path to the file

        Returns:
            GitDiff: Git diff

        Raises:
            Exception: If path is not a git repository or getting diff failed
        """
        generator = self._git_diff_generator(path)
        result = self._execute(generator)
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

    @property
    def default_conversation_tags(self) -> dict[str, str] | None:
        """Default tags to apply to conversations created with this workspace.

        Subclasses (e.g., OpenHandsCloudWorkspace) can override this to provide
        context-specific tags like automation metadata.

        Returns:
            Dictionary of tag key-value pairs, or None if no default tags.
        """
        return None

    def register_conversation(self, conversation_id: str) -> None:
        """Register a conversation ID with this workspace.

        Called by RemoteConversation after creation to associate the conversation
        with the workspace. Subclasses can override to track conversation IDs
        for callbacks or other purposes.

        Args:
            conversation_id: The conversation ID to register
        """
        # Default implementation is a no-op
        pass

    @property
    def conversation_id(self) -> str | None:
        """Get the most recently registered conversation ID.

        Returns:
            The conversation ID if one has been registered, None otherwise.
        """
        return None

    # ── Settings Methods ──────────────────────────────────────────────────
    # These methods fetch configuration from the agent-server's persisted
    # settings endpoints. Subclasses like OpenHandsCloudWorkspace may override
    # to use alternative endpoints (e.g., Cloud API).

    def _fetch_agent_settings(
        self,
    ) -> "OpenHandsAgentSettings | LLMAgentSettings | ACPAgentSettings":
        """Call ``GET /api/settings`` and return a validated settings model.

        Uses ``X-Expose-Secrets: plaintext`` so secret fields (e.g. LLM
        api_key) are returned as plain strings.  The outer response is
        validated via :class:`SettingsResponse`, then the ``agent_settings``
        dict is validated through :func:`validate_agent_settings` which
        picks the correct discriminated-union variant
        (``OpenHandsAgentSettings`` or ``ACPAgentSettings``).
        """
        from openhands.sdk.settings import validate_agent_settings

        headers = dict(self._headers)
        headers["X-Expose-Secrets"] = "plaintext"

        response = self.client.get("/api/settings", headers=headers)
        response.raise_for_status()

        data = SettingsResponse.model_validate(response.json())
        return validate_agent_settings(data.agent_settings)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_llm(self, **llm_kwargs: Any) -> "LLM":
        """Fetch LLM settings from the agent-server's persisted settings.

        Calls ``GET /api/settings`` with ``X-Expose-Secrets: plaintext`` header
        to retrieve the full LLM configuration and returns a fully usable
        ``LLM`` instance.  All persisted LLM fields (model, api_key,
        base_url, temperature, max_output_tokens, …) are preserved.

        Args:
            **llm_kwargs: Additional keyword arguments that override
                persisted values (e.g., ``model``, ``temperature``).

        Returns:
            An LLM instance configured with the persisted settings.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the workspace host is not set.

        Example:
            >>> with DockerWorkspace(...) as workspace:
            ...     llm = workspace.get_llm()
            ...     agent = Agent(llm=llm, tools=get_default_tools())
        """
        from openhands.sdk.llm.llm import LLM

        if not self.host or self.host == "undefined":
            raise RuntimeError("Workspace host is not set")

        settings = self._fetch_agent_settings()

        if not llm_kwargs:
            return settings.llm

        # Dump persisted LLM config and merge overrides, then
        # reconstruct so Pydantic validators run on the merged values
        llm_data = settings.llm.model_dump(context={"expose_secrets": "plaintext"})
        llm_data.update(llm_kwargs)
        return LLM(**llm_data)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_secrets(self, names: list[str] | None = None) -> dict[str, "LookupSecret"]:
        """Build ``LookupSecret`` references for the agent-server's secrets.

        Fetches the list of available secret **names** from the agent-server
        (no raw values) and returns a dict of ``LookupSecret`` objects whose
        URLs point to per-secret endpoints. The agent-server resolves each
        ``LookupSecret`` lazily, so raw values **never** transit through
        the SDK client.

        The returned dict is compatible with ``conversation.update_secrets()``.

        Args:
            names: Optional list of secret names to include. If ``None``,
                all available secrets are returned.

        Returns:
            A dictionary mapping secret names to ``LookupSecret`` instances.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the workspace host is not set.

        Example:
            >>> with DockerWorkspace(...) as workspace:
            ...     secrets = workspace.get_secrets()
            ...     conversation.update_secrets(secrets)
            ...
            ...     # Or a subset
            ...     gh = workspace.get_secrets(names=["GITHUB_TOKEN"])
            ...     conversation.update_secrets(gh)
        """
        from openhands.sdk.secret import LookupSecret

        if not self.host or self.host == "undefined":
            raise RuntimeError("Workspace host is not set")

        response = self.client.get("/api/settings/secrets", headers=self._headers)
        response.raise_for_status()

        # Validate response using shared SDK model
        data = SecretsListResponse.model_validate(response.json())

        result: dict[str, LookupSecret] = {}
        for item in data.secrets:
            if names is not None and item.name not in names:
                continue
            result[item.name] = LookupSecret(
                url=f"{self.host}/api/settings/secrets/{item.name}",
                headers=dict(self._headers),
                description=item.description,
            )

        return result

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_mcp_config(self) -> dict[str, Any]:
        """Fetch MCP configuration from the agent-server's persisted settings.

        Calls ``GET /api/settings`` with ``X-Expose-Secrets: plaintext`` header
        to retrieve the MCP configuration and returns a dict compatible with
        ``MCPConfig.model_validate()`` and the ``Agent(mcp_config=...)`` kwarg.

        Returns:
            A dictionary with ``mcpServers`` key containing server configurations
            (compatible with ``MCPConfig.model_validate()``), or an empty dict
            if no MCP config is set.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the workspace host is not set.

        Example:
            >>> with DockerWorkspace(...) as workspace:
            ...     llm = workspace.get_llm()
            ...     mcp_config = workspace.get_mcp_config()
            ...     agent = Agent(llm=llm, mcp_config=mcp_config, tools=...)
            ...
            ...     # Or validate as MCPConfig:
            ...     from fastmcp.mcp_config import MCPConfig
            ...     config = MCPConfig.model_validate(mcp_config)
        """
        from openhands.sdk.settings import OpenHandsAgentSettings

        if not self.host or self.host == "undefined":
            raise RuntimeError("Workspace host is not set")

        settings = self._fetch_agent_settings()

        # mcp_config only exists on OpenHandsAgentSettings, not ACPAgentSettings
        if not isinstance(settings, OpenHandsAgentSettings):
            return {}

        if settings.mcp_config is None:
            return {}

        return settings.mcp_config.model_dump(exclude_none=True, exclude_defaults=True)
