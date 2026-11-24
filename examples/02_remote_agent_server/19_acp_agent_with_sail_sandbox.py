"""Example: ACPAgent in a Sail sandbox.

This example runs an ACPAgent (Claude Code via the ACP protocol) on an agent
server inside a Sailbox, a hosted sandbox from Sail Research
(https://docs.sailresearch.com). It follows the same pattern as
18_convo_with_sail_sandboxed_server.py but uses ACPAgent instead of the default
LLM-based Agent.

Usage:
  uv run examples/02_remote_agent_server/19_acp_agent_with_sail_sandbox.py

Requirements:
  - LLM_BASE_URL: LiteLLM proxy URL (routes Claude Code requests)
  - ACP_LLM_API_KEY: LiteLLM virtual API key whose alias bypasses the proxy's
    OpenHands-identity guardrail (Claude Code sends its own system prompt,
    which the guardrail would 403). Falls back to LLM_API_KEY.
  - SAIL_API_KEY: API key from https://app.sailresearch.com
  - pip install sail
"""

import os
import time
import uuid

import sail

from openhands.sdk import (
    Conversation,
    RemoteConversation,
    Workspace,
    get_logger,
)
from openhands.sdk.agent import ACPAgent


logger = get_logger(__name__)


# ACP agents (Claude Code) route through the OpenHands LiteLLM proxy. Claude
# Code sends its own non-OpenHands system prompt, so the proxy's identity
# guardrail would 403 it; ACP_LLM_API_KEY is a dedicated key whose LiteLLM
# alias (`acp-llm-api-key`) is in the proxy's `ignored_key_aliases`.
llm_base_url = os.getenv("LLM_BASE_URL")
llm_api_key = os.getenv("ACP_LLM_API_KEY") or os.getenv("LLM_API_KEY")
assert llm_base_url and llm_api_key, (
    "LLM_BASE_URL and ACP_LLM_API_KEY (or LLM_API_KEY) required"
)
assert os.getenv("SAIL_API_KEY"), "SAIL_API_KEY required"

# SDK_SHA is the canonical commit SHA set by CI workflows (avoids the
# built-in GITHUB_SHA which resolves to the merge-commit on PRs).
server_image_sha = os.getenv("SDK_SHA") or os.getenv("GITHUB_SHA") or "main"
server_image = f"ghcr.io/openhands/agent-server:{server_image_sha[:7]}-python-amd64"
logger.info(f"Using server image: {server_image}")

# The sandbox URL is reachable from the internet, so the server gets a session
# key that every request must carry.
session_api_key = uuid.uuid4().hex

sailbox = sail.Sailbox.create(
    app=sail.App.find("openhands-sdk-examples", mint_if_missing=True),
    name=f"acp-agent-server-{uuid.uuid4().hex[:8]}",
    image=sail.Image.from_registry(server_image),
    ingress_ports=[8000],
    # Ends the sandbox even if this script is killed before it can terminate it.
    max_lifetime_seconds=1800,
)
try:
    # A Sailbox does not run the image's ENTRYPOINT, so start the server here.
    # Claude Code is started by the server and inherits the ANTHROPIC_* variables
    # that route it through LiteLLM.
    sailbox.exec(
        "/usr/local/bin/openhands-agent-server --host 0.0.0.0 --port 8000",
        background=True,
        env={
            "OH_SESSION_API_KEYS_0": session_api_key,
            "ANTHROPIC_BASE_URL": llm_base_url,
            "ANTHROPIC_API_KEY": llm_api_key,
        },
    )
    listener = sailbox.wait_for_listener(8000, timeout=120)
    assert isinstance(listener.endpoint, sail.HttpEndpoint)
    logger.info(f"Agent server is up at {listener.endpoint.url}")

    workspace = Workspace(
        host=listener.endpoint.url,
        api_key=session_api_key,
        working_dir="/workspace/project",
    )
    agent = ACPAgent(
        acp_command=["claude-agent-acp"],  # Pre-installed in the server image
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
finally:
    sailbox.terminate()
