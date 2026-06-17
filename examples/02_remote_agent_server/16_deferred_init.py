"""Example demonstrating deferred-init (warm-pool) mode for the agent server.

In warm-pool deployments, server pods are pre-warmed before a user is matched
to one. The pod boots with ``OH_DEFERRED_INIT=true``: stateless services
(VSCode, tool preload, etc.) start as normal, but all ``/api/*`` routes return
503 until ``POST /api/init`` delivers the runtime configuration (credentials,
workspace paths, session keys).

The orchestrator authenticates the init call with the server's bootstrap secret
key (``OH_SECRET_KEY`` / ``X-Init-API-Key``), which it already holds for
encryption purposes.

Lifecycle demonstrated here:
  1. Server starts in dormant mode.
  2. ``GET /api/init`` reports state=dormant.
  3. ``GET /api/conversations`` returns 503 (dormant gate is active).
  4. ``POST /api/init`` delivers runtime config → server transitions to ready.
  5. ``GET /api/init`` reports state=ready.
  6. A conversation runs normally on the now-ready server.
"""

import os
import tempfile
import time
from uuid import UUID

import httpx
from scripts.utils import ManagedAPIServer

from openhands.sdk import get_logger


logger = get_logger(__name__)

# ── LLM config ──────────────────────────────────────────────────────────────

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
llm_model = os.getenv("LLM_MODEL", "gpt-5.5")
llm_base_url = os.getenv("LLM_BASE_URL")

# The orchestrator knows this key before the pod is matched to a user.
# It's used to authenticate POST /api/init and as the encryption secret.
BOOTSTRAP_SECRET_KEY = "demo-warm-pool-bootstrap-key-32b!"

# ── Server lifecycle ─────────────────────────────────────────────────────────

with ManagedAPIServer(
    port=8003,
    extra_env={
        "OH_DEFERRED_INIT": "true",
        "OH_SECRET_KEY": BOOTSTRAP_SECRET_KEY,
        "TMUX_TMPDIR": "/tmp/oh-tmux-deferred",
    },
) as server:
    client = httpx.Client(base_url=server.base_url, timeout=120.0)

    try:
        # ── 1. Confirm dormant state ─────────────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("📊 Step 1: checking initial (dormant) state")
        logger.info("=" * 60)

        resp = client.get("/api/init")
        assert resp.status_code == 200, f"GET /api/init failed: {resp.text}"
        init_status = resp.json()
        assert init_status["state"] == "dormant", (
            f"Expected dormant, got: {init_status['state']}"
        )
        logger.info(f"✅ Server is dormant — {init_status}")

        # ── 2. Verify the dormant gate blocks /api/* ─────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("🚧 Step 2: dormant gate returns 503 on /api/conversations")
        logger.info("=" * 60)

        resp = client.get("/api/conversations")
        assert resp.status_code == 503, (
            f"Expected 503 from dormant gate, got {resp.status_code}"
        )
        logger.info("✅ /api/conversations correctly returns 503 while dormant")

        # ── 3. Activate via POST /api/init ───────────────────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("🚀 Step 3: activating server via POST /api/init")
        logger.info("=" * 60)

        temp_workspace_dir = tempfile.mkdtemp(prefix="deferred_init_demo_")

        # In a real warm-pool deployment, credentials that the server shouldn't
        # have at cold-start (e.g., the user's LLM API key) would arrive here.
        llm_env: dict[str, str] = {"LLM_API_KEY": api_key}
        if llm_base_url:
            llm_env["LLM_BASE_URL"] = llm_base_url

        init_body: dict = {
            # Pass user credentials into the server's environment.
            "env": llm_env,
        }

        resp = client.post(
            "/api/init",
            json=init_body,
            headers={"X-Init-API-Key": BOOTSTRAP_SECRET_KEY},
        )
        assert resp.status_code == 200, f"POST /api/init failed: {resp.text}"
        init_status = resp.json()
        assert init_status["state"] == "ready", (
            f"Expected ready after init, got: {init_status['state']}"
        )
        logger.info(f"✅ Server is now ready — {init_status}")

        # ── 4. Confirm ready via GET /api/init ───────────────────────────────
        resp = client.get("/api/init")
        assert resp.status_code == 200
        assert resp.json()["state"] == "ready"
        logger.info("✅ GET /api/init confirms ready state")

        # ── 5. Run a conversation on the now-ready server ────────────────────
        logger.info("\n" + "=" * 60)
        logger.info("🤖 Step 5: running a conversation on the ready server")
        logger.info("=" * 60)

        llm_config: dict[str, str] = {"model": llm_model, "api_key": api_key}
        if llm_base_url:
            llm_config["base_url"] = llm_base_url

        start_request: dict = {
            "agent": {
                "kind": "Agent",
                "llm": llm_config,
                "tools": [],
            },
            "workspace": {"working_dir": temp_workspace_dir},
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": "Reply with just the number 42."}],
                "run": True,
            },
        }

        resp = client.post("/api/conversations", json=start_request)
        assert resp.status_code == 201, f"Start conversation failed: {resp.text}"
        conversation_id = UUID(resp.json()["id"])
        logger.info(f"✅ Conversation started: {conversation_id}")

        # Poll until the agent finishes.
        max_wait = 120
        elapsed = 0
        execution_status = "unknown"
        while elapsed < max_wait:
            resp = client.get(f"/api/conversations/{conversation_id}")
            assert resp.status_code == 200
            data = resp.json()
            execution_status = data.get("execution_status", "unknown")
            if execution_status in ("stopped", "paused", "error"):
                break
            logger.info(f"   status: {execution_status} ({elapsed}s elapsed)")
            time.sleep(2)
            elapsed += 2

        logger.info(f"✅ Conversation finished — status: {execution_status}")
        assert execution_status in ("stopped", "paused"), (
            f"Unexpected final status: {execution_status}"
        )

        resp = client.get(f"/api/conversations/{conversation_id}/agent_final_response")
        if resp.status_code == 200:
            agent_response = resp.json().get("response", "")
            logger.info(f"   Agent response: {agent_response!r}")

        # Collect cost metrics.
        accumulated_cost = 0.0
        resp = client.get(f"/api/conversations/{conversation_id}")
        if resp.status_code == 200:
            stats = resp.json().get("stats") or {}
            usage_to_metrics = stats.get("usage_to_metrics") or {}
            accumulated_cost = sum(
                m.get("accumulated_cost", 0.0) for m in usage_to_metrics.values()
            )

        client.delete(f"/api/conversations/{conversation_id}")
        logger.info("   Conversation deleted")

        logger.info("\n" + "=" * 60)
        logger.info("🎉 Deferred-init example completed successfully!")
        logger.info("=" * 60)

        print(f"EXAMPLE_COST: {accumulated_cost}")

    finally:
        client.close()
