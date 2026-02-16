"""Example: Using FallbackStrategy for LLM resilience.

When the primary LLM fails with a transient error (rate limit, timeout, etc.),
FallbackStrategy automatically tries alternate LLMs in order.  Fallback is
per-call: each new request starts with the primary model.  Token usage and
cost from fallback calls are merged into the primary LLM's metrics.

This example:
  1. Saves two fallback LLM profiles to a temporary store.
  2. Configures a primary LLM with a FallbackStrategy pointing at those profiles.
  3. Runs a conversation — if the primary model is unavailable, the agent
     transparently falls back to the next available model.
"""

import os
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, LLMProfileStore, Tool
from openhands.sdk.llm import FallbackStrategy
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# Read configuration from environment
api_key = os.getenv("LLM_API_KEY")
api_key = "sk-CIFoH8V728O3bvN5bGnx0g"
assert api_key is not None, "LLM_API_KEY environment variable is not set."
base_url = os.getenv("LLM_BASE_URL")
primary_model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")

# Persist fallback profiles
profile_dir = Path("~/.openhands/profiles").mkdir(parents=True, exist_ok=True)
store = LLMProfileStore(base_dir=profile_dir)

fallback_1 = LLM(
    usage_id="fallback-1",
    model=os.getenv("LLM_FALLBACK_MODEL_1", "openai/gpt-4o"),
    api_key=SecretStr(os.getenv("LLM_FALLBACK_API_KEY_1", api_key)),
    base_url=os.getenv("LLM_FALLBACK_BASE_URL_1", base_url),
)
store.save("fallback-1", fallback_1, include_secrets=True)

fallback_2 = LLM(
    usage_id="fallback-2",
    model=os.getenv("LLM_FALLBACK_MODEL_2", "openai/gpt-4o-mini"),
    api_key=SecretStr(os.getenv("LLM_FALLBACK_API_KEY_2", api_key)),
    base_url=os.getenv("LLM_FALLBACK_BASE_URL_2", base_url),
)
store.save("fallback-2", fallback_2, include_secrets=True)

print(f"Saved fallback profiles: {store.list()}")


# Configure the primary LLM with a FallbackStrategy
primary_llm = LLM(
    usage_id="agent-primary",
    model=primary_model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    fallback_strategy=FallbackStrategy(
        fallback_llms=["fallback-1", "fallback-2"],
        profile_store_dir=profile_dir,
    ),
)


# Run a conversation
agent = Agent(
    llm=primary_llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ],
)

conversation = Conversation(agent=agent, workspace=os.getcwd())
conversation.send_message("Write a haiku about resilience into HAIKU.txt.")
conversation.run()


# Inspect metrics (includes any fallback usage)
metrics = primary_llm.metrics
print(f"Total cost (including fallbacks): ${metrics.accumulated_cost:.6f}")
print(f"Token usage records: {len(metrics.token_usages)}")
for usage in metrics.token_usages:
    print(
        f"  model={usage.model}"
        f"  prompt={usage.prompt_tokens}"
        f"  completion={usage.completion_tokens}"
    )

print(f"EXAMPLE_COST: {metrics.accumulated_cost}")
