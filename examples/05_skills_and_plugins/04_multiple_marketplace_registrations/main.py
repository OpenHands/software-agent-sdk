"""Example: Multiple Marketplace Registrations

Shows how to register multiple marketplaces and resolve plugins from them.

- auto_load="all": Load all plugins at conversation start
- auto_load=None: Register for resolution but don't auto-load
"""

from pathlib import Path

from openhands.sdk.plugin import (
    MarketplaceRegistration,
    MarketplaceRegistry,
)

# Reuse the existing example marketplace
EXAMPLE_MARKETPLACE = (
    Path(__file__).parent.parent.parent
    / "01_standalone_sdk"
    / "43_mixed_marketplace_skills"
)


def main():
    # Register multiple marketplaces with different auto-load settings
    registry = MarketplaceRegistry([
        MarketplaceRegistration(
            name="primary",
            source=str(EXAMPLE_MARKETPLACE),
            auto_load="all",  # Load skills at conversation start
        ),
        MarketplaceRegistration(
            name="secondary",
            source=str(EXAMPLE_MARKETPLACE),
            # auto_load=None (default) - available but not auto-loaded
        ),
    ])

    # Show registered marketplaces
    print("Registered marketplaces:")
    for name, reg in registry.registrations.items():
        status = "auto-load" if reg.auto_load == "all" else "on-demand"
        print(f"  {name}: {status}")

    # Show which marketplaces will auto-load
    auto_load = registry.get_auto_load_registrations()
    print(f"\nAuto-load marketplaces: {[r.name for r in auto_load]}")

    # List available skills from a marketplace
    marketplace, _ = registry.get_marketplace("primary")
    print(f"\nSkills in '{marketplace.name}':")
    for skill in marketplace.skills:
        print(f"  - {skill.name}: {skill.description}")


if __name__ == "__main__":
    main()
    print("\nEXAMPLE_COST: 0")
