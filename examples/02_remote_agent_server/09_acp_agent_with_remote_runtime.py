"""Example: ACPAgent with Remote Runtime via API.

This example demonstrates running an ACPAgent (Claude Code via ACP protocol)
in a remote sandboxed environment via Runtime API. It follows the same pattern
as 04_convo_with_api_sandboxed_server.py but uses ACPAgent instead of the
default LLM-based Agent.

Usage:
  uv run examples/02_remote_agent_server/09_acp_agent_with_remote_runtime.py

Requirements:
  - LLM_BASE_URL: LiteLLM proxy URL (routes Claude Code requests)
  - LLM_API_KEY: LiteLLM virtual API key
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


# ACP agents (Claude Code) route through LiteLLM proxy
llm_base_url = os.getenv("LLM_BASE_URL")
llm_api_key = os.getenv("LLM_API_KEY")
assert llm_base_url and llm_api_key, "LLM_BASE_URL and LLM_API_KEY required"

# Set ANTHROPIC_* vars so Claude Code routes through LiteLLM
os.environ["ANTHROPIC_BASE_URL"] = llm_base_url
os.environ["ANTHROPIC_API_KEY"] = llm_api_key

runtime_api_key = os.getenv("RUNTIME_API_KEY")
assert runtime_api_key, "RUNTIME_API_KEY required"

# AGENT_SERVER_SHA is set by CI to the PR head commit SHA
# This avoids conflict with GitHub's default GITHUB_SHA (merge commit)
server_image_sha = os.getenv("AGENT_SERVER_SHA") or "main"
server_image = f"ghcr.io/openhands/agent-server:{server_image_sha[:7]}-python-amd64"
logger.info(f"Using server image: {server_image}")

with APIRemoteWorkspace(
    runtime_api_url=os.getenv("RUNTIME_API_URL", "https://runtime.eval.all-hands.dev"),
    runtime_api_key=runtime_api_key,
    server_image=server_image,
    image_pull_policy="Always",
    target_type="binary",  # CI builds binary target images
    forward_env=["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"],
) as workspace:
    agent = ACPAgent(
        acp_command=["claude-agent-acp"],  # Pre-installed in Docker image
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

        # Report cost
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost:.4f}")
    finally:
        conversation.close()
