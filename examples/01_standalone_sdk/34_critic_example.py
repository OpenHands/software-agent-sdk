"""Example demonstrating critic-based evaluation of agent actions.

This shows how to configure an agent with a critic to evaluate action quality
in real-time. The critic scores are displayed in the conversation visualizer.
"""

import os
import sys

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.critic import APIBasedCritic
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    sys.exit(
        f"Missing required environment variable: {name}. "
        f"Set {name} before running this example."
    )


llm_api_key = get_required_env("LLM_API_KEY")

llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=llm_api_key,
    base_url=os.getenv("LLM_BASE_URL", None),
)

critic_kwargs = {}
if server_url := os.getenv("CRITIC_SERVER_URL"):
    critic_kwargs["server_url"] = server_url
if model_name := os.getenv("CRITIC_MODEL_NAME"):
    critic_kwargs["model_name"] = model_name

critic = APIBasedCritic(
    server_url=get_required_env("CRITIC_SERVER_URL"),
    api_key=get_required_env("CRITIC_API_KEY"),
    model_name=get_required_env("CRITIC_MODEL_NAME"),
    # finish_and_message: evaluate on FinishAction and agent MessageEvent (default)
    mode="finish_and_message",
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
