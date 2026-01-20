"""Example demonstrating critic-based evaluation of agent actions.

This is EXPERIMENTAL.

This shows how to configure an agent with a critic to evaluate action quality
in real-time. The critic scores are displayed in the conversation visualizer.

For All-Hands LLM proxy (llm-proxy.*.all-hands.dev), the critic is auto-configured
using the same base_url with /vllm suffix and "critic" as the model name.
"""

import os
import re

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.critic import APIBasedCritic
from openhands.sdk.critic.base import CriticBase
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(
        f"Missing required environment variable: {name}. "
        f"Set {name} before running this example."
    )


def get_default_critic(llm: LLM) -> CriticBase | None:
    """Auto-configure critic for All-Hands LLM proxy.

    When the LLM base_url matches `llm-proxy.*.all-hands.dev`, returns an
    APIBasedCritic configured with:
    - server_url: {base_url}/vllm
    - api_key: same as LLM
    - model_name: "critic"

    Returns None if base_url doesn't match or api_key is not set.
    """
    base_url = llm.base_url
    api_key = llm.api_key
    if base_url is None or api_key is None:
        return None

    # Match: llm-proxy.{env}.all-hands.dev (e.g., staging, prod, eval)
    pattern = r"^https?://llm-proxy\.[^./]+\.all-hands\.dev"
    if not re.match(pattern, base_url):
        return None

    return APIBasedCritic(
        server_url=f"{base_url.rstrip('/')}/vllm",
        api_key=api_key,
        model_name="critic",
    )


llm_api_key = get_required_env("LLM_API_KEY")

llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=llm_api_key,
    base_url=os.getenv("LLM_BASE_URL", None),
)

# Try auto-configuration for All-Hands proxy, fall back to explicit env vars
critic = get_default_critic(llm)
if critic is None:
    critic = APIBasedCritic(
        server_url=get_required_env("CRITIC_SERVER_URL"),
        api_key=get_required_env("CRITIC_API_KEY"),
        model_name=get_required_env("CRITIC_MODEL_NAME"),
    )


# Configure agent with critic
agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ],
    # Add critic to evaluate agent actions
    critic=critic,
)

cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)

conversation.send_message(
    "Create a file called GREETING.txt with a friendly greeting message."
)
conversation.run()

print("\nAll done! Check the output above for 'Critic Score' in the visualizer.")
