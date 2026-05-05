"""Example demonstrating the Settings and Secrets API.

This example shows the recommended workflow for managing secrets:
1. Store secrets via PUT /api/settings/secrets (encrypted at rest)
2. Reference secrets in conversations via LookupSecret
3. Agent uses secrets via environment variables ($SECRET_NAME)
4. Clean up secrets via DELETE /api/settings/secrets/{name}

This pattern enables:
- Secure secret storage (encrypted at rest with OH_SECRET_KEY)
- Lazy secret resolution at runtime (via LookupSecret URLs)
- Fine-grained secret lifecycle management (CRUD operations)
- Audit trail for secret access
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
        # Set TMUX_TMPDIR to a short path to avoid socket path length issues on macOS
        env = {
            "LOG_JSON": "true",
            "OH_SECRET_KEY": "example-secret-key-for-demo-only-32b",
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

with ManagedAPIServer(port=8765) as server:
    client = httpx.Client(base_url=server.base_url, timeout=120.0)

    try:
        # ══════════════════════════════════════════════════════════════
        # Part 1: Store LLM Settings via Settings API
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔧 Storing LLM configuration via Settings API")
        logger.info("=" * 60)

        # Store LLM configuration - the API key is encrypted at rest
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
        logger.info(f"   - LLM model: {settings['agent_settings']['llm']['model']}")
        if llm_base_url:
            logger.info(f"   - Base URL: {llm_base_url}")
        logger.info(f"   - API key set: {settings['llm_api_key_is_set']}")

        # ══════════════════════════════════════════════════════════════
        # Part 2: Store Custom Secret via Secrets API
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🔐 Storing custom secret via Secrets API")
        logger.info("=" * 60)

        # Store a custom secret - this could be an API token, database password, etc.
        # The secret is encrypted at rest using OH_SECRET_KEY
        secret_name = "MY_PROJECT_TOKEN"
        secret_value = "super-secret-token-12345"

        response = client.put(
            "/api/settings/secrets",
            json={
                "name": secret_name,
                "value": secret_value,
                "description": "Example project token for demonstration",
            },
        )
        assert response.status_code == 200, f"PUT secret failed: {response.text}"
        logger.info(f"✅ Created secret: {secret_name}")

        # List secrets to verify (values are not exposed)
        response = client.get("/api/settings/secrets")
        assert response.status_code == 200
        secrets_list = response.json()["secrets"]
        logger.info(f"✅ Server has {len(secrets_list)} secret(s) stored")
        for secret in secrets_list:
            logger.info(f"   - {secret['name']}: {secret.get('description', '')}")

        # ══════════════════════════════════════════════════════════════
        # Part 3: Start Conversation with LookupSecret Reference
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🤖 Starting conversation with secret reference")
        logger.info("=" * 60)

        # Create a workspace directory
        temp_workspace_dir = tempfile.mkdtemp(prefix="secrets_api_demo_")

        # Build the LookupSecret URL - agent server resolves this at runtime
        # The URL points to the secrets endpoint on the same server
        lookup_url = f"{server.base_url}/api/settings/secrets/{secret_name}"

        # Start conversation with LookupSecret reference
        # The secret will be resolved lazily when the agent needs it
        start_request = {
            "agent": {
                "kind": "Agent",
                "llm": llm_config,  # Use same LLM config (model, api_key, base_url)
                "tools": [
                    {"name": TerminalTool.name},
                    {"name": FileEditorTool.name},
                ],
            },
            "workspace": {"working_dir": temp_workspace_dir},
            # Reference the stored secret via LookupSecret
            # This creates an environment variable $MY_PROJECT_TOKEN in the agent
            "secrets": {
                secret_name: {
                    "kind": "LookupSecret",
                    "url": lookup_url,
                    "description": "Project token resolved from secrets API",
                }
            },
            "initial_message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Echo the value of the ${secret_name} environment "
                        "variable and save it to a file called 'secret_test.txt'. "
                        "Then confirm the file was created.",
                    }
                ],
                "run": True,  # Auto-run after sending message
            },
        }

        response = client.post("/api/conversations", json=start_request)
        assert response.status_code == 201, (
            f"Start conversation failed: {response.text}"
        )
        conversation_info = response.json()
        conversation_id = UUID(conversation_info["id"])

        logger.info("✅ Conversation started!")
        logger.info(f"   - Conversation ID: {conversation_id}")
        logger.info(f"   - Secret '{secret_name}' available as env var")

        # ══════════════════════════════════════════════════════════════
        # Part 4: Wait for Agent to Complete
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("⏳ Waiting for agent to complete task...")
        logger.info("=" * 60)

        # Poll conversation until agent finishes
        max_wait = 120  # seconds
        poll_interval = 2
        elapsed = 0
        execution_status = "unknown"

        while elapsed < max_wait:
            response = client.get(f"/api/conversations/{conversation_id}")
            assert response.status_code == 200
            conversation_data = response.json()
            execution_status = conversation_data.get("execution_status", "unknown")

            if execution_status in ("stopped", "paused", "error"):
                break

            logger.info(f"   Status: {execution_status} (waited {elapsed}s)")
            time.sleep(poll_interval)
            elapsed += poll_interval

        logger.info(f"✅ Agent finished with status: {execution_status}")

        # Verify the secret was used correctly
        response = client.post(
            f"/api/conversations/{conversation_id}/execute",
            json={
                "command": "cat secret_test.txt 2>/dev/null || echo 'File not found'"
            },
        )
        accumulated_cost = 0.0
        if response.status_code == 200:
            result = response.json()
            file_content = result.get("stdout", "").strip()
            logger.info(f"   File content: {file_content}")
            # The file should contain the secret value
            if secret_value in file_content:
                logger.info("   ✅ Secret was correctly resolved and used!")
            else:
                logger.info("   ⚠️  Secret may not have been written to file")

        # Get conversation metrics
        conversation_data = response.json() if response.status_code == 200 else {}
        metrics = conversation_data.get("metrics", {})
        accumulated_cost = metrics.get("accumulated_cost", 0.0)

        # Clean up - delete conversation
        client.delete(f"/api/conversations/{conversation_id}")
        logger.info("   Conversation deleted")

        # ══════════════════════════════════════════════════════════════
        # Part 5: Clean Up - Delete the Secret
        # ══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("🧹 Cleaning up - deleting secret")
        logger.info("=" * 60)

        # Delete the secret after use
        response = client.delete(f"/api/settings/secrets/{secret_name}")
        assert response.status_code == 200, f"DELETE secret failed: {response.text}"
        logger.info(f"✅ Deleted secret: {secret_name}")

        # Verify deletion
        response = client.get(f"/api/settings/secrets/{secret_name}")
        assert response.status_code == 404
        logger.info("✅ Verified deletion (secret no longer accessible)")

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
