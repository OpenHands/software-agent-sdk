"""Example demonstrating the Settings and Secrets API with a local agent server.

This example shows how to:
1. Manage agent settings (GET, PATCH)
2. Manage custom secrets (CRUD operations)
3. Handle secret name validation
4. Work with encrypted secrets

The example runs entirely against the REST API without requiring an LLM,
making it suitable for testing the settings/secrets persistence layer.
"""

import os
import subprocess
import sys
import threading
import time

import httpx

from openhands.sdk import get_logger


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
                response = httpx.get(f"{self.base_url}/ready", timeout=2.0)
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


def main():
    """Demonstrate the Settings and Secrets API."""
    with ManagedAPIServer(port=8765) as server:
        client = httpx.Client(base_url=server.base_url, timeout=10.0)

        try:
            # ══════════════════════════════════════════════════════════════
            # 1. GET Settings
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("📋 Getting current settings")
            logger.info("=" * 60)

            response = client.get("/api/settings")
            assert response.status_code == 200, f"GET settings failed: {response.text}"
            settings = response.json()

            logger.info("✅ Settings retrieved successfully")
            logger.info(f"   - LLM model: {settings['agent_settings']['llm']['model']}")
            logger.info(f"   - LLM API key set: {settings['llm_api_key_is_set']}")

            # ══════════════════════════════════════════════════════════════
            # 2. PATCH Settings (update LLM model)
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("🔧 Updating settings (changing LLM model)")
            logger.info("=" * 60)

            response = client.patch(
                "/api/settings",
                json={"agent_settings_diff": {"llm": {"model": "gpt-4o-mini"}}},
            )
            assert response.status_code == 200, (
                f"PATCH settings failed: {response.text}"
            )
            updated = response.json()

            logger.info("✅ Settings updated successfully")
            logger.info(
                f"   - New LLM model: {updated['agent_settings']['llm']['model']}"
            )

            # ══════════════════════════════════════════════════════════════
            # 3. Create Custom Secrets
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("🔐 Creating custom secrets")
            logger.info("=" * 60)

            # Create first secret
            response = client.put(
                "/api/settings/secrets",
                json={
                    "name": "MY_API_KEY",
                    "value": "sk-example-key-12345",
                    "description": "Example API key for demonstration",
                },
            )
            assert response.status_code == 200, f"Create secret failed: {response.text}"
            logger.info("✅ Created secret: MY_API_KEY")

            # Create second secret
            response = client.put(
                "/api/settings/secrets",
                json={
                    "name": "DATABASE_URL",
                    "value": "postgresql://localhost:5432/mydb",
                },
            )
            assert response.status_code == 200
            logger.info("✅ Created secret: DATABASE_URL")

            # ══════════════════════════════════════════════════════════════
            # 4. List Secrets
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("📜 Listing all secrets")
            logger.info("=" * 60)

            response = client.get("/api/settings/secrets")
            assert response.status_code == 200
            secrets = response.json()["secrets"]

            logger.info(f"✅ Found {len(secrets)} secrets:")
            for secret in secrets:
                desc = secret.get("description") or "(no description)"
                logger.info(f"   - {secret['name']}: {desc}")

            # ══════════════════════════════════════════════════════════════
            # 5. Get Secret Value
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("🔍 Retrieving secret value")
            logger.info("=" * 60)

            response = client.get("/api/settings/secrets/MY_API_KEY")
            assert response.status_code == 200
            value = response.text

            logger.info(f"✅ Retrieved MY_API_KEY value: {value[:10]}...")

            # ══════════════════════════════════════════════════════════════
            # 6. Update Secret (upsert)
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("🔄 Updating secret value")
            logger.info("=" * 60)

            response = client.put(
                "/api/settings/secrets",
                json={
                    "name": "MY_API_KEY",
                    "value": "sk-updated-key-67890",
                    "description": "Updated API key",
                },
            )
            assert response.status_code == 200

            # Verify update
            response = client.get("/api/settings/secrets/MY_API_KEY")
            assert response.text == "sk-updated-key-67890"
            logger.info("✅ Secret updated successfully")

            # ══════════════════════════════════════════════════════════════
            # 7. Secret Name Validation
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
            logger.info("✅ Rejected invalid name '123_invalid' (starts with number)")

            # Invalid: contains hyphen
            response = client.put(
                "/api/settings/secrets",
                json={"name": "invalid-name", "value": "test"},
            )
            assert response.status_code == 422
            logger.info("✅ Rejected invalid name 'invalid-name' (contains hyphen)")

            # ══════════════════════════════════════════════════════════════
            # 8. Delete Secret
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("🗑️  Deleting secrets")
            logger.info("=" * 60)

            response = client.delete("/api/settings/secrets/MY_API_KEY")
            assert response.status_code == 200
            assert response.json()["deleted"] is True
            logger.info("✅ Deleted secret: MY_API_KEY")

            # Verify deletion
            response = client.get("/api/settings/secrets/MY_API_KEY")
            assert response.status_code == 404
            logger.info("✅ Confirmed secret no longer exists")

            # Cleanup remaining secret
            client.delete("/api/settings/secrets/DATABASE_URL")
            logger.info("✅ Deleted secret: DATABASE_URL")

            # ══════════════════════════════════════════════════════════════
            # 9. Settings with LLM API Key
            # ══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 60)
            logger.info("🔑 Testing LLM API key in settings")
            logger.info("=" * 60)

            response = client.patch(
                "/api/settings",
                json={"agent_settings_diff": {"llm": {"api_key": "sk-llm-test-key"}}},
            )
            assert response.status_code == 200
            result = response.json()

            # Key should be set but redacted in response
            assert result["llm_api_key_is_set"] is True
            assert result["agent_settings"]["llm"]["api_key"] == "**********"
            logger.info("✅ LLM API key set (redacted in response)")

            logger.info("\n" + "=" * 60)
            logger.info("🎉 All Settings and Secrets API tests passed!")
            logger.info("=" * 60)

            # This example doesn't use LLM, so cost is 0
            print("EXAMPLE_COST: 0.0")

        finally:
            client.close()


if __name__ == "__main__":
    main()
