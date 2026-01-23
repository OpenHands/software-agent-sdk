"""Example demonstrating DeepSeek's reasoning content feature with tool calls.

DeepSeek's thinking mode (deepseek-reasoner) requires the reasoning_content field
to be sent back in assistant messages during tool call turns. This example shows
how the SDK handles this automatically.

For more details, see: https://api-docs.deepseek.com/guides/thinking_mode#tool-calls
"""

import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

# Configure LLM for DeepSeek with reasoning mode
api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
assert api_key is not None, "LLM_API_KEY or DEEPSEEK_API_KEY environment variable is not set."

# Use deepseek-reasoner model for thinking mode
# The SDK automatically handles sending reasoning_content back to the API
model = os.getenv("LLM_MODEL", "deepseek/deepseek-reasoner")
base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")

llm = LLM(
    usage_id="deepseek-reasoning-demo",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

# Setup agent with terminal tool
agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])


# Callback to display reasoning content
def show_reasoning(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        message = event.to_llm_message()
        if hasattr(message, "reasoning_content") and message.reasoning_content:
            print(f"\n🧠 Reasoning content:")
            # Truncate long reasoning content for display
            reasoning = message.reasoning_content
            if len(reasoning) > 500:
                reasoning = reasoning[:500] + "..."
            print(f"  {reasoning}")


conversation = Conversation(
    agent=agent, callbacks=[show_reasoning], workspace=os.getcwd()
)

# Send a task that requires reasoning and tool use
# DeepSeek will think through the problem and use tools to solve it
conversation.send_message(
    "What is the current date? Use the terminal to find out, "
    "then calculate how many days until the end of the year."
)
conversation.run()

print("\n✅ Done!")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
