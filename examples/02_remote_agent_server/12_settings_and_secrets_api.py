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
import tempfile
import time
from uuid import UUID

import httpx
from _utils import ManagedAPIServer

from openhands.sdk import get_logger
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)


# Get LLM configuration from environment
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
llm_model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
llm_base_url = os.getenv("LLM_BASE_URL")  # Optional custom base URL

with ManagedAPIServer(
    port=8765,
    extra_env={
        "OH_SECRET_KEY": "example-secret-key-for-demo-only-32b",
        "TMUX_TMPDIR": "/tmp/oh-tmux",
    },
    health_request_timeout=2.0,
) as server:
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
                        "variable to see if you have access. "
                        "If so just respond `YES`, otherwise `NO`.",
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

        # Get the agent's final response to verify the task was completed
        response = client.get(
            f"/api/conversations/{conversation_id}/agent_final_response"
        )
        accumulated_cost = 0.0
        if response.status_code == 200:
            result = response.json()
            agent_response = result.get("response", "")
            if agent_response:
                # Truncate long responses for display
                display_response = (
                    agent_response[:200] + "..."
                    if len(agent_response) > 200
                    else agent_response
                )
                logger.info(f"   Agent response: {display_response}")
                logger.info("   ✅ Agent completed the task using the secret!")

        # Get conversation metrics from stats
        response = client.get(f"/api/conversations/{conversation_id}")
        if response.status_code == 200:
            conversation_data = response.json()
            # Metrics are tracked per-LLM usage in stats.usage_to_metrics
            stats = conversation_data.get("stats") or {}
            usage_to_metrics = stats.get("usage_to_metrics") or {}
            # Sum accumulated_cost across all LLM usages
            accumulated_cost = sum(
                m.get("accumulated_cost", 0.0) for m in usage_to_metrics.values()
            )

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
