# Multiple Marketplace Registrations

Register multiple marketplaces with different auto-load behaviors.

## Usage

```bash
python main.py
```

## Key Concepts

```python
registry = MarketplaceRegistry([
    MarketplaceRegistration(
        name="primary",
        source="github:company/plugins",
        auto_load="all",  # Load at conversation start
    ),
    MarketplaceRegistration(
        name="secondary",
        source="github:company/experimental",
        # auto_load=None - available but not auto-loaded
    ),
])
```

## Related

- [43_mixed_marketplace_skills](../../01_standalone_sdk/43_mixed_marketplace_skills/) - Example marketplace used here
