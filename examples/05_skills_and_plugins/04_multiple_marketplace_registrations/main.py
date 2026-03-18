"""Example: Multiple Marketplace Registrations

Register multiple marketplaces and load plugins on-demand.

- auto_load="all": Load all plugins at conversation start  
- auto_load=None: Register but don't auto-load (use conversation.load_plugin())
"""

from pathlib import Path

from openhands.sdk import AgentContext
from openhands.sdk.plugin import MarketplaceRegistration, MarketplaceRegistry

# Reuse the existing example marketplace (has skills, not plugins)
EXAMPLE_MARKETPLACE = (
    Path(__file__).parent.parent.parent
    / "01_standalone_sdk"
    / "43_mixed_marketplace_skills"
)


def main():
    # Register multiple marketplaces with different auto-load settings
    agent_context = AgentContext(
        registered_marketplaces=[
            MarketplaceRegistration(
                name="company",
                source=str(EXAMPLE_MARKETPLACE),
                auto_load="all",  # Load all plugins at conversation start
            ),
            MarketplaceRegistration(
                name="experimental",
                source=str(EXAMPLE_MARKETPLACE),
                # auto_load=None (default) - registered but not auto-loaded
            ),
        ],
    )

    print("Configured AgentContext with registered_marketplaces:")
    for reg in agent_context.registered_marketplaces:
        status = "auto-load" if reg.auto_load == "all" else "on-demand"
        print(f"  {reg.name}: {status}")

    # The registry can be used to inspect/resolve plugins before conversation
    registry = MarketplaceRegistry(agent_context.registered_marketplaces)
    
    # List available skills from the marketplace
    marketplace, _ = registry.get_marketplace("company")
    print(f"\nSkills in '{marketplace.name}':")
    for skill in marketplace.skills:
        print(f"  - {skill.name}: {skill.description}")

    # Example usage with Conversation (requires LLM_API_KEY):
    print("""
To use in a conversation:

    from openhands.sdk import LLM, Agent, Conversation
    
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)
    conversation = Conversation(agent=agent, workspace=workspace)
    
    # Load a plugin on-demand from registered marketplace
    conversation.load_plugin("plugin-name@experimental")
    
    # Use the loaded plugin's skills
    conversation.send_message("...")
    conversation.run()
""")


if __name__ == "__main__":
    main()
    print("EXAMPLE_COST: 0")
