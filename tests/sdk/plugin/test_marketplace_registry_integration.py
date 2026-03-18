"""Integration tests for MarketplaceRegistry - full registration and resolution flow."""

import json
import pytest
from pathlib import Path

from openhands.sdk.plugin import (
    MarketplaceRegistration,
    MarketplaceRegistry,
    PluginSource,
    Plugin,
    Marketplace,
    PluginNotFoundError,
    AmbiguousPluginError,
)


def create_marketplace(
    base_path: Path,
    name: str,
    plugins: list[dict],
    skills: list[dict] | None = None,
) -> Path:
    """Helper to create a complete marketplace directory structure.
    
    Args:
        base_path: Parent directory for the marketplace
        name: Marketplace name
        plugins: List of plugin definitions, each with 'name' and optionally 'description'
        skills: Optional list of skill definitions
        
    Returns:
        Path to the marketplace directory
    """
    marketplace_dir = base_path / name
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    
    # Create .plugin/marketplace.json
    plugin_meta_dir = marketplace_dir / ".plugin"
    plugin_meta_dir.mkdir(exist_ok=True)
    
    # Build plugin entries with sources pointing to local directories
    plugin_entries = []
    for plugin in plugins:
        plugin_name = plugin["name"]
        plugin_entries.append({
            "name": plugin_name,
            "source": f"./plugins/{plugin_name}",
            "description": plugin.get("description", f"Plugin {plugin_name}"),
        })
        
        # Create plugin directory with plugin.json
        plugin_dir = marketplace_dir / "plugins" / plugin_name / ".plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        
        plugin_manifest = {
            "name": plugin_name,
            "version": plugin.get("version", "1.0.0"),
            "description": plugin.get("description", f"Plugin {plugin_name}"),
        }
        (plugin_dir / "plugin.json").write_text(json.dumps(plugin_manifest, indent=2))
        
        # Create a sample skill in the plugin
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        skill_content = f"""---
name: {plugin_name}-skill
description: A skill from {plugin_name}
---

# {plugin_name} Skill

This is a skill provided by the {plugin_name} plugin.
"""
        (skills_dir / "SKILL.md").write_text(skill_content)
    
    marketplace_data = {
        "name": name,
        "owner": {"name": "Test Owner", "email": "test@example.com"},
        "description": f"Test marketplace: {name}",
        "plugins": plugin_entries,
        "skills": skills or [],
    }
    
    (plugin_meta_dir / "marketplace.json").write_text(
        json.dumps(marketplace_data, indent=2)
    )
    
    return marketplace_dir


class TestMarketplaceRegistryIntegration:
    """Integration tests for the full marketplace registration and resolution flow."""

    def test_single_marketplace_registration_and_resolution(self, tmp_path):
        """Test registering a single marketplace and resolving plugins from it."""
        # Create a marketplace with two plugins
        marketplace_dir = create_marketplace(
            tmp_path,
            name="company-tools",
            plugins=[
                {"name": "formatter", "description": "Code formatter"},
                {"name": "linter", "description": "Code linter"},
            ],
        )
        
        # Register the marketplace
        registry = MarketplaceRegistry([
            MarketplaceRegistration(
                name="company",
                source=str(marketplace_dir),
                auto_load="all",
            ),
        ])
        
        # Verify registration
        assert "company" in registry.registrations
        assert registry.registrations["company"].auto_load == "all"
        
        # Resolve plugin with explicit marketplace
        source = registry.resolve_plugin("formatter@company")
        assert source.source == str(marketplace_dir / "plugins" / "formatter")
        
        # Resolve plugin without marketplace (search all)
        source = registry.resolve_plugin("linter")
        assert source.source == str(marketplace_dir / "plugins" / "linter")
        
        # List all plugins
        plugins = registry.list_plugins("company")
        assert set(plugins) == {"formatter", "linter"}

    def test_multiple_marketplace_registration(self, tmp_path):
        """Test registering multiple marketplaces with different plugins."""
        # Create two marketplaces
        public_dir = create_marketplace(
            tmp_path,
            name="public-marketplace",
            plugins=[
                {"name": "git", "description": "Git utilities"},
                {"name": "docker", "description": "Docker utilities"},
            ],
        )
        
        team_dir = create_marketplace(
            tmp_path,
            name="team-marketplace",
            plugins=[
                {"name": "deploy", "description": "Deployment tools"},
                {"name": "monitor", "description": "Monitoring tools"},
            ],
        )
        
        # Register both marketplaces
        registry = MarketplaceRegistry([
            MarketplaceRegistration(
                name="public",
                source=str(public_dir),
                auto_load="all",
            ),
            MarketplaceRegistration(
                name="team",
                source=str(team_dir),
                auto_load="all",
            ),
        ])
        
        # Resolve plugins from specific marketplaces
        git_source = registry.resolve_plugin("git@public")
        assert "public-marketplace" in git_source.source
        
        deploy_source = registry.resolve_plugin("deploy@team")
        assert "team-marketplace" in deploy_source.source
        
        # Resolve unique plugin without marketplace qualifier
        docker_source = registry.resolve_plugin("docker")
        assert "docker" in docker_source.source
        
        # List all plugins from all marketplaces
        all_plugins = registry.list_plugins()
        assert set(all_plugins) == {"git", "docker", "deploy", "monitor"}

    def test_auto_load_vs_registered_only(self, tmp_path):
        """Test that auto_load setting is correctly tracked."""
        public_dir = create_marketplace(
            tmp_path,
            name="public",
            plugins=[{"name": "common"}],
        )
        
        experimental_dir = create_marketplace(
            tmp_path,
            name="experimental",
            plugins=[{"name": "beta-tool"}],
        )
        
        registry = MarketplaceRegistry([
            MarketplaceRegistration(
                name="public",
                source=str(public_dir),
                auto_load="all",  # Auto-load
            ),
            MarketplaceRegistration(
                name="experimental",
                source=str(experimental_dir),
                # auto_load=None (default) - registered but not auto-loaded
            ),
        ])
        
        # Check auto_load registrations
        auto_load_regs = registry.get_auto_load_registrations()
        assert len(auto_load_regs) == 1
        assert auto_load_regs[0].name == "public"
        
        # Both marketplaces can still resolve plugins
        common_source = registry.resolve_plugin("common@public")
        assert common_source is not None
        
        beta_source = registry.resolve_plugin("beta-tool@experimental")
        assert beta_source is not None

    def test_ambiguous_plugin_error(self, tmp_path):
        """Test that ambiguous plugin names raise appropriate error."""
        # Create two marketplaces with a plugin of the same name
        mp1_dir = create_marketplace(
            tmp_path,
            name="marketplace1",
            plugins=[{"name": "shared-plugin", "description": "Version from MP1"}],
        )
        
        mp2_dir = create_marketplace(
            tmp_path,
            name="marketplace2",
            plugins=[{"name": "shared-plugin", "description": "Version from MP2"}],
        )
        
        registry = MarketplaceRegistry([
            MarketplaceRegistration(name="mp1", source=str(mp1_dir)),
            MarketplaceRegistration(name="mp2", source=str(mp2_dir)),
        ])
        
        # Resolving without qualifier should fail
        with pytest.raises(AmbiguousPluginError) as exc_info:
            registry.resolve_plugin("shared-plugin")
        
        assert exc_info.value.plugin_name == "shared-plugin"
        assert set(exc_info.value.matching_marketplaces) == {"mp1", "mp2"}
        
        # But explicit qualification should work
        source1 = registry.resolve_plugin("shared-plugin@mp1")
        assert "marketplace1" in source1.source
        
        source2 = registry.resolve_plugin("shared-plugin@mp2")
        assert "marketplace2" in source2.source

    def test_plugin_not_found_error(self, tmp_path):
        """Test that missing plugins raise appropriate error."""
        marketplace_dir = create_marketplace(
            tmp_path,
            name="test-marketplace",
            plugins=[{"name": "existing-plugin"}],
        )
        
        registry = MarketplaceRegistry([
            MarketplaceRegistration(name="test", source=str(marketplace_dir)),
        ])
        
        # Non-existent plugin in specific marketplace
        with pytest.raises(PluginNotFoundError) as exc_info:
            registry.resolve_plugin("nonexistent@test")
        assert exc_info.value.plugin_name == "nonexistent"
        assert exc_info.value.marketplace_name == "test"
        
        # Non-existent plugin searching all
        with pytest.raises(PluginNotFoundError) as exc_info:
            registry.resolve_plugin("nonexistent")
        assert exc_info.value.plugin_name == "nonexistent"
        assert exc_info.value.marketplace_name is None

    def test_marketplace_caching(self, tmp_path):
        """Test that marketplace fetching is cached."""
        marketplace_dir = create_marketplace(
            tmp_path,
            name="cached-marketplace",
            plugins=[{"name": "plugin-a"}, {"name": "plugin-b"}],
        )
        
        registry = MarketplaceRegistry([
            MarketplaceRegistration(name="cached", source=str(marketplace_dir)),
        ])
        
        # First resolution - should fetch and cache
        source_a = registry.resolve_plugin("plugin-a@cached")
        
        # Check that marketplace is now cached
        assert "cached" in registry._cache
        
        # Second resolution - should use cache
        source_b = registry.resolve_plugin("plugin-b@cached")
        
        # Cache should still have just one entry
        assert len(registry._cache) == 1

    def test_prefetch_all_marketplaces(self, tmp_path):
        """Test eagerly prefetching all registered marketplaces."""
        mp1_dir = create_marketplace(tmp_path, "mp1", [{"name": "p1"}])
        mp2_dir = create_marketplace(tmp_path, "mp2", [{"name": "p2"}])
        
        registry = MarketplaceRegistry([
            MarketplaceRegistration(name="mp1", source=str(mp1_dir)),
            MarketplaceRegistration(name="mp2", source=str(mp2_dir)),
        ])
        
        # Cache should be empty initially
        assert len(registry._cache) == 0
        
        # Prefetch all
        registry.prefetch_all()
        
        # Both should now be cached
        assert len(registry._cache) == 2
        assert "mp1" in registry._cache
        assert "mp2" in registry._cache

    def test_monorepo_marketplace_with_repo_path(self, tmp_path):
        """Test marketplace in a monorepo subdirectory."""
        # Create a monorepo structure
        monorepo_dir = tmp_path / "monorepo"
        monorepo_dir.mkdir()
        
        # Create marketplace in a subdirectory
        marketplace_subdir = monorepo_dir / "packages" / "marketplace"
        marketplace_subdir.mkdir(parents=True)
        
        # Create .plugin structure in the subdirectory
        plugin_meta_dir = marketplace_subdir / ".plugin"
        plugin_meta_dir.mkdir()
        
        # Create plugin directory
        plugin_dir = marketplace_subdir / "plugins" / "monorepo-plugin" / ".plugin"
        plugin_dir.mkdir(parents=True)
        
        plugin_manifest = {"name": "monorepo-plugin", "version": "1.0.0"}
        (plugin_dir / "plugin.json").write_text(json.dumps(plugin_manifest))
        
        marketplace_data = {
            "name": "monorepo-marketplace",
            "owner": {"name": "Test"},
            "plugins": [
                {"name": "monorepo-plugin", "source": "./plugins/monorepo-plugin"},
            ],
        }
        (plugin_meta_dir / "marketplace.json").write_text(json.dumps(marketplace_data))
        
        # Register with repo_path pointing to the subdirectory
        # Note: For local paths, repo_path isn't used the same way as git repos
        # The source should point directly to the marketplace directory
        registry = MarketplaceRegistry([
            MarketplaceRegistration(
                name="monorepo",
                source=str(marketplace_subdir),
            ),
        ])
        
        # Should be able to resolve the plugin
        source = registry.resolve_plugin("monorepo-plugin@monorepo")
        assert "monorepo-plugin" in source.source

    def test_full_plugin_load_flow(self, tmp_path):
        """Test the complete flow from registration to plugin loading."""
        # Create a marketplace directory manually with proper plugin structure
        marketplace_dir = tmp_path / "full-test-marketplace"
        marketplace_dir.mkdir()
        
        # Create marketplace manifest
        mp_meta_dir = marketplace_dir / ".plugin"
        mp_meta_dir.mkdir()
        
        marketplace_data = {
            "name": "full-test-marketplace",
            "owner": {"name": "Test"},
            "plugins": [
                {"name": "test-plugin", "source": "./plugins/test-plugin"},
            ],
        }
        (mp_meta_dir / "marketplace.json").write_text(json.dumps(marketplace_data))
        
        # Create plugin with skills directory at root level (not inside .plugin)
        plugin_dir = marketplace_dir / "plugins" / "test-plugin"
        plugin_dir.mkdir(parents=True)
        
        # Plugin manifest in .plugin/
        plugin_meta_dir = plugin_dir / ".plugin"
        plugin_meta_dir.mkdir()
        (plugin_meta_dir / "plugin.json").write_text(json.dumps({
            "name": "test-plugin",
            "version": "1.0.0",
            "description": "A complete test plugin",
        }))
        
        # Skills directory at plugin root level
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        skill_content = """---
name: test-plugin-skill
description: A skill from test-plugin
---

# Test Plugin Skill

This is a skill provided by the test-plugin plugin.
"""
        (skills_dir / "SKILL.md").write_text(skill_content)
        
        # Register the marketplace
        registry = MarketplaceRegistry([
            MarketplaceRegistration(
                name="fulltest",
                source=str(marketplace_dir),
                auto_load="all",
            ),
        ])
        
        # Resolve the plugin
        plugin_source = registry.resolve_plugin("test-plugin@fulltest")
        
        # Load the plugin using the resolved source
        plugin = Plugin.load(plugin_source.source)
        
        # Verify plugin was loaded correctly
        assert plugin.manifest.name == "test-plugin"
        assert plugin.manifest.description == "A complete test plugin"
        
        # Check that skills were loaded
        assert len(plugin.skills) > 0
        assert any("test-plugin" in s.name for s in plugin.skills)

    def test_marketplace_with_claude_plugin_directory(self, tmp_path):
        """Test marketplace using .claude-plugin directory (fallback)."""
        marketplace_dir = tmp_path / "claude-compat-marketplace"
        marketplace_dir.mkdir()
        
        # Use .claude-plugin instead of .plugin
        plugin_meta_dir = marketplace_dir / ".claude-plugin"
        plugin_meta_dir.mkdir()
        
        # Create plugin
        plugin_dir = marketplace_dir / "plugins" / "claude-plugin" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        
        plugin_manifest = {"name": "claude-plugin", "version": "1.0.0"}
        (plugin_dir / "plugin.json").write_text(json.dumps(plugin_manifest))
        
        marketplace_data = {
            "name": "claude-compat",
            "owner": {"name": "Test"},
            "plugins": [
                {"name": "claude-plugin", "source": "./plugins/claude-plugin"},
            ],
        }
        (plugin_meta_dir / "marketplace.json").write_text(json.dumps(marketplace_data))
        
        # Register and resolve
        registry = MarketplaceRegistry([
            MarketplaceRegistration(
                name="claude",
                source=str(marketplace_dir),
            ),
        ])
        
        source = registry.resolve_plugin("claude-plugin@claude")
        assert "claude-plugin" in source.source


class TestConversationLoadPlugin:
    """Test Conversation.load_plugin() integration with MarketplaceRegistry."""

    def test_load_plugin_from_marketplace(self, tmp_path):
        """Test loading a plugin via conversation.load_plugin()."""
        from openhands.sdk import LLM, Agent, AgentContext
        from openhands.sdk.conversation import Conversation
        
        # Create a marketplace with a plugin
        marketplace_dir = create_marketplace(
            tmp_path,
            name="test-marketplace",
            plugins=[{"name": "test-plugin", "description": "A test plugin"}],
        )
        
        # Create agent with registered marketplace (use dummy LLM - won't make calls)
        llm = LLM(model="test/model", api_key="test-key")
        
        agent_context = AgentContext(
            registered_marketplaces=[
                MarketplaceRegistration(
                    name="test",
                    source=str(marketplace_dir),
                ),
            ],
        )
        
        agent = Agent(
            llm=llm,
            tools=[],
            agent_context=agent_context,
        )
        
        # Create conversation
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace_dir),
        )
        
        # Verify no resolved_plugins yet
        assert conversation.resolved_plugins is None
        
        # Load the plugin
        conversation.load_plugin("test-plugin@test")
        
        # Verify resolved_plugins was updated
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1
        assert conversation.resolved_plugins[0].source == f"{marketplace_dir}/plugins/test-plugin"
        
        conversation.close()

    def test_load_plugin_no_marketplaces_registered(self, tmp_path):
        """Test that load_plugin raises ValueError when no marketplaces registered."""
        from openhands.sdk import LLM, Agent, AgentContext
        from openhands.sdk.conversation import Conversation
        
        # Create agent without registered marketplaces
        llm = LLM(model="test/model", api_key="test-key")
        
        agent = Agent(
            llm=llm,
            tools=[],
            agent_context=AgentContext(),
        )
        
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace_dir),
        )
        
        with pytest.raises(ValueError, match="No marketplaces registered"):
            conversation.load_plugin("some-plugin")
        
        conversation.close()

    def test_load_plugin_not_found(self, tmp_path):
        """Test that load_plugin raises PluginNotFoundError for missing plugins."""
        from openhands.sdk import LLM, Agent, AgentContext
        from openhands.sdk.conversation import Conversation
        
        # Create a marketplace with a plugin
        marketplace_dir = create_marketplace(
            tmp_path,
            name="test-marketplace",
            plugins=[{"name": "existing-plugin"}],
        )
        
        llm = LLM(model="test/model", api_key="test-key")
        
        agent_context = AgentContext(
            registered_marketplaces=[
                MarketplaceRegistration(
                    name="test",
                    source=str(marketplace_dir),
                ),
            ],
        )
        
        agent = Agent(
            llm=llm,
            tools=[],
            agent_context=agent_context,
        )
        
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace_dir),
        )
        
        with pytest.raises(PluginNotFoundError):
            conversation.load_plugin("nonexistent-plugin@test")
        
        conversation.close()
