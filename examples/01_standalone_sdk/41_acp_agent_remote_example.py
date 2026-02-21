"""Example: ACPAgent with TCP transport and APIRemoteWorkspace.

This example demonstrates the real-world use case for ACPAgent's TCP transport
mode: running inside a remote K8s pod (via APIRemoteWorkspace) that doesn't have
Node.js installed, and connecting to an ACP server running as a separate service
on the network.

Architecture::

    ┌─────────────────────┐       ┌──────────────────────┐
    │  Local Machine       │       │  K8s Cluster          │
    │                      │       │                       │
    │  APIRemoteWorkspace ─┼──────>│  Runtime Pod          │
    │  provisions pod     │       │  (agent-server image) │
    │                      │       │                       │
    │  Conversation        │       │  ACPAgent runs here   │
    │  (RemoteConversation)│       │  connects via TCP ────┼──> ACP Server
    └─────────────────────┘       │                       │    (separate pod
                                   └──────────────────────┘     or sidecar)

The ACPAgent is serialized and sent to the remote pod. Inside the pod, it
connects to the ACP server over TCP instead of spawning it as a subprocess
(since the pod image lacks Node.js / npx).

Prerequisites:
    - RUNTIME_API_KEY: API key for the OpenHands Runtime API
    - ACP_HOST: Hostname of the ACP server (e.g. "acp-server.default.svc.cluster.local")
    - ACP_PORT: Port of the ACP server (default: 4001)

Optional:
    - RUNTIME_API_URL: Runtime API URL (default: https://runtime.eval.all-hands.dev)
    - SERVER_IMAGE: Custom agent-server image (default: main branch image)

Usage:
    RUNTIME_API_KEY=... ACP_HOST=acp.internal ACP_PORT=4001 \\
        uv run python examples/01_standalone_sdk/41_acp_agent_remote_example.py
"""

import os
import sys
import time

from openhands.sdk import Conversation, RemoteConversation, get_logger
from openhands.sdk.agent import ACPAgent
from openhands.workspace import APIRemoteWorkspace


logger = get_logger(__name__)


def main() -> None:
    # ── Validate required environment variables ──────────────────────────
    runtime_api_key = os.getenv("RUNTIME_API_KEY")
    if not runtime_api_key:
        logger.error("RUNTIME_API_KEY is required to provision the remote runtime")
        sys.exit(1)

    acp_host = os.getenv("ACP_HOST")
    if not acp_host:
        logger.error(
            "ACP_HOST is required (hostname of the running ACP server, "
            "e.g. 'acp-server.default.svc.cluster.local')"
        )
        sys.exit(1)

    acp_port = int(os.getenv("ACP_PORT", "4001"))

    # ── Create the ACPAgent with TCP transport ───────────────────────────
    # This agent will be serialized and sent to the remote pod.
    # Inside the pod, it connects to the ACP server over the network.
    agent = ACPAgent(
        acp_host=acp_host,
        acp_port=acp_port,
    )
    logger.info(f"ACPAgent configured for TCP transport: {acp_host}:{acp_port}")

    # ── Provision a remote K8s pod via Runtime API ───────────────────────
    server_image_sha = os.getenv("GITHUB_SHA") or "main"
    server_image = os.getenv(
        "SERVER_IMAGE",
        f"ghcr.io/openhands/agent-server:{server_image_sha[:7]}-python-amd64",
    )
    logger.info(f"Using server image: {server_image}")

    runtime_api_url = os.getenv("RUNTIME_API_URL", "https://runtime.eval.all-hands.dev")

    with APIRemoteWorkspace(
        runtime_api_url=runtime_api_url,
        runtime_api_key=runtime_api_key,
        server_image=server_image,
        image_pull_policy="Always",
    ) as workspace:
        # Verify the remote runtime is alive
        result = workspace.execute_command("echo 'Runtime is alive' && uname -a")
        logger.info(f"Remote runtime: exit={result.exit_code}, stdout={result.stdout}")

        # ── Create a RemoteConversation ──────────────────────────────────
        # The Conversation factory detects the RemoteWorkspace and returns
        # a RemoteConversation.  The ACPAgent is serialized to JSON and
        # sent to the remote agent server running in the K8s pod.
        received_events: list = []
        last_event_time = {"ts": time.time()}

        def on_event(event) -> None:
            received_events.append(event)
            last_event_time["ts"] = time.time()
            logger.info(f"Event: {type(event).__name__}")

        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            callbacks=[on_event],
        )
        assert isinstance(conversation, RemoteConversation), (
            f"Expected RemoteConversation, got {type(conversation).__name__}"
        )

        try:
            # ── First turn: ask the ACP-powered agent to explore ─────────
            conversation.send_message(
                "List the files in the current directory and write a short "
                "summary of what you see into SUMMARY.md."
            )
            conversation.run()

            # Wait for any trailing events
            while time.time() - last_event_time["ts"] < 2.0:
                time.sleep(0.1)

            # ── Second turn: use ask_agent for a side-question ───────────
            print("\n--- ask_agent (stateless side-question) ---")
            response = conversation.ask_agent(
                "Based on what you just saw, what is the most interesting file?"
            )
            print(f"ask_agent response: {response}")

            # ── Print cost metrics ───────────────────────────────────────
            cost = (
                conversation.conversation_stats.get_combined_metrics().accumulated_cost
            )
            print(f"\nTotal cost: ${cost:.4f}")
            print(f"Events received: {len(received_events)}")

        finally:
            conversation.close()

    print("\nDone!")


if __name__ == "__main__":
    main()
