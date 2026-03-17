"""Example: Inherit SaaS credentials via OpenHandsCloudWorkspace.

This example shows the simplified flow where your OpenHands Cloud account's
LLM configuration and secrets are inherited automatically — no need to
provide LLM_API_KEY separately.

Compared to 07_convo_with_cloud_workspace.py (which requires a separate
LLM_API_KEY), this approach uses:
  - workspace.get_llm()     → fetches LLM config from your SaaS account
  - workspace.get_secrets()  → builds lazy LookupSecret references for your secrets

Raw secret values never transit through the SDK client. The agent-server
inside the sandbox resolves them on demand.

Usage:
  uv run examples/02_remote_agent_server/10_cloud_workspace_share_credentials.py

Requirements:
  - OPENHANDS_CLOUD_API_KEY: API key for OpenHands Cloud (the only credential needed)

Optional:
  - OPENHANDS_CLOUD_API_URL: Override the Cloud API URL (default: https://app.all-hands.dev)
  - LLM_MODEL: Override the model from your SaaS settings
"""

import os
import time

from openhands.sdk import (
    Conversation,
    RemoteConversation,
    get_logger,
)
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import OpenHandsCloudWorkspace


logger = get_logger(__name__)


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
    # --- LLM from SaaS account settings ---
    # get_llm() calls GET /users/me?expose_secrets=true
    # (dual auth: Bearer + session key) and returns a
    # fully configured LLM instance.
    # Override any parameter: workspace.get_llm(model="gpt-4o")
    llm_kwargs = {}
    if os.getenv("LLM_MODEL"):
        llm_kwargs["model"] = os.getenv("LLM_MODEL")
    llm = workspace.get_llm(**llm_kwargs)
    logger.info(f"LLM configured: model={llm.model}")

    # --- Secrets from SaaS account ---
    # get_secrets() fetches secret *names* (not values) and builds LookupSecret
    # references. Values are resolved lazily inside the sandbox.
    secrets = workspace.get_secrets()
    logger.info(f"Available secrets: {list(secrets.keys())}")

    # Build agent and conversation
    agent = get_default_agent(llm=llm, cli_mode=True)
    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        received_events.append(event)
        last_event_time["ts"] = time.time()

    conversation = Conversation(
        agent=agent, workspace=workspace, callbacks=[event_callback]
    )
    assert isinstance(conversation, RemoteConversation)

    # Inject SaaS secrets into the conversation
    if secrets:
        conversation.update_secrets(secrets)
        logger.info(f"Injected {len(secrets)} secrets into conversation")

    # Build a prompt that exercises the injected secrets by asking the agent to
    # print the last 50% of each token — proves values resolved without leaking
    # full secrets in logs.
    secret_names = list(secrets.keys()) if secrets else []
    if secret_names:
        names_str = ", ".join(f"${name}" for name in secret_names)
        prompt = (
            f"For each of these environment variables: {names_str} — "
            "print the variable name and the LAST 50% of its value "
            "(i.e. the second half of the string). "
            "Then write a short summary into SECRETS_CHECK.txt."
        )
    else:
        prompt = (
            "List the environment variables that start with "
            "SECRET_ or end with _TOKEN, then write a short "
            "summary of what you find into SECRETS_CHECK.txt."
        )

    try:
        conversation.send_message(prompt)
        conversation.run()

        while time.time() - last_event_time["ts"] < 2.0:
            time.sleep(0.1)

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        conversation.close()

    logger.info("✅ Conversation completed successfully.")
    logger.info(f"Total {len(received_events)} events received during conversation.")
