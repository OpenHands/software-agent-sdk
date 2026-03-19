"""Example: Multiple Marketplace Registrations

Register multiple marketplaces and load plugins on-demand.

- auto_load="all": Load all plugins at conversation start
- auto_load=None: Register but don't auto-load (use conversation.load_plugin())

This example uses a pre-created marketplace in ./demo_marketplace/
"""

import os
from pathlib import Path

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.plugin import MarketplaceRegistration


SCRIPT_DIR = Path(__file__).parent


def main():
    # Use pre-created marketplace in this directory
    marketplace_dir = SCRIPT_DIR / "demo_marketplace"

    llm = LLM(
        model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
    )

    # Register marketplace (not auto-loaded)
    agent_context = AgentContext(
        registered_marketplaces=[
            MarketplaceRegistration(
                name="demo",
                source=str(marketplace_dir),
                # auto_load=None means we load explicitly with load_plugin()
            ),
        ],
    )

    agent = Agent(llm=llm, tools=[], agent_context=agent_context)
    conversation = Conversation(agent=agent, workspace=os.getcwd())

    # Load the plugin on-demand
    conversation.load_plugin("greeter@demo")
    resolved = conversation.resolved_plugins
    if resolved:
        print(f"Loaded: {resolved[0].source}")

    # Use the skill
    conversation.send_message("Please greet me!")
    conversation.run()

    print(f"\nEXAMPLE_COST: {llm.metrics.accumulated_cost:.4f}")


if __name__ == "__main__":
    if not os.getenv("LLM_API_KEY"):
        print("Set LLM_API_KEY to run this example")
        print("EXAMPLE_COST: 0")
    else:
        main()
