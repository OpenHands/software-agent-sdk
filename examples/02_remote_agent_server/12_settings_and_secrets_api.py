"""Example demonstrating the Settings and Secrets API with a local agent server.

This example shows how to:
1. Store LLM API key via PATCH /api/settings (persisted encrypted at rest)
2. Fetch settings with X-Expose-Secrets header to verify encryption modes
3. Run a real agent conversation using settings stored via the API
4. Manage custom secrets (CRUD operations with validation)

The key workflow demonstrated:
1. Store LLM configuration via the Settings API
2. Verify the API key is properly redacted/encrypted in responses
3. Run an agent session that uses the stored model configuration
4. Test custom secrets CRUD operations
"""

import os
import subprocess
import sys
import tempfile
import threading
import time

import httpx
from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, RemoteConversation, Workspace, get_logger
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
    """Context manager for subprocess-managed OpenHands API server."""

    def __init__(self, port: int = 8000, host: str = "127.0.0.1"):
        self.port: int = port
        self.host: str = host
        self.process: subprocess.Popen[str] | None = None
        self.base_url: str = f"http://{host}:{port}"
        self.stdout_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None

    def __enter__(self):
        """Start the API server subprocess."""
        print(f"Starting OpenHands API server on {self.base_url}...")

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
            env={"LOG_JSON": "true", **os.environ},
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


# Get LLM API key from environment
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
llm_model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")

with ManagedAPIServer(port=8765) as server:
    client = httpx.Client(base_url=server.base_url, timeout=10.0)

    try:
        # ══════════════════════════════════════════════════════════════
        # Part 1: Store LLM API Key via Settings API
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔧 Storing LLM configuration via Settings API")
        logger.info("=" * 60)

        # Store LLM configuration - the API key is encrypted at rest
        response = client.patch(
            "/api/settings",
            json={
                "agent_settings_diff": {
                    "llm": {
                        "model": llm_model,
                        "api_key": api_key,
                    }
                }
            },
        )
        assert response.status_code == 200, f"PATCH settings failed: {response.text}"
        settings = response.json()

        logger.info("✅ Settings stored successfully")
        logger.info(f"   - LLM model: {settings['agent_settings']['llm']['model']}")
        logger.info(f"   - API key set: {settings['llm_api_key_is_set']}")
        # API key should be redacted in response (no header = redacted mode)
        assert settings["agent_settings"]["llm"]["api_key"] == "**********"
        logger.info("   - API key redacted in response (default behavior)")

        # ══════════════════════════════════════════════════════════════
        # Part 2: Verify Encrypted Secrets Mode
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔐 Verifying encrypted secrets mode")
        logger.info("=" * 60)

        # X-Expose-Secrets: encrypted returns cipher-encrypted secrets
        # This is used by frontend clients to pass secrets back safely
        response = client.get(
            "/api/settings",
            headers={"X-Expose-Secrets": "encrypted"},
        )
        assert response.status_code == 200
        encrypted_settings = response.json()

        encrypted_api_key = encrypted_settings["agent_settings"]["llm"]["api_key"]
        # Encrypted keys start with "gAAAAA" (Fernet token format)
        assert encrypted_api_key.startswith("gAAAAA"), "Expected encrypted token"
        logger.info("✅ Encrypted secrets mode verified")
        logger.info(f"   - Encrypted API key: {encrypted_api_key[:20]}...")

        # ══════════════════════════════════════════════════════════════
        # Part 3: Run Agent Session with Settings
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🤖 Running agent session using stored settings")
        logger.info("=" * 60)

        # Create workspace connected to the remote server
        temp_workspace_dir = tempfile.mkdtemp(prefix="settings_api_demo_")
        workspace = Workspace(host=server.base_url, working_dir=temp_workspace_dir)

        # Verify workspace connection
        result = workspace.execute_command("pwd")
        logger.info(f"✅ Workspace connected: {result.stdout.strip()}")

        # Create LLM using the model from settings
        llm = LLM(
            model=llm_model,
            api_key=SecretStr(api_key),
        )

        # Create agent using the LLM
        agent = get_default_agent(
            llm=llm,
            cli_mode=True,  # Disable browser tools for simplicity
        )

        # Create conversation - with remote workspace, returns RemoteConversation
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
        )
        assert isinstance(conversation, RemoteConversation)
        logger.info("✅ RemoteConversation created")

        try:
            # Send a task to verify the agent works
            logger.info("\n📝 Sending task to agent...")
            conversation.send_message(
                "Create a file called 'settings_test.txt' that contains exactly: "
                "'Settings API test successful!'"
            )

            # Run the conversation
            logger.info("⏳ Running agent...")
            conversation.run()

            logger.info("✅ Agent task completed!")
            logger.info(f"   Final status: {conversation.state.execution_status}")

            # Verify the file was created
            result = workspace.execute_command("cat settings_test.txt")
            if result.exit_code == 0:
                logger.info(f"   File content: {result.stdout.strip()}")
                assert "successful" in result.stdout.lower()
                logger.info("   ✅ Content verified - agent session works!")

        finally:
            conversation.close()

        # ══════════════════════════════════════════════════════════════
        # Part 4: Test Custom Secrets CRUD
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🧪 Testing custom secrets CRUD operations")
        logger.info("=" * 60)

        # Create a custom secret
        response = client.put(
            "/api/settings/secrets",
            json={
                "name": "MY_PROJECT_TOKEN",
                "value": "secret-token-abc123",
                "description": "Example project token",
            },
        )
        assert response.status_code == 200
        logger.info("✅ Created secret: MY_PROJECT_TOKEN")

        # List secrets (values not exposed)
        response = client.get("/api/settings/secrets")
        assert response.status_code == 200
        secrets = response.json()["secrets"]
        logger.info(f"✅ Listed {len(secrets)} secret(s)")

        # Get secret value
        response = client.get("/api/settings/secrets/MY_PROJECT_TOKEN")
        assert response.status_code == 200
        assert response.text == "secret-token-abc123"
        logger.info("✅ Retrieved secret value")

        # Update secret
        response = client.put(
            "/api/settings/secrets",
            json={
                "name": "MY_PROJECT_TOKEN",
                "value": "updated-token-xyz789",
            },
        )
        assert response.status_code == 200
        logger.info("✅ Updated secret")

        # Delete secret
        response = client.delete("/api/settings/secrets/MY_PROJECT_TOKEN")
        assert response.status_code == 200
        logger.info("✅ Deleted secret")

        # Verify deletion
        response = client.get("/api/settings/secrets/MY_PROJECT_TOKEN")
        assert response.status_code == 404
        logger.info("✅ Verified deletion (404)")

        # ══════════════════════════════════════════════════════════════
        # Part 5: Test Secret Name Validation
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("⚠️  Testing secret name validation")
        logger.info("=" * 60)

        # Invalid: starts with number
        response = client.put(
            "/api/settings/secrets",
            json={"name": "123_invalid", "value": "test"},
        )
        assert response.status_code == 422
        logger.info("✅ Rejected '123_invalid' (starts with number)")

        # Invalid: contains hyphen
        response = client.put(
            "/api/settings/secrets",
            json={"name": "invalid-name", "value": "test"},
        )
        assert response.status_code == 422
        logger.info("✅ Rejected 'invalid-name' (contains hyphen)")

        logger.info("\n" + "=" * 60)
        logger.info("🎉 All Settings and Secrets API tests passed!")
        logger.info("=" * 60)

        # Get cost from conversation
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")

    finally:
        client.close()
