"""Example demonstrating Tom agent with Theory of Mind capabilities.

This example shows how to set up an agent with Tom tools for getting
personalized guidance based on user modeling. Tom tools include:
- TomConsultTool: Get guidance for vague or unclear tasks
- SleeptimeComputeTool: Index conversations for user modeling
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.tool import Tool
from openhands.tools.preset.default import get_default_tools
from openhands.tools.tom_consult import (
    SleeptimeComputeAction,
    SleeptimeComputeObservation,
    SleeptimeComputeTool,
    TomConsultTool,
)


# Configure LLM
api_key: str | None = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm: LLM = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
    usage_id="agent",
    drop_params=True,
)

# Build tools list with Tom tools
# Note: Tom tools are automatically registered on import (PR #862)
tools = get_default_tools(enable_browser=False)

# Configure Tom tools with parameters
tom_params: dict[str, bool | str] = {
    "enable_rag": True,  # Enable RAG in Tom agent
}

# Add LLM configuration for Tom tools (uses same LLM as main agent)
tom_params["llm_model"] = llm.model
if llm.api_key:
    if isinstance(llm.api_key, SecretStr):
        tom_params["api_key"] = llm.api_key.get_secret_value()
    else:
        tom_params["api_key"] = llm.api_key
if llm.base_url:
    tom_params["api_base"] = llm.base_url

# Add both Tom tools to the agent
tools.append(Tool(name=TomConsultTool.name, params=tom_params))
tools.append(Tool(name=SleeptimeComputeTool.name, params=tom_params))

# Create agent with Tom capabilities
# This agent can consult Tom for personalized guidance
# Note: Tom's user modeling data will be stored in ~/.openhands/
agent: Agent = Agent(llm=llm, tools=tools)

# Start conversation
cwd: str = os.getcwd()
PERSISTENCE_DIR = os.path.expanduser("~/.openhands")
CONVERSATIONS_DIR = os.path.join(PERSISTENCE_DIR, "conversations")
conversation = Conversation(
    agent=agent, workspace=cwd, persistence_dir=CONVERSATIONS_DIR
)

# Optionally run sleeptime compute to index existing conversations
# This builds user preferences and patterns from conversation history
# Using execute_tool allows running tools before conversation.run()
print("\nRunning sleeptime compute to index conversations...")
try:
    sleeptime_result = conversation.execute_tool(
        "sleeptime_compute", SleeptimeComputeAction()
    )
    # Cast to the expected observation type for type-safe access
    if isinstance(sleeptime_result, SleeptimeComputeObservation):
        print(f"Result: {sleeptime_result.message}")
        print(f"Sessions processed: {sleeptime_result.sessions_processed}")
    else:
        print(f"Result: {sleeptime_result.text}")
except KeyError as e:
    print(f"Tool not available: {e}")

# Send a potentially vague message where Tom consultation might help
conversation.send_message(
    "I need to debug some code but I'm not sure where to start. "
    + "Can you help me figure out the best approach?"
)
conversation.run()

print("\n" + "=" * 80)
print("Tom agent consultation example completed!")
print("=" * 80)

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")


# Optional: Index this conversation for Tom's user modeling
# This builds user preferences and patterns from conversation history
# Uncomment the lines below to index the conversation:
#
# conversation.send_message("Please index this conversation using sleeptime_compute")
# conversation.run()
# print("\nConversation indexed for user modeling!")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
