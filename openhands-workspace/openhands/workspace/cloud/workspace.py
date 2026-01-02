"""OpenHands Cloud workspace implementation using Cloud API."""

from typing import Any
from urllib.request import urlopen

import httpx
import tenacity
from pydantic import Field, PrivateAttr

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.remote.base import RemoteWorkspace


logger = get_logger(__name__)

# Standard exposed URL names from OpenHands Cloud
AGENT_SERVER = "AGENT_SERVER"


class OpenHandsCloudWorkspace(RemoteWorkspace):
    """Remote workspace using OpenHands Cloud API.

    This workspace connects to OpenHands Cloud (app.all-hands.dev) to provision
    and manage sandboxed environments for agent execution.

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
    """

    # Parent fields
    working_dir: str = Field(
        default="/workspace/project",
        description="Working directory inside the sandbox",
    )
    host: str = Field(
        default="undefined",
        description="The agent server URL. Set automatically after sandbox starts.",
    )

    # Cloud API fields
    cloud_api_url: str = Field(
        description="Base URL of OpenHands Cloud API (e.g., https://app.all-hands.dev)"
    )
    cloud_api_key: str = Field(
        description="API key for authenticating with OpenHands Cloud"
    )
    sandbox_spec_id: str | None = Field(
        default=None,
        description="Optional sandbox specification ID (e.g., container image)",
    )

    # Lifecycle options
    init_timeout: float = Field(
        default=300.0, description="Sandbox initialization timeout in seconds"
    )
    api_timeout: float = Field(
        default=60.0, description="API request timeout in seconds"
    )
    keep_alive: bool = Field(
        default=False,
        description="If True, keep sandbox alive on cleanup instead of deleting",
    )

    # Private state
    _sandbox_id: str | None = PrivateAttr(default=None)
    _session_api_key: str | None = PrivateAttr(default=None)
    _exposed_urls: list[dict[str, Any]] | None = PrivateAttr(default=None)

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

        try:
            self._start_sandbox()
            super().model_post_init(context)
        except Exception:
            self.cleanup()
            raise

    def _start_sandbox(self) -> None:
        """Start a new sandbox via Cloud API."""
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
        """Clean up the sandbox by deleting it."""
        if not self._sandbox_id:
            return

        try:
            if self.keep_alive:
                logger.info(f"Keeping sandbox {self._sandbox_id} alive")
                return

            logger.info(f"Deleting sandbox {self._sandbox_id}...")
            self._send_api_request(
                "DELETE",
                f"{self.cloud_api_url}/api/v1/sandboxes",
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

    def __del__(self) -> None:
        self.cleanup()

    def __enter__(self) -> "OpenHandsCloudWorkspace":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.cleanup()
