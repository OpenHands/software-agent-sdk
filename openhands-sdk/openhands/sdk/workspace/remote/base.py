from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from urllib.request import urlopen

import httpx
from pydantic import PrivateAttr

from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.base import BaseWorkspace
from openhands.sdk.workspace.models import CommandResult, FileOperationResult
from openhands.sdk.workspace.remote.remote_workspace_mixin import RemoteWorkspaceMixin


logger = get_logger(__name__)


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.secret import LookupSecret

# ── Agent-Server Settings API Routes ─────────────────────────────────────
# These route paths match the agent-server's settings_router endpoints.
# The router is mounted at /api/settings, so full paths are /api/settings/*.
# Keep in sync with openhands.agent_server.settings_router route constants.
_SETTINGS_API_BASE = "/api/settings"
_SECRETS_API_PATH = f"{_SETTINGS_API_BASE}/secrets"


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

    # -----------------------------------------------------------------
    # Settings methods - fetch configuration from agent-server
    # -----------------------------------------------------------------

    def get_llm(self, **llm_kwargs: Any) -> "LLM":
        """Fetch LLM settings from the agent-server and return an LLM instance.

        Calls ``GET /api/settings?expose_secrets=true`` to retrieve LLM
        configuration (model, api_key, base_url) from the agent-server's
        persisted settings.

        Args:
            **llm_kwargs: Additional keyword arguments passed to the LLM
                constructor, allowing overrides of any LLM parameter
                (e.g. ``model``, ``temperature``).

        Returns:
            An LLM instance configured with the fetched credentials.

        Raises:
            httpx.HTTPStatusError: If the API request fails.

        Example:
            >>> workspace = RemoteWorkspace(host="http://localhost:60000", ...)
            >>> llm = workspace.get_llm()
            >>> agent = Agent(llm=llm, tools=get_default_tools())
        """
        from openhands.sdk.llm.llm import LLM

        response = self.client.get(
            _SETTINGS_API_BASE, params={"expose_secrets": "true"}
        )
        response.raise_for_status()
        data = response.json()

        # Validate response is a dict (server error may return null/list/string)
        if not isinstance(data, dict):
            raise ValueError(
                f"Invalid settings response from agent-server: "
                f"expected dict, got {type(data).__name__}"
            )

        # Extract from agent_settings structure
        agent_settings = data.get("agent_settings", {})
        if not isinstance(agent_settings, dict):
            agent_settings = {}
        llm_settings = agent_settings.get("llm", {})
        if not isinstance(llm_settings, dict):
            llm_settings = {}

        # Build kwargs from fetched config (only include non-None values)
        kwargs: dict[str, Any] = {
            k: v
            for k, v in {
                "model": llm_settings.get("model"),
                "api_key": llm_settings.get("api_key"),
                "base_url": llm_settings.get("base_url"),
            }.items()
            if v is not None
        }

        # User-provided kwargs take precedence
        kwargs.update(llm_kwargs)

        # Warn if no API key is configured (common misconfiguration)
        if not kwargs.get("api_key"):
            logger.warning(
                "No LLM API key found in server settings or kwargs. "
                "LLM calls will likely fail. Configure via /api/settings."
            )

        return LLM(**kwargs)

    def get_secrets(self, names: list[str] | None = None) -> dict[str, "LookupSecret"]:
        """Build ``LookupSecret`` references for secrets from the agent-server.

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

        Example:
            >>> workspace = RemoteWorkspace(host="http://localhost:60000", ...)
            >>> secrets = workspace.get_secrets()
            >>> conversation.update_secrets(secrets)
        """
        from openhands.sdk.secret import LookupSecret

        response = self.client.get(_SECRETS_API_PATH)
        response.raise_for_status()
        data = response.json()

        # Validate response is a dict (server error may return null/list/string)
        if not isinstance(data, dict):
            return {}

        result: dict[str, LookupSecret] = {}
        secrets_list = data.get("secrets", [])
        if not isinstance(secrets_list, list):
            secrets_list = []

        for item in secrets_list:
            # Safely extract name, skip malformed items
            name = item.get("name") if isinstance(item, dict) else None
            if name is None:
                continue
            if names is not None and name not in names:
                continue
            # URL-encode secret name to handle special characters
            encoded_name = quote(name, safe="")
            result[name] = LookupSecret(
                url=f"{self.host}{_SECRETS_API_PATH}/{encoded_name}",
                headers=self._headers,
                description=item.get("description"),
            )

        return result

    def get_mcp_config(self) -> dict[str, Any]:
        """Fetch MCP configuration from the agent-server.

        Calls ``GET /api/settings`` to retrieve the MCP configuration
        and transforms it into the format expected by the SDK Agent and
        ``fastmcp.mcp_config.MCPConfig``.

        Returns:
            A dictionary with ``mcpServers`` key containing server configurations
            (compatible with ``MCPConfig.model_validate()``), or an empty dict
            if no MCP config is set.

        Raises:
            httpx.HTTPStatusError: If the API request fails.

        Example:
            >>> workspace = RemoteWorkspace(host="http://localhost:60000", ...)
            >>> mcp_config = workspace.get_mcp_config()
            >>> agent = Agent(llm=llm, mcp_config=mcp_config, tools=...)
        """
        response = self.client.get(_SETTINGS_API_BASE)
        response.raise_for_status()
        data = response.json()

        # Validate response is a dict (server error may return null/list/string)
        if not isinstance(data, dict):
            return {}

        # Extract from agent_settings structure
        agent_settings = data.get("agent_settings", {})
        if not isinstance(agent_settings, dict):
            return {}
        mcp_config_data = agent_settings.get("mcp_config")

        if not mcp_config_data or not isinstance(mcp_config_data, dict):
            return {}

        mcp_servers = self._transform_mcp_config_to_servers(mcp_config_data)

        if not mcp_servers:
            return {}

        return {"mcpServers": mcp_servers}

    def _transform_mcp_config_to_servers(
        self, mcp_config_data: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Transform MCP config data to mcpServers format.

        Args:
            mcp_config_data: Raw MCP config with sse_servers, shttp_servers,
                and stdio_servers lists.

        Returns:
            Dictionary of server configurations for MCPConfig.model_validate()
        """
        mcp_servers: dict[str, dict[str, Any]] = {}

        # Transform SSE servers → RemoteMCPServer format
        for i, sse_server in enumerate(mcp_config_data.get("sse_servers") or []):
            if not isinstance(sse_server, dict):
                continue  # Skip malformed entries
            url = sse_server.get("url")
            if not url or not isinstance(url, str):
                continue  # Skip entries without valid URL
            server_config: dict[str, Any] = {
                "url": url,
                "transport": "sse",
            }
            api_key = sse_server.get("api_key")
            if api_key and isinstance(api_key, str):
                server_config["headers"] = {"Authorization": f"Bearer {api_key}"}
            # SSE servers don't have names, use index
            mcp_servers[f"sse_{i}"] = server_config

        # Transform SHTTP servers → RemoteMCPServer format
        for i, shttp_server in enumerate(mcp_config_data.get("shttp_servers") or []):
            if not isinstance(shttp_server, dict):
                continue  # Skip malformed entries
            url = shttp_server.get("url")
            if not url or not isinstance(url, str):
                continue  # Skip entries without valid URL
            server_config = {
                "url": url,
                "transport": "streamable-http",
            }
            api_key = shttp_server.get("api_key")
            if api_key and isinstance(api_key, str):
                server_config["headers"] = {"Authorization": f"Bearer {api_key}"}
            # SHTTP servers don't have names, use index
            mcp_servers[f"shttp_{i}"] = server_config

        # Transform STDIO servers → StdioMCPServer format
        for stdio_server in mcp_config_data.get("stdio_servers") or []:
            if not isinstance(stdio_server, dict):
                continue  # Skip malformed entries
            command = stdio_server.get("command")
            server_name = stdio_server.get("name")
            if not command or not isinstance(command, str):
                continue  # Skip entries without valid command
            if not server_name or not isinstance(server_name, str):
                continue  # Skip entries without valid name
            args = stdio_server.get("args", [])
            server_config = {
                "command": command,
                "args": args if isinstance(args, list) else [],
            }
            env = stdio_server.get("env")
            if env and isinstance(env, dict):
                server_config["env"] = env
            # STDIO servers have an explicit name field - check for cross-type collision
            if server_name in mcp_servers:
                logger.warning(
                    f"MCP server name '{server_name}' collides with existing server "
                    f"(possibly from SSE/SHTTP config) - STDIO server will overwrite it"
                )
            mcp_servers[server_name] = server_config

        return mcp_servers
