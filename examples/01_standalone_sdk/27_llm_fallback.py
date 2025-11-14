"""
Example demonstrating LLM fallback functionality using FallbackRouter.

This example shows how to configure multiple language models with automatic
fallback capability. If the primary model fails (due to rate limits, timeouts,
or service unavailability), the system automatically falls back to secondary
models.

Use cases:
- High availability: Ensure your application continues working even if one
  provider has an outage
- Rate limit handling: Automatically switch to a backup model when you hit
  rate limits
- Cost optimization: Use expensive models as primary but have cheaper backups
"""

import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Message,
    TextContent,
    get_logger,
)
from openhands.sdk.llm.router import FallbackRouter
from openhands.tools.preset.default import get_default_tools


logger = get_logger(__name__)

# Configure API credentials
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
base_url = os.getenv("LLM_BASE_URL")

# Configure LLMs for fallback
# First model: A powerful but potentially rate-limited model
primary_llm = LLM(
    usage_id="primary",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

# Second model: A reliable alternative model
# In a real scenario, this might be a different provider or cheaper model
fallback_llm = LLM(
    usage_id="fallback",
    model="openhands/devstral-small-2507",
    base_url=base_url,
    api_key=SecretStr(api_key),
)

# Create FallbackRouter with a list of LLMs
# Models will be tried in the order they appear in the list
# Similar to how litellm handles fallbacks
fallback_router = FallbackRouter(
    usage_id="fallback-router",
    llms=[primary_llm, fallback_llm],
)

# Configure agent with fallback router
tools = get_default_tools()
agent = Agent(llm=fallback_router, tools=tools)

# Create conversation
conversation = Conversation(agent=agent, workspace=os.getcwd())

# Send a message - the router will automatically try models in order,
# falling back if one fails
conversation.send_message(
    message=Message(
        role="user",
        content=[
            TextContent(
                text=(
                    "Hello! Can you tell me what the current date is? "
                    "You can use the bash tool to run the 'date' command."
                )
            )
        ],
    )
)

# Run the conversation
conversation.run()

# Display results
print("=" * 100)
print("Conversation completed successfully!")
if fallback_router.active_llm:
    print(f"Active model used: {fallback_router.active_llm.model}")
else:
    print("No active model (no completions made)")

# Report costs
metrics = conversation.conversation_stats.get_combined_metrics()
print(f"Total cost: ${metrics.accumulated_cost:.4f}")
print(f"Total tokens: {metrics.accumulated_token_usage}")

print("\n" + "=" * 100)
print("Key features demonstrated:")
print("1. Automatic fallback when primary model fails")
print("2. Transparent switching between models")
print("3. Cost and usage tracking across all models")
print("4. Works seamlessly with agents and tools")
