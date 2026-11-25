"""Example: agent server in a Sail sandbox.

This example runs the agent-server image in a Sailbox, a hosted sandbox from
Sail Research (https://docs.sailresearch.com), and connects to it with a remote
Workspace. The sandbox publishes the server's port as an HTTPS URL, so the SDK
talks to it like to any other remote agent server.

Usage:
  uv run examples/02_remote_agent_server/18_convo_with_sail_sandboxed_server.py

Requirements:
  - LLM_API_KEY: API key for LLM access
  - SAIL_API_KEY: API key from https://app.sailresearch.com
  - pip install sail

The LLM is called from inside the sandbox (the agent runs on the agent server),
so the LLM settings, including the API key, are sent to it.
"""

import os
import time
import uuid

import sail
from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Conversation,
    RemoteConversation,
    Workspace,
    get_logger,
)
from openhands.tools.preset.default import get_default_agent


logger = get_logger(__name__)


api_key = os.getenv("LLM_API_KEY")
assert api_key, "LLM_API_KEY required"
assert os.getenv("SAIL_API_KEY"), "SAIL_API_KEY required"

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

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
    name=f"agent-server-{uuid.uuid4().hex[:8]}",
    image=sail.Image.from_registry(server_image),
    ingress_ports=[8000],
    # Ends the sandbox even if this script is killed before it can terminate it.
    max_lifetime_seconds=1800,
)
try:
    # A Sailbox does not run the image's ENTRYPOINT, so start the server here.
    sailbox.exec(
        "/usr/local/bin/openhands-agent-server --host 0.0.0.0 --port 8000",
        background=True,
        env={"OH_SESSION_API_KEYS_0": session_api_key},
    )
    listener = sailbox.wait_for_listener(8000, timeout=120)
    assert isinstance(listener.endpoint, sail.HttpEndpoint)
    logger.info(f"Agent server is up at {listener.endpoint.url}")

    workspace = Workspace(
        host=listener.endpoint.url,
        api_key=session_api_key,
        working_dir="/workspace/project",
    )
    agent = get_default_agent(llm=llm, cli_mode=True)
    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        received_events.append(event)
        last_event_time["ts"] = time.time()

    result = workspace.execute_command(
        "echo 'Hello from sandboxed environment!' && pwd"
    )
    logger.info(f"Command completed: {result.exit_code}, {result.stdout}")

    conversation = Conversation(
        agent=agent, workspace=workspace, callbacks=[event_callback]
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        conversation.send_message(
            "Write 3 facts about the operating system of this sandbox into FACTS.txt."
        )
        conversation.run()

        while time.time() - last_event_time["ts"] < 2.0:
            time.sleep(0.1)

        conversation.send_message("Great! Now delete that file.")
        conversation.run()
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        conversation.close()
finally:
    sailbox.terminate()
