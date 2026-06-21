"""Example: Registered Marketplaces and Runtime Plugin Loading

This example demonstrates the registered marketplace flow:

1. Register multiple marketplace catalogs on AgentContext.
2. Auto-load plugins from a marketplace with ``auto_load='all'``.
3. Load an additional plugin at runtime by marketplace-qualified name.

The example builds two temporary local marketplaces so it can run without network
access or external credentials.
"""

import json
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.marketplace import MarketplaceRegistration


def write_plugin(plugin_dir: Path, plugin_name: str, skill_name: str) -> None:
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": plugin_name,
                "version": "1.0.0",
                "description": f"Example plugin {plugin_name}",
            }
        )
    )

    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir()
    (skills_dir / f"{skill_name}.md").write_text(
        f"---\nname: {skill_name}\ndescription: Example skill\n---\n"
        f"Use {skill_name} when demonstrating registered marketplace plugins."
    )


def write_marketplace(marketplace_dir: Path, plugin_name: str, skill_name: str) -> None:
    write_plugin(marketplace_dir / "plugins" / plugin_name, plugin_name, skill_name)
    manifest_dir = marketplace_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "marketplace.json").write_text(
        json.dumps(
            {
                "name": marketplace_dir.name,
                "owner": {"name": "Example Team"},
                "plugins": [
                    {
                        "name": plugin_name,
                        "source": f"./plugins/{plugin_name}",
                        "description": f"Example marketplace plugin {plugin_name}",
                    }
                ],
            }
        )
    )


with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    team_marketplace = tmp_path / "team-marketplace"
    specialists_marketplace = tmp_path / "specialists-marketplace"
    write_marketplace(team_marketplace, "review-bot", "review-checklist")
    write_marketplace(specialists_marketplace, "incident-bot", "incident-brief")

    agent = Agent(
        llm=LLM(model="test/model", api_key=SecretStr("not-used")),
        tools=[],
        agent_context=AgentContext(
            registered_marketplaces=[
                MarketplaceRegistration(
                    name="team",
                    source=str(team_marketplace),
                    auto_load="all",
                ),
                MarketplaceRegistration(
                    name="specialists",
                    source=str(specialists_marketplace),
                ),
            ]
        ),
    )

    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path / "workspace"),
    )

    conversation.load_plugin("incident-bot@specialists")

    agent_context = conversation.agent.agent_context
    assert agent_context is not None
    skill_names = sorted(skill.name for skill in agent_context.skills or [])
    resolved_sources = [plugin.source for plugin in conversation.resolved_plugins or []]

    print("Registered marketplaces:")
    for registration in agent_context.registered_marketplaces:
        print(f"  - {registration.name}: auto_load={registration.auto_load}")

    print("Loaded skills:")
    for skill_name in skill_names:
        print(f"  - {skill_name}")

    print("Resolved plugins:")
    for source in resolved_sources:
        print(f"  - {source}")

    assert skill_names == ["incident-brief", "review-checklist"]
    assert any(
        source.endswith("team-marketplace/plugins/review-bot")
        for source in resolved_sources
    )
    assert any(
        source.endswith("specialists-marketplace/plugins/incident-bot")
        for source in resolved_sources
    )

print("EXAMPLE_COST: 0")
