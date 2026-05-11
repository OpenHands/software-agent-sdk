"""Switch LLM profiles with the built-in switch_llm tool.

This example creates two temporary LLM profiles, starts the conversation on a
GPT profile, asks the agent to call the switch_llm tool, and then verifies that
future model calls use the Claude profile.

Usage:
    LLM_API_KEY=... LLM_BASE_URL=https://llm-proxy.app.all-hands.dev \
        uv run python examples/01_standalone_sdk/49_switch_llm_tool.py
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LocalConversation
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


GPT_PROFILE = "example-gpt55"
CLAUDE_PROFILE = "example-claude"
DEFAULT_BASE_URL = "https://llm-proxy.app.all-hands.dev"
GPT_MODEL = "openai/gpt-5.5"
CLAUDE_MODEL = "openai/prod/claude-sonnet-4-5-20250929"

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)

store = LLMProfileStore()
store.save(
    GPT_PROFILE,
    LLM(
        model=GPT_MODEL,
        api_key=SecretStr(api_key),
        base_url=base_url,
        usage_id="gpt55",
    ),
    include_secrets=True,
)
store.save(
    CLAUDE_PROFILE,
    LLM(
        model=CLAUDE_MODEL,
        api_key=SecretStr(api_key),
        base_url=base_url,
        usage_id="claude",
    ),
    include_secrets=True,
)

try:
    initial_llm = store.load(GPT_PROFILE)
    agent = Agent(
        llm=initial_llm,
        tools=[],
        include_default_tools=["FinishTool", "SwitchLLMTool"],
    )
    conversation = LocalConversation(agent=agent, workspace=os.getcwd())

    print(f"Starting model: {conversation.agent.llm.model}")
    conversation.send_message(
        f"Call the switch_llm tool now with profile_name={CLAUDE_PROFILE!r}. "
        "After the tool succeeds, answer in one short sentence naming the "
        "active model value from the tool observation exactly."
    )
    conversation.run()

    active_model = conversation.agent.llm.model
    print(f"Active model after tool switch: {active_model}")
    assert active_model == CLAUDE_MODEL

    for usage_id, metrics in conversation.state.stats.usage_to_metrics.items():
        print(f"  [{usage_id}] cost=${metrics.accumulated_cost:.6f}")

    combined = conversation.state.stats.get_combined_metrics()
    print(f"Total cost: ${combined.accumulated_cost:.6f}")
    print(f"EXAMPLE_COST: {combined.accumulated_cost}")
finally:
    store.delete(GPT_PROFILE)
    store.delete(CLAUDE_PROFILE)
