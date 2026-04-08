"""Example: Multiple Marketplace Registrations

Demonstrates two loading strategies for marketplace plugins:

- auto_load="all": Plugins loaded automatically at conversation start
- auto_load=None: Plugins loaded on-demand via conversation.load_plugin()

This example uses pre-created marketplaces in:
- ./auto_marketplace/ - auto-loaded at conversation start
- ./demo_marketplace/ - loaded on-demand
"""

import os
from pathlib import Path

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.plugin import MarketplaceRegistration


SCRIPT_DIR = Path(__file__).parent


def main():
    llm = LLM(
        model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
    )

    # Register two marketplaces with different loading strategies
    agent_context = AgentContext(
        registered_marketplaces=[
            # Auto-loaded: plugins available immediately when conversation starts
            MarketplaceRegistration(
                name="auto",
                source=str(SCRIPT_DIR / "auto_marketplace"),
                auto_load="all",
            ),
            # On-demand: registered but not loaded until explicitly requested
            MarketplaceRegistration(
                name="demo",
                source=str(SCRIPT_DIR / "demo_marketplace"),
                # auto_load=None (default) - use load_plugin() to load
            ),
        ],
    )

    agent = Agent(llm=llm, tools=[], agent_context=agent_context)
    conversation = Conversation(agent=agent, workspace=os.getcwd())

    # The "auto" marketplace plugins are already loaded
    # Now load an additional plugin on-demand from "demo" marketplace
    # Format: "plugin-name@marketplace-name" (same as Claude Code plugin syntax)
    conversation.load_plugin("greeter@demo")

    resolved = conversation.resolved_plugins
    if resolved:
        print(f"Loaded {len(resolved)} plugin(s):")
        for plugin in resolved:
            print(f"  - {plugin.source}")

    # Use skills from both plugins
    conversation.send_message("Give me a tip, then greet me!")
    conversation.run()

    print(f"\nEXAMPLE_COST: {llm.metrics.accumulated_cost:.4f}")


if __name__ == "__main__":
    if not os.getenv("LLM_API_KEY"):
        print("Set LLM_API_KEY to run this example")
        print("EXAMPLE_COST: 0")
    else:
        main()
