"""Example demonstrating workspace.get_llm() for settings-driven conversations.

This example shows how to use the new RemoteWorkspace settings methods with
API key authentication for secure access:

1. Spin up an agent-server with a session API key configured
2. Configure LLM settings via the Settings API (requires API key auth)
3. Use workspace.get_llm() to retrieve a configured LLM (also authenticated)
4. Start a conversation using the retrieved LLM

Security Model:
- The agent-server is configured with SESSION_API_KEY env var
- All requests must include the X-Session-API-Key header
- RemoteWorkspace.api_key parameter sets this header automatically
- LookupSecrets include the API key in their headers for resolution

This pattern enables:
- Secure centralized LLM configuration on the agent-server
- Authenticated access to settings and secrets
- Consistent security across all workspace operations
"""

import os
import secrets
import subprocess
import sys
import threading
import time

import httpx

from openhands.sdk import Conversation, get_logger
from openhands.sdk.workspace.remote.base import RemoteWorkspace
from openhands.tools.preset.default import get_default_agent


logger = get_logger(__name__)


def _stream_output(stream, prefix, target_stream):
    """Stream output from subprocess to target stream with prefix."""
    try:
        for line in iter(stream.readline, ""):
            if line:
                target_stream.write(f"[{prefix}] {line}")
                target_stream.flush()
    except Exception as e:
        print(f"Error streaming {prefix}: {e}", file=sys.stderr)
    finally:
        stream.close()


class ManagedAPIServer:
    """Context manager for subprocess-managed OpenHands API server.

    Launches an agent-server with a randomly generated session API key
    for secure access. All API requests must include this key.
    """

    def __init__(self, port: int = 8000, host: str = "127.0.0.1"):
        self.port: int = port
        self.host: str = host
        self.process: subprocess.Popen[str] | None = None
        self.base_url: str = f"http://{host}:{port}"
        # Generate a random session API key for this server instance
        self.session_api_key: str = secrets.token_urlsafe(32)
        self.stdout_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None

    def __enter__(self):
        """Start the API server subprocess with session API key auth."""
        print(f"Starting OpenHands API server on {self.base_url}...")
        print("🔐 Session API key configured (required for all requests)")

        # Configure server with security:
        # - OH_SECRET_KEY: enables encrypted storage of secrets
        # - SESSION_API_KEY: requires all requests to be authenticated
        env = {
            "LOG_JSON": "true",
            "OH_SECRET_KEY": "example-secret-key-for-demo-only-32b",
            "SESSION_API_KEY": self.session_api_key,  # Enable auth!
            "TMUX_TMPDIR": "/tmp/oh-tmux",
            **os.environ,
        }

        self.process = subprocess.Popen(
            [
                "python",
                "-m",
                "openhands.agent_server",
                "--port",
                str(self.port),
                "--host",
                self.host,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        assert self.process is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.stdout_thread = threading.Thread(
            target=_stream_output,
            args=(self.process.stdout, "SERVER", sys.stdout),
            daemon=True,
        )
        self.stderr_thread = threading.Thread(
            target=_stream_output,
            args=(self.process.stderr, "SERVER", sys.stderr),
            daemon=True,
        )
        self.stdout_thread.start()
        self.stderr_thread.start()

        # Wait for server to be ready
        max_retries = 30
        for i in range(max_retries):
            try:
                response = httpx.get(f"{self.base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    print(f"✅ Server ready after {i + 1} attempts")
                    return self
            except httpx.RequestError:
                pass
            time.sleep(1)

        raise RuntimeError(f"Server failed to start after {max_retries} seconds")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop the API server subprocess."""
        if self.process:
            print("Stopping API server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            print("✅ Server stopped")


# Get LLM configuration from environment
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
llm_model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
llm_base_url = os.getenv("LLM_BASE_URL")  # Optional custom base URL

with ManagedAPIServer(port=8766) as server:
    # Create HTTP client for settings API - MUST include session API key!
    # The X-Session-API-Key header authenticates all requests
    client = httpx.Client(
        base_url=server.base_url,
        timeout=120.0,
        headers={"X-Session-API-Key": server.session_api_key},
    )

    try:
        # ══════════════════════════════════════════════════════════════
        # Part 0: Demonstrate Authentication Requirement
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔐 Demonstrating API key authentication")
        logger.info("=" * 60)

        # Request WITHOUT api key should fail (401 Unauthorized)
        unauthenticated = httpx.Client(base_url=server.base_url, timeout=10.0)
        response = unauthenticated.get("/api/settings")
        assert response.status_code == 401, (
            f"Expected 401 without API key, got {response.status_code}"
        )
        logger.info("✅ Request without API key rejected (401 Unauthorized)")
        unauthenticated.close()

        # Request WITH api key should succeed
        response = client.get("/api/settings")
        assert response.status_code == 200, f"Authenticated request failed: {response}"
        logger.info("✅ Request with API key accepted (200 OK)")

        # ══════════════════════════════════════════════════════════════
        # Part 1: Configure LLM Settings on Agent-Server
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔧 Configuring LLM settings on agent-server")
        logger.info("=" * 60)

        # Store LLM configuration via the Settings API
        llm_config: dict[str, str] = {
            "model": llm_model,
            "api_key": api_key,
        }
        if llm_base_url:
            llm_config["base_url"] = llm_base_url

        response = client.patch(
            "/api/settings",
            json={"agent_settings_diff": {"llm": llm_config}},
        )
        assert response.status_code == 200, f"PATCH settings failed: {response.text}"
        settings = response.json()

        logger.info("✅ LLM settings stored successfully")
        logger.info(f"   - Model: {settings['agent_settings']['llm']['model']}")
        logger.info(f"   - API key set: {settings['llm_api_key_is_set']}")

        # ══════════════════════════════════════════════════════════════
        # Part 2: Create Workspace and Retrieve LLM via get_llm()
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔗 Creating workspace and retrieving LLM configuration")
        logger.info("=" * 60)

        # Create a RemoteWorkspace with API key authentication!
        # The api_key is used for X-Session-API-Key header on all requests,
        # including get_llm(), get_secrets(), and get_mcp_config().
        workspace = RemoteWorkspace(
            host=server.base_url,
            working_dir="/tmp/workspace_get_llm_demo",
            api_key=server.session_api_key,  # Authenticate workspace requests
        )

        logger.info("✅ Workspace created with session API key")

        # Use get_llm() to retrieve LLM configured on the agent-server!
        # This calls GET /api/settings with both:
        # - X-Session-API-Key (authentication)
        # - X-Expose-Secrets: plaintext (to get the actual API key value)
        llm = workspace.get_llm()

        logger.info("✅ Retrieved LLM from workspace.get_llm()")
        logger.info(f"   - Model: {llm.model}")
        logger.info(f"   - Base URL: {llm.base_url or '(default)'}")

        # You can also override specific settings:
        # llm_custom = workspace.get_llm(model="gpt-4o", temperature=0.5)

        # ══════════════════════════════════════════════════════════════
        # Part 3: Create Agent and Start Conversation
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🤖 Creating agent with retrieved LLM")
        logger.info("=" * 60)

        # Create agent using the LLM from workspace settings
        agent = get_default_agent(llm=llm, cli_mode=True)

        logger.info("✅ Agent created with workspace LLM settings")

        # ══════════════════════════════════════════════════════════════
        # Part 4: Start Conversation and Run Task
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("💬 Starting conversation")
        logger.info("=" * 60)

        # Create conversation using the workspace and agent
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
        )

        try:
            logger.info(f"   Conversation ID: {conversation.state.id}")

            # Send a simple task
            conversation.send_message("What is 2 + 2? Just respond with the number.")
            logger.info("📝 Sent message, running conversation...")
            conversation.run()

            logger.info("✅ Conversation completed!")
            logger.info(f"   Status: {conversation.state.execution_status}")

            # Get cost metrics
            cost = (
                conversation.conversation_stats.get_combined_metrics().accumulated_cost
            )
            logger.info(f"   Cost: ${cost:.6f}")

            print(f"EXAMPLE_COST: {cost}")

        finally:
            conversation.close()
            logger.info("🧹 Conversation closed")

        # ══════════════════════════════════════════════════════════════
        # Part 5: Demonstrate get_secrets() with API Key Auth
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔐 Demonstrating get_secrets() and get_mcp_config()")
        logger.info("=" * 60)

        # Store a test secret
        response = client.put(
            "/api/settings/secrets",
            json={
                "name": "TEST_SECRET",
                "value": "secret-value-123",
                "description": "Test secret for demo",
            },
        )
        assert response.status_code == 200

        # Retrieve secrets via workspace.get_secrets()
        # The returned LookupSecrets include the API key in their headers
        # so they can authenticate when resolved by the agent-server
        workspace_secrets = workspace.get_secrets()
        logger.info(
            f"✅ Retrieved {len(workspace_secrets)} secret(s) via "
            "workspace.get_secrets()"
        )
        for name, lookup_secret in workspace_secrets.items():
            logger.info(f"   - {name}: LookupSecret")
            logger.info(f"     URL: {lookup_secret.url}")
            # The LookupSecret includes the X-Session-API-Key header
            # so it can authenticate when resolved
            has_auth = "X-Session-API-Key" in (lookup_secret.headers or {})
            logger.info(f"     Has API key header: {has_auth}")

        # Clean up test secret
        client.delete("/api/settings/secrets/TEST_SECRET")
        logger.info("   Test secret deleted")

        # get_mcp_config() returns empty dict if no MCP config is set
        mcp_config = workspace.get_mcp_config()
        logger.info(f"✅ MCP config: {mcp_config or '(none configured)'}")

        logger.info("\n" + "=" * 60)
        logger.info("🎉 Example completed successfully!")
        logger.info("=" * 60)
        logger.info("""
Key takeaways:
1. Agent-server can be secured with SESSION_API_KEY env var
2. RemoteWorkspace.api_key passes X-Session-API-Key header
3. workspace.get_llm() retrieves LLM with authentication
4. workspace.get_secrets() returns LookupSecrets with auth headers
5. workspace.get_mcp_config() retrieves MCP config with auth
""")

    finally:
        client.close()
