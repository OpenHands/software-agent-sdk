"""Example demonstrating Tom agent with Theory of Mind capabilities.

This example shows how to use the Tom agent preset which includes
a TomConsultTool for getting personalized guidance based on user modeling.
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.tools.preset import get_tom_agent
from openhands.tools.tom_consult.action import SleeptimeComputeAction

# Configure LLM
api_key: str | None = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm: LLM = LLM(
    model="openhands/claude-sonnet-4-5-20250929",
    api_key=SecretStr(api_key),
    usage_id="agent",
    drop_params=True,
)

# Create Tom agent with Theory of Mind capabilities
# This agent can consult Tom for personalized guidance
# Note: Tom's user modeling data will be stored in workspace/.openhands/
agent: Agent = get_tom_agent(
    llm=llm,
    cli_mode=True,  # Disable browser tools for CLI
    enable_rag=True,  # Enable RAG in Tom agent
)

# Start conversation
cwd: str = os.getcwd()
PERSISTENCE_DIR = os.path.expanduser("~/.openhands")
CONVERSATIONS_DIR = os.path.join(PERSISTENCE_DIR, "conversations")
conversation = Conversation(
    agent=agent, workspace=cwd, persistence_dir=CONVERSATIONS_DIR
)

# Sleep time compute
sleeptime_compute_tool = conversation.agent.tools_map["sleeptime_compute"]
assert sleeptime_compute_tool is not None
sleeptime_result = sleeptime_compute_tool.executor(SleeptimeComputeAction())


# Display the result of sleeptime_compute
print("\nSleeptime Compute Result:")
print(sleeptime_compute_tool.executor.list_user_models())

# Send a potentially vague message where Tom consultation might help
conversation.send_message(
    "I need to debug some code but I'm not sure where to start. "
    + "Can you help me figure out the best approach?"
)
conversation.run()

print("\n" + "=" * 80)
print("Tom agent consultation example completed!")
print("=" * 80)


# Optional: Index this conversation for Tom's user modeling
# This builds user preferences and patterns from conversation history
# Uncomment the lines below to index the conversation:
#
# conversation.send_message("Please index this conversation using sleeptime_compute")
# conversation.run()
# print("\nConversation indexed for user modeling!")
