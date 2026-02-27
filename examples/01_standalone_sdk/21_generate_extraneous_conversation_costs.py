import os

from pydantic import SecretStr
from tabulate import tabulate

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    LLMSummarizingCondenser,
    Message,
    TextContent,
    get_logger,
)
from openhands.sdk.tool.spec import Tool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

# Configure LLM using LLMRegistry
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")

# Create LLM instance
llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

llm_condenser = LLM(
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
    usage_id="condenser",
)

# Tools
condenser = LLMSummarizingCondenser(llm=llm_condenser, max_size=10, keep_first=2)

cwd = os.getcwd()
agent = Agent(
    llm=llm,
    tools=[
        Tool(
            name=TerminalTool.name,
        ),
    ],
    condenser=condenser,
)

conversation = Conversation(agent=agent, workspace=cwd)
conversation.send_message(
    message=Message(
        role="user",
        content=[TextContent(text="Please echo 'Hello!'")],
    )
)
conversation.run()

# Demonstrate extraneous costs part of the conversation
second_llm = LLM(
    usage_id="demo-secondary",
    model=model,
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)
conversation.llm_registry.add(second_llm)
second_llm.completion(
    messages=[Message(role="user", content=[TextContent(text="echo 'More spend!'")])]
)

# Access total spend
spend = conversation.conversation_stats.get_combined_metrics()
print("\n=== Total Spend for Conversation ===\n")
print(f"Accumulated Cost: ${spend.accumulated_cost:.6f}")
if spend.accumulated_token_usage:
    print(f"Prompt Tokens: {spend.accumulated_token_usage.prompt_tokens}")
    print(f"Completion Tokens: {spend.accumulated_token_usage.completion_tokens}")
    print(f"Cache Read Tokens: {spend.accumulated_token_usage.cache_read_tokens}")
    print(f"Cache Write Tokens: {spend.accumulated_token_usage.cache_write_tokens}")

spend_per_usage = conversation.conversation_stats.usage_to_metrics
print("\n=== Spend Breakdown by Usage ID ===\n")
rows = []
for usage_id, metrics in spend_per_usage.items():
    rows.append(
        [
            usage_id,
            f"${metrics.accumulated_cost:.6f}",
            metrics.accumulated_token_usage.prompt_tokens
            if metrics.accumulated_token_usage
            else 0,
            metrics.accumulated_token_usage.completion_tokens
            if metrics.accumulated_token_usage
            else 0,
        ]
    )

print(
    tabulate(
        rows,
        headers=["Usage ID", "Cost", "Prompt Tokens", "Completion Tokens"],
        tablefmt="github",
    )
)

# Report cost
cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"EXAMPLE_COST: {cost}")
