"""Example: Multiple Marketplace Registrations

Register multiple marketplaces and use their skills in a conversation.

- auto_load="all": Load all skills at conversation start
- auto_load=None: Register but don't auto-load (available for explicit use)
"""

import os
from pathlib import Path

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool
from openhands.sdk.plugin import MarketplaceRegistration
from openhands.tools.terminal import TerminalTool

# Reuse the existing example marketplace
EXAMPLE_MARKETPLACE = (
    Path(__file__).parent.parent.parent
    / "01_standalone_sdk"
    / "43_mixed_marketplace_skills"
)


def main():
    llm = LLM(
        model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
    )

    # Register multiple marketplaces with different auto-load settings
    agent_context = AgentContext(
        registered_marketplaces=[
            MarketplaceRegistration(
                name="company",
                source=str(EXAMPLE_MARKETPLACE),
                auto_load="all",  # Load skills at conversation start
            ),
            MarketplaceRegistration(
                name="experimental",
                source=str(EXAMPLE_MARKETPLACE),
                # auto_load=None (default) - registered but not auto-loaded
            ),
        ],
    )

    agent = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name)],
        agent_context=agent_context,
    )

    conversation = Conversation(agent=agent, workspace=os.getcwd())

    # The "greeting-helper" skill from the marketplace should be available
    conversation.send_message("Use the greeting helper skill to greet me!")
    conversation.run()

    print(f"\nEXAMPLE_COST: {llm.metrics.accumulated_cost:.4f}")


if __name__ == "__main__":
    if not os.getenv("LLM_API_KEY"):
        print("Set LLM_API_KEY to run this example")
        print("EXAMPLE_COST: 0")
    else:
        main()
