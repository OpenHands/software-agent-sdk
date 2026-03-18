# Multiple Marketplace Registrations

Register multiple marketplaces and load plugins on-demand.

## Usage

```bash
python main.py
```

## Key Concepts

```python
# Configure marketplaces in AgentContext
agent_context = AgentContext(
    registered_marketplaces=[
        MarketplaceRegistration(
            name="company",
            source="github:company/plugins",
            auto_load="all",  # Load all plugins at conversation start
        ),
        MarketplaceRegistration(
            name="experimental",
            source="github:company/experimental",
            # auto_load=None - registered but not auto-loaded
        ),
    ],
)

# Create agent and conversation
agent = Agent(llm=llm, tools=tools, agent_context=agent_context)
conversation = Conversation(agent=agent, workspace=workspace)

# Load a plugin on-demand from registered marketplace
conversation.load_plugin("beta-tool@experimental")
```

## Related

- [43_mixed_marketplace_skills](../../01_standalone_sdk/43_mixed_marketplace_skills/) - Example marketplace used here
