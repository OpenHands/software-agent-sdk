# Multiple Marketplace Registrations

Register multiple marketplaces and use their skills in a conversation.

## Usage

```bash
export LLM_API_KEY=your-api-key
python main.py
```

## Key Concepts

```python
agent_context = AgentContext(
    registered_marketplaces=[
        MarketplaceRegistration(
            name="company",
            source="github:company/plugins",
            auto_load="all",  # Load skills at conversation start
        ),
        MarketplaceRegistration(
            name="experimental",
            source="github:company/experimental",
            # auto_load=None - registered but not auto-loaded
        ),
    ],
)

agent = Agent(llm=llm, tools=tools, agent_context=agent_context)
conversation = Conversation(agent=agent, workspace=workspace)
```

## Related

- [43_mixed_marketplace_skills](../../01_standalone_sdk/43_mixed_marketplace_skills/) - Example marketplace used here
