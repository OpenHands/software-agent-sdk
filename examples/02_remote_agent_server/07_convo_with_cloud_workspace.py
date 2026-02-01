"""Example: OpenHandsCloudWorkspace for OpenHands Cloud API.

This example demonstrates using OpenHandsCloudWorkspace to provision a sandbox
via OpenHands Cloud (app.all-hands.dev) and run an agent conversation.

Usage:
  uv run examples/02_remote_agent_server/06_convo_with_cloud_workspace.py

Requirements:
  - LLM_API_KEY: API key for direct LLM provider access (e.g., Anthropic API key)
  - OPENHANDS_CLOUD_API_KEY: API key for OpenHands Cloud access

Note:
  The LLM configuration is sent to the cloud sandbox, so you need an API key
  that works directly with the LLM provider (not a local proxy). If using
  Anthropic, set LLM_API_KEY to your Anthropic API key.
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
from openhands.workspace import OpenHandsCloudWorkspace


logger = get_logger(__name__)


api_key = os.getenv("LLM_API_KEY")
assert api_key, "LLM_API_KEY required"

# Note: Don't use a local proxy URL here - the cloud sandbox needs direct access
# to the LLM provider. Use None for base_url to let LiteLLM use the default
# provider endpoint, or specify the provider's direct URL.
llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL") or None,
    api_key=SecretStr(api_key),
)

cloud_api_key = os.getenv("OPENHANDS_CLOUD_API_KEY")
if not cloud_api_key:
    logger.error("OPENHANDS_CLOUD_API_KEY required")
    exit(1)

cloud_api_url = os.getenv("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev")
logger.info(f"Using OpenHands Cloud API: {cloud_api_url}")

with OpenHandsCloudWorkspace(
    cloud_api_url=cloud_api_url,
    cloud_api_key=cloud_api_key,
) as workspace:
    agent = get_default_agent(llm=llm, cli_mode=True)
    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        received_events.append(event)
        last_event_time["ts"] = time.time()

    result = workspace.execute_command(
        "echo 'Hello from OpenHands Cloud sandbox!' && pwd"
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
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        conversation.close()

    logger.info("✅ Conversation completed successfully.")
    logger.info(f"Total {len(received_events)} events received during conversation.")
