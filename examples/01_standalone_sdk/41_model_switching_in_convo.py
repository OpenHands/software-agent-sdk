"""Interactive chat with mid-conversation model switching.

Usage:
    uv run examples/01_standalone_sdk/41_model_switching_in_convo.py
"""

import os

from openhands.sdk import LLM, Agent, LocalConversation, Tool
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.tools.terminal import TerminalTool


LLM_API_KEY = os.getenv("LLM_API_KEY")
store = LLMProfileStore()

profiles: dict[str, str] = {
    "kimi": "openhands/kimi-k2-0711-preview",
    "deepseek": "openhands/deepseek-chat",
    "gpt": "openhands/gpt-5.2",
}
for profile_name, model in profiles.items():
    store.save(
        profile_name,
        LLM(model=model, api_key=LLM_API_KEY),
        include_secrets=True,
    )

llm = LLM(
    model=os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929"),
    api_key=LLM_API_KEY,
)

agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])

conversation = LocalConversation(
    agent=agent,
    workspace=os.getcwd(),
    allow_model_switching=True,
)

print(
    "Chat with the agent. Commands:\n"
    "  /model                                   — show current model and available profiles\n"  # noqa: E501
    "  /model <model_profile_name>              — switch to a different model profile\n"
    "  /model <model_profile_name> [prompt]     — switch and send a message in one step\n"  # noqa: E501
    "  /exit                                    — quit\n"
)

try:
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() == "/exit":
            break
        conversation.send_message(user_input)
        conversation.run()
except Exception:
    raise
finally:
    # Clean up the profiles we created
    for name in profiles.keys():
        store.delete(name)

# Inspect metrics across all LLMs (original + switched profiles)
stats = conversation.state.stats
for usage_id, metrics in stats.usage_to_metrics.items():
    print(f"  [{usage_id}] cost=${metrics.accumulated_cost:.6f}")
    for usage in metrics.token_usages:
        print(
            f"    model={usage.model}"
            f"  prompt={usage.prompt_tokens}"
            f"  completion={usage.completion_tokens}"
        )

combined = stats.get_combined_metrics()
print(f"\nTotal cost (all models): ${combined.accumulated_cost:.6f}")
print(f"EXAMPLE_COST: {combined.accumulated_cost}")
