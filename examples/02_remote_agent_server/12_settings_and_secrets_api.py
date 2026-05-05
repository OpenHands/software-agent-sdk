"""Example demonstrating the Settings and Secrets API with encrypted secrets flow.

This example shows the complete defense-in-depth workflow for frontend clients:
1. Store LLM API key via PATCH /api/settings (encrypted at rest)
2. Fetch settings with X-Expose-Secrets: encrypted (cipher-encrypted for transit)
3. Start conversation via POST /api/conversations with secrets_encrypted=True
4. Server decrypts secrets and runs the agent

This is the recommended pattern for frontends that need to:
- Store secrets securely via the Settings API
- Pass encrypted secrets when starting conversations
- Never have access to plaintext secrets after initial storage
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
from uuid import UUID

import httpx

from openhands.sdk import get_logger
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


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

        # Set OH_SECRET_KEY to enable encrypted secrets feature
        # In production, this should be a secure randomly generated key
        env = {
            "LOG_JSON": "true",
            "OH_SECRET_KEY": "example-secret-key-for-demo-only-32b",
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


# Get LLM API key from environment
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
llm_model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")

with ManagedAPIServer(port=8765) as server:
    client = httpx.Client(base_url=server.base_url, timeout=120.0)

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
        # Part 2: Fetch Settings with Encrypted Secrets
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔐 Fetching settings with encrypted secrets")
        logger.info("=" * 60)

        # Frontend clients use X-Expose-Secrets: encrypted to get cipher-encrypted
        # secrets that can be safely passed back to start a conversation
        response = client.get(
            "/api/settings",
            headers={"X-Expose-Secrets": "encrypted"},
        )
        assert response.status_code == 200
        encrypted_settings = response.json()

        encrypted_api_key = encrypted_settings["agent_settings"]["llm"]["api_key"]
        # Encrypted keys start with "gAAAAA" (Fernet token format)
        assert encrypted_api_key.startswith("gAAAAA"), "Expected encrypted token"
        logger.info("✅ Retrieved encrypted settings")
        logger.info(f"   - Encrypted API key: {encrypted_api_key[:20]}...")

        # ══════════════════════════════════════════════════════════════
        # Part 3: Start Conversation via REST API with Encrypted Secrets
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🤖 Starting conversation via POST /api/conversations")
        logger.info("=" * 60)

        # Create a workspace directory
        temp_workspace_dir = tempfile.mkdtemp(prefix="settings_api_demo_")

        # Extract LLM config from encrypted settings response
        # We can use the encrypted settings directly - just need to map fields
        encrypted_llm = encrypted_settings["agent_settings"]["llm"]

        # Build the StartConversationRequest using the encrypted LLM config
        # The server will decrypt the api_key because secrets_encrypted=True
        start_request = {
            "agent": {
                "kind": "Agent",
                "llm": encrypted_llm,  # Use entire LLM config from settings
                "tools": [
                    {"name": TerminalTool.name},
                    {"name": FileEditorTool.name},
                ],
            },
            "workspace": {"working_dir": temp_workspace_dir},
            "secrets_encrypted": True,  # Tell server to decrypt the API key
            "initial_message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Create a file called 'encrypted_secrets_test.txt' "
                            "that contains exactly: 'Encrypted secrets flow works!'"
                        ),
                    }
                ],
                "run": True,  # Auto-run after sending message
            },
        }

        # Start the conversation - server decrypts the API key
        response = client.post("/api/conversations", json=start_request)
        assert response.status_code == 201, (
            f"Start conversation failed: {response.text}"
        )
        conversation_info = response.json()
        conversation_id = UUID(conversation_info["id"])

        logger.info("✅ Conversation started!")
        logger.info(f"   - Conversation ID: {conversation_id}")
        logger.info(f"   - Title: {conversation_info.get('title', '(generating...)')}")

        # ══════════════════════════════════════════════════════════════
        # Part 4: Wait for Agent to Complete
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("⏳ Waiting for agent to complete task...")
        logger.info("=" * 60)

        # Poll conversation state until agent finishes
        max_wait = 120  # seconds
        poll_interval = 2
        elapsed = 0
        execution_status = "unknown"

        while elapsed < max_wait:
            response = client.get(f"/api/conversations/{conversation_id}/state")
            assert response.status_code == 200
            state = response.json()
            execution_status = state.get("execution_status", "unknown")

            if execution_status in ("stopped", "paused", "error"):
                break

            logger.info(f"   Status: {execution_status} (waited {elapsed}s)")
            time.sleep(poll_interval)
            elapsed += poll_interval

        logger.info(f"✅ Agent finished with status: {execution_status}")

        # Verify the file was created
        response = client.post(
            f"/api/conversations/{conversation_id}/execute",
            json={"command": "cat encrypted_secrets_test.txt"},
        )
        if response.status_code == 200:
            result = response.json()
            logger.info(f"   File content: {result.get('stdout', '').strip()}")
            assert "works" in result.get("stdout", "").lower()
            logger.info("   ✅ Encrypted secrets flow verified end-to-end!")

        # Get conversation metrics
        response = client.get(f"/api/conversations/{conversation_id}/state")
        state = response.json()
        accumulated_cost = state.get("accumulated_cost", 0.0)

        # Clean up - delete conversation
        client.delete(f"/api/conversations/{conversation_id}")
        logger.info("   Conversation deleted")

        # ══════════════════════════════════════════════════════════════
        # Part 5: Test Custom Secrets CRUD
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
        # Part 6: Test Secret Name Validation
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

        print(f"EXAMPLE_COST: {accumulated_cost}")

    finally:
        client.close()
