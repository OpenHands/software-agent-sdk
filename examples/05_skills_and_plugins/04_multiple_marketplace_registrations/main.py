"""Example: Multiple Marketplace Registrations

Register multiple marketplaces and load plugins on-demand.

- auto_load="all": Load all plugins at conversation start  
- auto_load=None: Register but don't auto-load (use conversation.load_plugin())
"""

import json
import os
from pathlib import Path

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.plugin import MarketplaceRegistration

SCRIPT_DIR = Path(__file__).parent


def create_example_marketplace() -> Path:
    """Create a simple marketplace with a plugin for this demo."""
    marketplace_dir = SCRIPT_DIR / "demo_marketplace"
    plugin_dir = marketplace_dir / "plugins" / "greeter"
    
    # Create marketplace manifest
    (marketplace_dir / ".plugin").mkdir(parents=True, exist_ok=True)
    (marketplace_dir / ".plugin" / "marketplace.json").write_text(json.dumps({
        "name": "demo-marketplace",
        "owner": {"name": "Demo"},
        "plugins": [{"name": "greeter", "source": "./plugins/greeter"}],
        "skills": [],
    }))
    
    # Create plugin with a skill
    (plugin_dir / ".plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".plugin" / "plugin.json").write_text(json.dumps({
        "name": "greeter",
        "version": "1.0.0",
        "description": "A greeting plugin",
    }))
    
    (plugin_dir / "skills").mkdir(exist_ok=True)
    (plugin_dir / "skills" / "SKILL.md").write_text("""---
name: greeter-skill
description: Generates friendly greetings
---
# Greeter Skill
When asked to greet someone, respond with a warm, friendly greeting.
""")
    
    return marketplace_dir


def main():
    marketplace_dir = create_example_marketplace()
    
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
                # auto_load=None - we'll load explicitly
            ),
        ],
    )

    agent = Agent(llm=llm, tools=[], agent_context=agent_context)
    conversation = Conversation(agent=agent, workspace=os.getcwd())

    # Load the plugin on-demand
    conversation.load_plugin("greeter@demo")
    print(f"Loaded: {conversation.resolved_plugins[0].source}")

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
