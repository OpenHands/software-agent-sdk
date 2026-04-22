"""OpenHands Cloud workspace implementation using Cloud API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.request import urlopen

import httpx
import tenacity
from pydantic import Field, PrivateAttr

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.remote.base import RemoteWorkspace

from .repo import CloneResult, RepoMapping, RepoSource, clone_repos, get_repos_context


if TYPE_CHECKING:
    from openhands.sdk.context import AgentContext
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.secret import LookupSecret
    from openhands.sdk.skills import Skill


logger = get_logger(__name__)

# Standard exposed URL names from OpenHands Cloud
AGENT_SERVER = "AGENT_SERVER"

# Number of retry attempts for transient API failures
_MAX_RETRIES = 3

# Default port the agent-server listens on inside a Cloud Runtime
DEFAULT_AGENT_SERVER_PORT = 60000


def _is_retryable_error(error: BaseException) -> bool:
    """Return True for transient errors that are worth retrying."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    return isinstance(error, (httpx.ConnectError, httpx.TimeoutException))


class OpenHandsCloudWorkspace(RemoteWorkspace):
    """Remote workspace using OpenHands Cloud API.

    This workspace connects to OpenHands Cloud (app.all-hands.dev) to provision
    and manage sandboxed environments for agent execution.

    When ``local_agent_server_mode=True``, the workspace assumes it is already
    running inside an OpenHands Cloud Runtime sandbox.  Instead of creating or
    managing a sandbox via the Cloud API it connects directly to the local
    agent-server at ``http://localhost:<agent_server_port>``.

    Example:
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="your-api-key",
        )

        # With custom sandbox spec
        workspace = OpenHandsCloudWorkspace(
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key="your-api-key",
            sandbox_spec_id="ghcr.io/openhands/agent-server:main-python",
        )

        # Running inside an OpenHands Cloud Runtime (local agent-server mode)
        workspace = OpenHandsCloudWorkspace(
            local_agent_server_mode=True,
            cloud_api_url="https://app.all-hands.dev",
            cloud_api_key=os.environ["OPENHANDS_API_KEY"],
        )
    """

    # Parent fields
    working_dir: str = Field(
        default="/workspace/project",
        description="Working directory inside the sandbox",
    )
    host: str = Field(
        default="undefined",
        description=("The agent server URL. Set automatically after sandbox starts."),
    )

    # Local agent-server mode
    local_agent_server_mode: bool = Field(
        default=False,
        description=(
            "When True, assume the SDK is running inside an OpenHands Cloud "
            "Runtime and connect to the local agent-server instead of "
            "provisioning a sandbox via the Cloud API."
        ),
    )
    agent_server_port: int = Field(
        default=DEFAULT_AGENT_SERVER_PORT,
        description=(
            "Port of the local agent-server. "
            "Only used when local_agent_server_mode=True."
        ),
    )

    # Cloud API fields
    cloud_api_url: str = Field(
        description=(
            "Base URL of OpenHands Cloud API "
            "(e.g., https://app.all-hands.dev). "
            "Required in all modes — used for get_llms / get_secrets."
        ),
    )
    cloud_api_key: str = Field(
        description=(
            "API key for authenticating with OpenHands Cloud. "
            "Required in all modes — used for get_llms / get_secrets."
        ),
    )
    sandbox_spec_id: str | None = Field(
        default=None,
        description=("Optional sandbox specification ID (e.g., container image)"),
    )

    # Lifecycle options
    init_timeout: float = Field(
        default=300.0,
        description="Sandbox initialization timeout in seconds",
    )
    api_timeout: float = Field(
        default=60.0, description="API request timeout in seconds"
    )
    keep_alive: bool = Field(
        default=False,
        description=("If True, keep sandbox alive on cleanup instead of deleting"),
    )

    # Sandbox ID - can be provided to resume an existing sandbox
    sandbox_id: str | None = Field(
        default=None,
        description=(
            "Optional sandbox ID to resume. If provided, the workspace will "
            "attempt to resume the existing sandbox instead of creating a "
            "new one."
        ),
    )

    # Private state
    _sandbox_id: str | None = PrivateAttr(default=None)
    _session_api_key: str | None = PrivateAttr(default=None)
    _exposed_urls: list[dict[str, Any]] | None = PrivateAttr(default=None)
    _automation_callback_url: str | None = PrivateAttr(default=None)
    _automation_run_id: str | None = PrivateAttr(default=None)

    @property
    def default_conversation_tags(self) -> dict[str, str]:
        """Build default tags from automation env vars for conversation creation.

        When running inside an OpenHands Cloud Runtime (local_agent_server_mode=True),
        this property extracts automation metadata from environment variables and
        returns them as tags that can be attached to conversations.

        The tags include (keys are lowercase alphanumeric per API requirements):
          - automationtrigger: The trigger type (e.g., 'cron', 'webhook', 'manual')
          - automationid: The automation's unique identifier
          - automationname: Human-readable automation name
          - automationrunid: The specific run identifier

        Note: Skills/plugins are NOT included here - they are passed when creating
        the RemoteConversation and merged at that level.

        These tags are automatically merged into conversations created via this
        workspace, allowing the Cloud platform to track automation context.
        """
        tags: dict[str, str] = {}

        # Parse AUTOMATION_EVENT_PAYLOAD (injected by dispatcher)
        payload_str = os.environ.get("AUTOMATION_EVENT_PAYLOAD")
        if payload_str:
            try:
                payload = json.loads(payload_str)
                if isinstance(payload, dict):
                    if payload.get("trigger"):
                        tags["automationtrigger"] = str(payload["trigger"])
                    if payload.get("automation_id"):
                        tags["automationid"] = str(payload["automation_id"])
                    if payload.get("automation_name"):
                        tags["automationname"] = str(payload["automation_name"])
            except (json.JSONDecodeError, TypeError):
                logger.error("Failed to parse AUTOMATION_EVENT_PAYLOAD")

        # Add run_id from env var or private attr
        run_id = os.environ.get("AUTOMATION_RUN_ID") or self._automation_run_id
        if run_id:
            tags["automationrunid"] = run_id

        return tags

    @property
    def client(self) -> httpx.Client:
        """Override client property to use api_timeout for HTTP requests."""
        client = self._client
        if client is None:
            timeout = httpx.Timeout(
                connect=10.0,
                read=self.api_timeout,
                write=10.0,
                pool=10.0,
            )
            client = httpx.Client(
                base_url=self.host, timeout=timeout, headers=self._headers
            )
            self._client = client
        return client

    @property
    def _api_headers(self) -> dict[str, str]:
        """Headers for Cloud API requests.

        Uses Bearer token authentication as per OpenHands Cloud API.
        """
        return {"Authorization": f"Bearer {self.cloud_api_key}"}

    def model_post_init(self, context: Any) -> None:
        """Set up the sandbox and initialize the workspace."""
        self.cloud_api_url = self.cloud_api_url.rstrip("/")

        if self.local_agent_server_mode:
            self._init_local_agent_server_mode()
        else:
            try:
                self._start_sandbox()
                super().model_post_init(context)
            except Exception:
                self.cleanup()
                raise

    def _init_local_agent_server_mode(self) -> None:
        """Initialize in local agent-server mode — connect to local agent-server.

        Reads sandbox identity and automation callback settings from
        environment variables so that ``get_llm()`` and ``get_secrets()``
        can call the Cloud API's sandbox-scoped settings endpoints.

        Expected env vars (injected by the automation dispatcher):
          ``SANDBOX_ID``                — this sandbox's Cloud API identifier
          ``SESSION_API_KEY``           — session key for sandbox settings auth
          ``AUTOMATION_CALLBACK_URL``   — completion callback endpoint (optional)
          ``AUTOMATION_RUN_ID``         — run ID for callback payload (optional)

        Falls back to ``OH_SESSION_API_KEYS_0`` (set by the runtime)
        if ``SESSION_API_KEY`` is not present.
        """
        port = os.environ.get("AGENT_SERVER_PORT", str(self.agent_server_port))
        self.host = f"http://localhost:{port}"
        logger.info(
            f"Local agent-server mode: connecting to agent-server at {self.host}"
        )

        # Discover sandbox identity from env vars
        self._sandbox_id = self.sandbox_id or os.environ.get("SANDBOX_ID")
        self._session_api_key = os.environ.get(
            "SESSION_API_KEY", os.environ.get("OH_SESSION_API_KEYS_0")
        )

        # Automation callback settings from env vars
        self._automation_callback_url = os.environ.get("AUTOMATION_CALLBACK_URL")
        self._automation_run_id = os.environ.get("AUTOMATION_RUN_ID")

        if not self._sandbox_id:
            logger.warning(
                "SANDBOX_ID env var not set — get_llm()/get_secrets() "
                "will not work. Set SANDBOX_ID or pass sandbox_id= to "
                "the constructor."
            )
        if not self._session_api_key:
            logger.warning(
                "SESSION_API_KEY env var not set — sandbox settings "
                "API calls will fail."
            )

        # Propagate to RemoteWorkspaceMixin.api_key so the shared HTTP
        # client (used by RemoteConversation) includes X-Session-API-Key.
        self.api_key = self._session_api_key

        self.reset_client()
        # Trigger parent mixin init (strips trailing slash, etc.)
        super().model_post_init(None)

    def _start_sandbox(self) -> None:
        """Start a new sandbox or resume an existing one via Cloud API.

        If sandbox_id is provided, attempts to resume the existing sandbox.
        Otherwise, creates a new sandbox.
        """
        if self.sandbox_id:
            self._resume_existing_sandbox()
        else:
            self._create_new_sandbox()

        # Wait for sandbox to become RUNNING
        self._wait_until_sandbox_ready()

        # Extract agent server URL from exposed_urls
        agent_server_url = self._get_agent_server_url()
        if not agent_server_url:
            raise ValueError(
                f"Agent server URL not found in sandbox {self._sandbox_id}"
            )

        logger.info(f"Sandbox ready at {agent_server_url}")

        # Set host and api_key for RemoteWorkspace operations
        self.host = agent_server_url.rstrip("/")
        self.api_key = self._session_api_key

        # Reset HTTP client with new host and API key
        self.reset_client()

        # Verify client is properly initialized
        assert self.client is not None
        assert self.client.base_url == self.host

    def _create_new_sandbox(self) -> None:
        """Create a new sandbox via Cloud API."""
        logger.info("Starting sandbox via OpenHands Cloud API...")

        # Build request params
        params: dict[str, str] = {}
        if self.sandbox_spec_id:
            params["sandbox_spec_id"] = self.sandbox_spec_id

        # POST /api/v1/sandboxes to start a new sandbox
        resp = self._send_api_request(
            "POST",
            f"{self.cloud_api_url}/api/v1/sandboxes",
            params=params if params else None,
            timeout=self.init_timeout,
        )
        data = resp.json()

        self._sandbox_id = data["id"]
        self._session_api_key = data.get("session_api_key")
        logger.info(
            f"Sandbox {self._sandbox_id} created, waiting for it to be ready..."
        )

    def _resume_existing_sandbox(self) -> None:
        """Resume an existing sandbox by ID.

        Sets the internal sandbox ID and calls the resume endpoint directly.
        """
        assert self.sandbox_id is not None
        self._sandbox_id = self.sandbox_id
        logger.info(f"Resuming existing sandbox {self._sandbox_id}...")
        self._resume_sandbox()

    @tenacity.retry(
        stop=tenacity.stop_after_delay(300),
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        retry=tenacity.retry_if_exception_type(RuntimeError),
        reraise=True,
    )
    def _wait_until_sandbox_ready(self) -> None:
        """Wait until the sandbox becomes RUNNING and responsive."""
        logger.debug("Checking sandbox status...")

        # GET /api/v1/sandboxes?id=<sandbox_id>
        resp = self._send_api_request(
            "GET",
            f"{self.cloud_api_url}/api/v1/sandboxes",
            params={"id": self._sandbox_id},
        )
        sandboxes = resp.json()

        if not sandboxes or sandboxes[0] is None:
            raise RuntimeError(f"Sandbox {self._sandbox_id} not found")

        sandbox = sandboxes[0]
        status = sandbox.get("status")
        logger.info(f"Sandbox status: {status}")

        if status == "RUNNING":
            # Update session_api_key and exposed_urls from response
            self._session_api_key = sandbox.get("session_api_key")
            self._exposed_urls = sandbox.get("exposed_urls") or []

            # Verify agent server is accessible
            agent_server_url = self._get_agent_server_url()
            if agent_server_url:
                self._check_agent_server_health(agent_server_url)
            return

        elif status == "STARTING":
            raise RuntimeError("Sandbox still starting")

        elif status in ("ERROR", "MISSING"):
            raise ValueError(f"Sandbox failed with status: {status}")

        elif status == "PAUSED":
            # Try to resume the sandbox
            logger.info("Sandbox is paused, attempting to resume...")
            self._resume_sandbox()
            raise RuntimeError("Sandbox resuming, waiting for RUNNING status")

        else:
            logger.warning(f"Unknown sandbox status: {status}")
            raise RuntimeError(f"Unknown sandbox status: {status}")

    def _check_agent_server_health(self, agent_server_url: str) -> None:
        """Check if the agent server is healthy."""
        health_url = f"{agent_server_url.rstrip('/')}/health"
        logger.debug(f"Checking agent server health at: {health_url}")
        try:
            with urlopen(health_url, timeout=5.0) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= status < 300:
                    logger.debug("Agent server is healthy")
                    return
                raise RuntimeError(f"Health check failed with status: {status}")
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            raise RuntimeError(f"Agent server health check failed: {e}")

    def _resume_sandbox(self) -> None:
        """Resume a paused sandbox."""
        if not self._sandbox_id:
            return

        logger.info(f"Resuming sandbox {self._sandbox_id}...")
        self._send_api_request(
            "POST",
            f"{self.cloud_api_url}/api/v1/sandboxes/{self._sandbox_id}/resume",
            timeout=self.init_timeout,
        )

    def _get_agent_server_url(self) -> str | None:
        """Extract agent server URL from exposed_urls."""
        if not self._exposed_urls:
            return None

        for url_info in self._exposed_urls:
            if url_info.get("name") == AGENT_SERVER:
                return url_info.get("url")

        return None

    def pause(self) -> None:
        """Pause the sandbox to conserve resources.

        Note: OpenHands Cloud does not currently support pausing sandboxes.
        This method raises NotImplementedError until the API is available.

        Raises:
            NotImplementedError: Cloud API pause endpoint is not yet available.
        """
        raise NotImplementedError(
            "OpenHandsCloudWorkspace.pause() is not yet supported - "
            "Cloud API pause endpoint not available"
        )

    def resume(self) -> None:
        """Resume a paused sandbox.

        Calls the /resume endpoint on the Cloud API to resume the sandbox.

        Raises:
            RuntimeError: If the sandbox is not running.
        """
        if not self._sandbox_id:
            raise RuntimeError("Cannot resume: sandbox is not running")

        logger.info(f"Resuming sandbox {self._sandbox_id}")
        self._resume_sandbox()
        self._wait_until_sandbox_ready()
        logger.info(f"Sandbox resumed: {self._sandbox_id}")

    def _send_api_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send an API request to the Cloud API with error handling."""
        logger.debug(f"Sending {method} request to {url}")

        # Ensure headers include API key
        headers = kwargs.pop("headers", {})
        headers.update(self._api_headers)

        # Use a separate client for API requests (not the agent server client)
        timeout = kwargs.pop("timeout", self.api_timeout)
        with httpx.Client(timeout=timeout) as api_client:
            response = api_client.request(method, url, headers=headers, **kwargs)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            try:
                error_detail = response.json()
                logger.error(f"Cloud API request failed: {error_detail}")
            except Exception:
                logger.error(f"Cloud API request failed: {response.text}")
            raise

        return response

    def cleanup(self) -> None:
        """Clean up the sandbox by deleting it.

        In local agent-server mode the sandbox is managed externally, so only
        the HTTP client is closed.
        """
        # Guard against __del__ on partially-constructed instances
        # (e.g. when validation fails before all fields are initialised).
        try:
            local_mode = self.local_agent_server_mode
        except AttributeError:
            return

        if local_mode:
            try:
                if self._client:
                    self._client.close()
            except Exception:
                pass
            return

        if not self._sandbox_id:
            return

        try:
            if self.keep_alive:
                logger.info(f"Keeping sandbox {self._sandbox_id} alive")
                return

            logger.info(f"Deleting sandbox {self._sandbox_id}...")
            self._send_api_request(
                "DELETE",
                f"{self.cloud_api_url}/api/v1/sandboxes/{self._sandbox_id}",
                params={"sandbox_id": self._sandbox_id},
                timeout=30.0,
            )
            logger.info(f"Sandbox {self._sandbox_id} deleted")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")
        finally:
            self._sandbox_id = None
            self._session_api_key = None
            self._exposed_urls = None
            try:
                if self._client:
                    self._client.close()
            except Exception:
                pass

    # -----------------------------------------------------------------
    # Settings helpers
    # -----------------------------------------------------------------

    @property
    def _settings_base_url(self) -> str:
        """Base URL for sandbox-scoped settings endpoints."""
        return f"{self.cloud_api_url}/api/v1/sandboxes/{self._sandbox_id}/settings"

    @property
    def _session_headers(self) -> dict[str, str]:
        """Headers for settings requests (SESSION_API_KEY auth)."""
        return {"X-Session-API-Key": self._session_api_key or ""}

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_llm(self, **llm_kwargs: Any) -> LLM:
        """Fetch LLM settings from the user's SaaS account and return an LLM.

        Calls ``GET /api/v1/users/me?expose_secrets=true`` to retrieve the
        user's LLM configuration (model, api_key, base_url) and returns a
        fully usable ``LLM`` instance.  Retries up to 3 times on transient
        errors (network issues, server 5xx).

        Args:
            **llm_kwargs: Additional keyword arguments passed to the LLM
                constructor, allowing overrides of any LLM parameter
                (e.g. ``model``, ``temperature``).

        Returns:
            An LLM instance configured with the user's SaaS credentials.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the sandbox is not running.

        Example:
            >>> with OpenHandsCloudWorkspace(...) as workspace:
            ...     llm = workspace.get_llm()
            ...     agent = Agent(llm=llm, tools=get_default_tools())
        """
        from openhands.sdk.llm.llm import LLM

        if not self._sandbox_id:
            raise RuntimeError("Sandbox is not running")

        resp = self._send_api_request(
            "GET",
            f"{self.cloud_api_url}/api/v1/users/me",
            params={"expose_secrets": "true"},
            headers={"X-Session-API-Key": self._session_api_key or ""},
        )
        data = resp.json()

        kwargs: dict[str, Any] = {}
        if data.get("llm_model"):
            kwargs["model"] = data["llm_model"]
        if data.get("llm_api_key"):
            kwargs["api_key"] = data["llm_api_key"]
        if data.get("llm_base_url"):
            kwargs["base_url"] = data["llm_base_url"]

        # User-provided kwargs take precedence
        kwargs.update(llm_kwargs)

        return LLM(**kwargs)

    def get_secrets(self, names: list[str] | None = None) -> dict[str, LookupSecret]:
        """Build ``LookupSecret`` references for the user's SaaS secrets.

        Fetches the list of available secret **names** from the SaaS (no raw
        values) and returns a dict of ``LookupSecret`` objects whose URLs
        point to per-secret endpoints.  The agent-server resolves each
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
            RuntimeError: If the sandbox is not running.

        Example:
            >>> with OpenHandsCloudWorkspace(...) as workspace:
            ...     secrets = workspace.get_secrets()
            ...     conversation.update_secrets(secrets)
            ...
            ...     # Or a subset
            ...     gh = workspace.get_secrets(names=["GITHUB_TOKEN"])
            ...     conversation.update_secrets(gh)
        """
        from openhands.sdk.secret import LookupSecret

        if not self._sandbox_id:
            raise RuntimeError("Sandbox is not running")

        resp = self._send_settings_request("GET", f"{self._settings_base_url}/secrets")
        data = resp.json()

        result: dict[str, LookupSecret] = {}
        for item in data.get("secrets", []):
            name = item["name"]
            if names is not None and name not in names:
                continue
            result[name] = LookupSecret(
                url=f"{self._settings_base_url}/secrets/{name}",
                headers={"X-Session-API-Key": self._session_api_key or ""},
                description=item.get("description"),
            )

        return result

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def get_mcp_config(self) -> dict[str, Any]:
        """Fetch MCP configuration from the user's SaaS account.

        Calls ``GET /api/v1/users/me`` to retrieve the user's MCP configuration
        and transforms it into the format expected by the SDK Agent and
        ``fastmcp.mcp_config.MCPConfig``.

        Returns:
            A dictionary with ``mcpServers`` key containing server configurations
            (compatible with ``MCPConfig.model_validate()``), or an empty dict
            if no MCP config is set.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
            RuntimeError: If the sandbox is not running.

        Example:
            >>> with OpenHandsCloudWorkspace(...) as workspace:
            ...     llm = workspace.get_llm()
            ...     mcp_config = workspace.get_mcp_config()
            ...     agent = Agent(llm=llm, mcp_config=mcp_config, tools=...)
            ...
            ...     # Or validate as MCPConfig:
            ...     from fastmcp.mcp_config import MCPConfig
            ...     config = MCPConfig.model_validate(mcp_config)
        """
        if not self._sandbox_id:
            raise RuntimeError("Sandbox is not running")

        resp = self._send_api_request(
            "GET",
            f"{self.cloud_api_url}/api/v1/users/me",
            headers={"X-Session-API-Key": self._session_api_key or ""},
        )
        data = resp.json()

        mcp_config_data = data.get("mcp_config")
        if not mcp_config_data:
            return {}

        mcp_servers: dict[str, dict[str, Any]] = {}

        # Transform SSE servers → RemoteMCPServer format
        for i, sse_server in enumerate(mcp_config_data.get("sse_servers") or []):
            server_config: dict[str, Any] = {
                "url": sse_server["url"],
                "transport": "sse",
            }
            if sse_server.get("api_key"):
                server_config["headers"] = {
                    "Authorization": f"Bearer {sse_server['api_key']}"
                }
            server_name = f"sse_{i}"
            mcp_servers[server_name] = server_config

        # Transform SHTTP servers → RemoteMCPServer format
        for i, shttp_server in enumerate(mcp_config_data.get("shttp_servers") or []):
            server_config = {
                "url": shttp_server["url"],
                "transport": "streamable-http",
            }
            if shttp_server.get("api_key"):
                server_config["headers"] = {
                    "Authorization": f"Bearer {shttp_server['api_key']}"
                }
            if shttp_server.get("timeout"):
                server_config["timeout"] = shttp_server["timeout"]
            server_name = f"shttp_{i}"
            mcp_servers[server_name] = server_config

        # Transform STDIO servers → StdioMCPServer format
        for stdio_server in mcp_config_data.get("stdio_servers") or []:
            server_config = {
                "command": stdio_server["command"],
                "args": stdio_server.get("args", []),
            }
            if stdio_server.get("env"):
                server_config["env"] = stdio_server["env"]
            # STDIO servers have an explicit name field
            mcp_servers[stdio_server["name"]] = server_config

        if not mcp_servers:
            return {}

        return {"mcpServers": mcp_servers}

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(_MAX_RETRIES),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=5),
        retry=tenacity.retry_if_exception(_is_retryable_error),
        reraise=True,
    )
    def _send_settings_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """Send a request to sandbox settings endpoints (SESSION_API_KEY auth).

        Retries up to 3 times on transient errors (network issues, server 5xx).
        """
        headers = kwargs.pop("headers", {})
        headers.update(self._session_headers)

        timeout = kwargs.pop("timeout", self.api_timeout)
        with httpx.Client(timeout=timeout) as api_client:
            response = api_client.request(method, url, headers=headers, **kwargs)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            try:
                error_detail = response.json()
                logger.error(f"Settings request failed: {error_detail}")
            except Exception:
                logger.error(f"Settings request failed: {response.text}")
            raise

        return response

    def __del__(self) -> None:
        self.cleanup()

    def __enter__(self) -> OpenHandsCloudWorkspace:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._send_completion_callback(exc_type, exc_val)
        self.cleanup()

    def _send_completion_callback(
        self, exc_type: type | None, exc_val: BaseException | None
    ) -> None:
        """POST completion status to the automation service (best-effort).

        Called by ``__exit__`` before ``cleanup()``.  Does nothing when
        ``AUTOMATION_CALLBACK_URL`` env var was not set.
        """
        try:
            callback_url = self._automation_callback_url
        except AttributeError:
            return

        if not callback_url:
            return

        status = "COMPLETED" if exc_type is None else "FAILED"
        payload: dict[str, Any] = {"status": status}
        if self._automation_run_id:
            payload["run_id"] = self._automation_run_id
        if exc_val is not None:
            payload["error"] = str(exc_val)

        try:
            headers = {"Authorization": f"Bearer {self.cloud_api_key}"}
            with httpx.Client(timeout=10.0) as cb_client:
                resp = cb_client.post(callback_url, json=payload, headers=headers)
                logger.info(f"Completion callback sent ({status}): {resp.status_code}")
        except Exception as e:
            logger.warning(f"Completion callback failed: {e}")

    # --- Repository Cloning Methods ---

    def _get_secret_value(self, name: str) -> str | None:
        """Fetch a secret value directly from the sandbox settings API.

        Unlike get_secrets() which returns LookupSecret references, this method
        fetches the actual secret value for use in operations like git cloning.

        Args:
            name: Name of the secret to fetch (e.g., "github_token", "gitlab_token")

        Returns:
            The secret value as a string, or None if not found or an error occurred.
        """
        if not self._sandbox_id or not self._session_api_key:
            return None

        # Validate secret name to prevent path traversal
        if not name or "/" in name or ".." in name:
            logger.warning(f"Invalid secret name: {name}")
            return None

        try:
            resp = self._send_settings_request(
                "GET", f"{self._settings_base_url}/secrets/{name}"
            )
            return resp.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Secret '{name}' not found")
            else:
                logger.warning(f"Failed to fetch secret '{name}': {e}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching secret '{name}': {e}")
            return None

    def clone_repos(
        self,
        repos: list[RepoSource | dict[str, Any] | str],
        target_dir: str | Path | None = None,
    ) -> CloneResult:
        """Clone repositories to the workspace directory.

        Clones specified repositories to meaningful directory names (e.g.,
        'openhands-cli' instead of 'repo_0'). Automatically fetches GitHub,
        GitLab, and Bitbucket tokens from the user's secrets for authentication.

        Args:
            repos: List of repositories to clone. Can be:
                - List of RepoSource objects
                - List of dicts with 'url', optional 'ref', and 'provider' keys
                - List of full URL strings (e.g., "https://github.com/owner/repo")
                Note: Short URLs (owner/repo) require explicit 'provider' field.
            target_dir: Directory to clone into. Defaults to self.working_dir.

        Returns:
            CloneResult containing:
                - success_count: Number of successfully cloned repos
                - failed_repos: List of repo URLs that failed to clone
                - repo_mappings: Dict mapping URLs to RepoMapping objects

        Example:
            >>> with OpenHandsCloudWorkspace(...) as workspace:
            ...     # Clone with full URLs (provider auto-detected)
            ...     result = workspace.clone_repos([
            ...         "https://github.com/owner/repo1",
            ...         {"url": "https://gitlab.com/owner/repo2", "ref": "main"},
            ...     ])
            ...
            ...     # Clone with short URLs (provider required)
            ...     result = workspace.clone_repos([
            ...         {"url": "owner/repo1", "provider": "github"},
            ...         {"url": "owner/repo2", "provider": "gitlab", "ref": "v1.0"},
            ...     ])
            ...
            ...     # Access cloned repo paths
            ...     for url, mapping in result.repo_mappings.items():
            ...         print(f"{url} -> {mapping.local_path}")
        """
        # Normalize repos to RepoSource objects using model_validate
        # This ensures consistent validation for all input formats
        normalized_repos: list[RepoSource] = []
        for repo in repos:
            if isinstance(repo, RepoSource):
                normalized_repos.append(repo)
            else:
                # model_validate handles both dicts and strings (via model_validator)
                normalized_repos.append(RepoSource.model_validate(repo))

        # Determine target directory
        if target_dir is None:
            target_path = Path(self.working_dir)
        elif isinstance(target_dir, str):
            target_path = Path(target_dir)
        else:
            target_path = target_dir

        # Clone repositories using _get_secret_value as token fetcher
        # This fetches tokens lazily based on each repo's provider
        return clone_repos(
            repos=normalized_repos,
            target_dir=target_path,
            token_fetcher=self._get_secret_value,
        )

    def get_repos_context(self, repo_mappings: dict[str, RepoMapping]) -> str:
        """Generate context string describing cloned repositories for the agent.

        This method produces a markdown-formatted string that can be prepended
        to agent prompts to inform the agent about available repositories.

        Args:
            repo_mappings: Dict mapping URLs to RepoMapping objects, typically
                obtained from CloneResult.repo_mappings after calling clone_repos().

        Returns:
            Markdown-formatted context string, or empty string if no repos.

        Example:
            >>> with OpenHandsCloudWorkspace(...) as workspace:
            ...     result = workspace.clone_repos(["owner/repo"])
            ...     context = workspace.get_repos_context(result.repo_mappings)
            ...     prompt = f"{context}\\n\\n{user_prompt}"
        """
        return get_repos_context(repo_mappings)

    # --- Skill Loading Methods ---

    def _call_skills_api(
        self,
        project_dir: str,
        load_public: bool = False,
        load_user: bool = False,
        load_project: bool = False,
        load_org: bool = False,
        timeout: float = 60.0,
    ) -> list[dict[str, Any]]:
        """Call the agent-server /api/skills endpoint.

        Returns list of skill dicts, or empty list on error.
        """
        payload = {
            "load_public": load_public,
            "load_user": load_user,
            "load_project": load_project,
            "load_org": load_org,
            "project_dir": project_dir,
            "org_config": None,
            "sandbox_config": None,
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._session_api_key:
            headers["X-Session-API-Key"] = self._session_api_key

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{self.host}/api/skills",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                logger.debug(f"Agent-server sources: {data.get('sources', {})}")
                return data.get("skills", [])
        except httpx.HTTPStatusError as e:
            logger.error(f"Agent-server HTTP error {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"Failed to connect to agent-server: {e}")
            return []

    def _add_skills_to_dict(
        self,
        skills_by_name: dict[str, dict[str, Any]],
        skill_list: list[dict[str, Any]],
    ) -> None:
        """Add skills to dict, keyed by name (later values override)."""
        for skill_data in skill_list:
            name = skill_data.get("name", "unknown")
            skills_by_name[name] = skill_data

    def _load_skills_multi_dir(
        self,
        project_dirs: list[str],
        load_public: bool,
        load_user: bool,
        load_project: bool,
        load_org: bool,
        timeout: float,
    ) -> dict[str, dict[str, Any]]:
        """Load skills when multiple project directories are specified."""
        skills_by_name: dict[str, dict[str, Any]] = {}

        # Load global skills (public/user/org) once
        logger.debug("Loading public/user/org skills...")
        global_skills = self._call_skills_api(
            project_dir=self.working_dir,
            load_public=load_public,
            load_user=load_user,
            load_project=False,
            load_org=load_org,
            timeout=timeout,
        )
        self._add_skills_to_dict(skills_by_name, global_skills)

        # Load project skills from each directory
        if not load_project:
            return skills_by_name

        for dir_path in project_dirs:
            logger.debug(f"Loading project skills from {dir_path}...")
            proj_skills = self._call_skills_api(
                project_dir=dir_path,
                load_project=True,
                timeout=timeout,
            )
            self._add_skills_to_dict(skills_by_name, proj_skills)

        return skills_by_name

    def _load_skills_single_dir(
        self,
        load_public: bool,
        load_user: bool,
        load_project: bool,
        load_org: bool,
        timeout: float,
    ) -> dict[str, dict[str, Any]]:
        """Load all skills from the working directory."""
        logger.debug("Loading all skills from working_dir...")
        all_skills = self._call_skills_api(
            project_dir=self.working_dir,
            load_public=load_public,
            load_user=load_user,
            load_project=load_project,
            load_org=load_org,
            timeout=timeout,
        )

        skills_by_name: dict[str, dict[str, Any]] = {}
        self._add_skills_to_dict(skills_by_name, all_skills)
        return skills_by_name

    def _convert_skills_dict_to_list(
        self, skills_by_name: dict[str, dict[str, Any]]
    ) -> list[Skill]:
        """Convert skill dicts to SDK Skill objects."""
        loaded_skills: list[Skill] = []
        for skill_data in skills_by_name.values():
            try:
                skill = self._convert_skill_data_to_skill(skill_data)
                loaded_skills.append(skill)
            except Exception as e:
                skill_name = skill_data.get("name", "unknown")
                logger.warning(f"Failed to convert skill {skill_name}: {e}")
        return loaded_skills

    def load_skills_from_agent_server(
        self,
        project_dirs: list[str | Path] | None = None,
        load_public: bool = True,
        load_user: bool = True,
        load_project: bool = True,
        load_org: bool = True,
        timeout: float = 60.0,
    ) -> tuple[list[Skill], AgentContext]:
        """Load skills via the local agent-server's /api/skills endpoint.

        This method calls the agent-server running inside the sandbox to load
        skills from all configured sources, mirroring how V1 conversations
        load skills in OpenHands.

        When project_dirs is provided (e.g., directories of cloned repos),
        project skills are loaded from EACH directory separately and merged.
        Skills are deduplicated by name, with later directories taking
        precedence over earlier ones.

        Args:
            project_dirs: List of directories to load project skills from.
                If None, uses self.working_dir only.
            load_public: Load public skills from OpenHands/extensions repo.
            load_user: Load user skills from ~/.openhands/skills/.
            load_project: Load project skills from workspace directories.
            load_org: Load organization-level skills.
            timeout: Request timeout in seconds.

        Returns:
            Tuple of (list of Skill objects, AgentContext).
            The AgentContext is pre-configured with loaded skills and
            load_public_skills=False to avoid duplicates (or True if no skills loaded).

        Example:
            >>> with OpenHandsCloudWorkspace(...) as workspace:
            ...     # Load all skills using working_dir
            ...     skills, context = workspace.load_skills_from_agent_server()
            ...
            ...     # Load skills from cloned repos
            ...     result = workspace.clone_repos(["owner/repo1", "owner/repo2"])
            ...     repo_dirs = [m.local_path for m in result.repo_mappings.values()]
            ...     skills, context = workspace.load_skills_from_agent_server(
            ...         project_dirs=repo_dirs
            ...     )
            ...
            ...     # Use with agent
            ...     agent = agent.model_copy(update={"agent_context": context})
        """
        from openhands.sdk.context import AgentContext

        logger.info("Loading skills via agent-server...")
        logger.debug(f"Agent-server URL: {self.host}")

        # Load skills based on whether multiple project dirs are specified
        if project_dirs:
            dirs = [str(d) if isinstance(d, Path) else d for d in project_dirs]
            skills_by_name = self._load_skills_multi_dir(
                dirs, load_public, load_user, load_project, load_org, timeout
            )
        else:
            skills_by_name = self._load_skills_single_dir(
                load_public, load_user, load_project, load_org, timeout
            )

        # Convert to SDK Skill objects
        loaded_skills = self._convert_skills_dict_to_list(skills_by_name)

        logger.info(f"Loaded {len(loaded_skills)} skills")
        if loaded_skills:
            logger.debug(f"Skills: {[s.name for s in loaded_skills]}")

        # Create AgentContext - fall back to public skills if none loaded
        if loaded_skills:
            agent_context = AgentContext(skills=loaded_skills, load_public_skills=False)
        else:
            logger.warning("No skills loaded, falling back to public skills")
            agent_context = AgentContext(skills=[], load_public_skills=True)

        return loaded_skills, agent_context

    def _convert_skill_data_to_skill(self, skill_data: dict[str, Any]) -> Skill:
        """Convert skill dict from API response to SDK Skill object.

        Args:
            skill_data: Dict with name, content, triggers, source, description, etc.

        Returns:
            Skill object
        """
        from openhands.sdk.skills import KeywordTrigger, Skill, TaskTrigger

        trigger = None
        triggers = skill_data.get("triggers", [])

        if triggers:
            # Determine trigger type based on content (same logic as OpenHands)
            if any(t.startswith("/") for t in triggers):
                trigger = TaskTrigger(triggers=triggers)
            else:
                trigger = KeywordTrigger(keywords=triggers)

        return Skill(
            name=skill_data.get("name", "unknown"),
            content=skill_data.get("content", ""),
            trigger=trigger,
            source=skill_data.get("source"),
            description=skill_data.get("description"),
            is_agentskills_format=skill_data.get("is_agentskills_format", False),
        )
