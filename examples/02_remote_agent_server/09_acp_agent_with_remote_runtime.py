"""Example: ACPAgent with Remote Runtime via API.

This example demonstrates running an ACPAgent (Claude Code via ACP protocol)
in a remote sandboxed environment via Runtime API. It follows the same pattern
as 04_convo_with_api_sandboxed_server.py but uses ACPAgent instead of the
default LLM-based Agent.

Usage:
  uv run examples/02_remote_agent_server/07_acp_agent_with_remote_runtime.py

Requirements:
  - ANTHROPIC_API_KEY: API key for Claude (forwarded to the container)
  - RUNTIME_API_KEY: API key for runtime API access
"""

import os
import time

from openhands.sdk import (
    Conversation,
    RemoteConversation,
    get_logger,
)
from openhands.sdk.agent import ACPAgent
from openhands.workspace import APIRemoteWorkspace

logger = get_logger(__name__)


anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
assert anthropic_api_key, "ANTHROPIC_API_KEY required"

runtime_api_key = os.getenv("RUNTIME_API_KEY")
assert runtime_api_key, "RUNTIME_API_KEY required"

# If GITHUB_SHA is set (e.g. running in CI of a PR), use that to ensure consistency
# Otherwise, use the latest image from main
server_image_sha = os.getenv("GITHUB_SHA") or "main"
server_image = f"ghcr.io/openhands/agent-server:{server_image_sha[:7]}-python-amd64"
logger.info(f"Using server image: {server_image}")

with APIRemoteWorkspace(
    runtime_api_url=os.getenv("RUNTIME_API_URL", "https://runtime.eval.all-hands.dev"),
    runtime_api_key=runtime_api_key,
    server_image=server_image,
    image_pull_policy="Always",
    target_type="binary",  # CI builds binary target images
    forward_env=["ANTHROPIC_API_KEY"],
) as workspace:
    agent = ACPAgent(
        acp_command=["claude-code-acp"],  # Pre-installed in Docker image
    )

    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        received_events.append(event)
        last_event_time["ts"] = time.time()

    conversation = Conversation(
        agent=agent, workspace=workspace, callbacks=[event_callback]
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        conversation.send_message(
            "List the files in /workspace and describe what you see."
        )
        conversation.run()

        while time.time() - last_event_time["ts"] < 2.0:
            time.sleep(0.1)
    finally:
        conversation.close()
