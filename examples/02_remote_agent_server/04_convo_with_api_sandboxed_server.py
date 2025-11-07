"""Example: APIRemoteWorkspace with Dynamic Build.

This example demonstrates building an agent-server image on-the-fly from the SDK
codebase and launching it in a remote sandboxed environment via Runtime API.

Usage:
  uv run examples/24_remote_convo_with_api_sandboxed_server.py

Requirements:
  - LLM_API_KEY: API key for LLM access
  - RUNTIME_API_KEY: API key for runtime API access
"""

import os
import time

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Conversation,
    RemoteConversation,
    get_logger,
)
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import APIRemoteWorkspace


logger = get_logger(__name__)


api_key = os.getenv("LLM_API_KEY")
assert api_key, "LLM_API_KEY required"

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

runtime_api_key = os.getenv("RUNTIME_API_KEY")
if not runtime_api_key:
    logger.error("RUNTIME_API_KEY required")
    exit(1)

with APIRemoteWorkspace(
    runtime_api_url=os.getenv("RUNTIME_API_URL", "https://runtime.eval.all-hands.dev"),
    runtime_api_key=runtime_api_key,
    server_image="ghcr.io/openhands/agent-server:main-python",
) as workspace:
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
            "Read the current repo and write 3 facts about the project into FACTS.txt."
        )
        conversation.run()

        while time.time() - last_event_time["ts"] < 2.0:
            time.sleep(0.1)

        conversation.send_message("Great! Now delete that file.")
        conversation.run()
    finally:
        conversation.close()
