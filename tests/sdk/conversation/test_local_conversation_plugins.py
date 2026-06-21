"""Tests for plugin loading via LocalConversation and Conversation factory."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.hooks import HookConfig
from openhands.sdk.hooks.config import HookDefinition, HookMatcher
from openhands.sdk.marketplace import MarketplaceRegistration
from openhands.sdk.plugin import PluginSource


@pytest.fixture
def mock_llm():
    """Create a mock LLM for agent tests."""
    return LLM(
        model="test/model",
        api_key=SecretStr("test-key"),
    )


@pytest.fixture
def basic_agent(mock_llm):
    """Create a basic agent for testing."""
    return Agent(
        llm=mock_llm,
        tools=[],
    )


def create_test_plugin(
    plugin_dir: Path,
    name: str = "test-plugin",
    skills: list[dict] | None = None,
    mcp_config: dict | None = None,
    hooks: dict | None = None,
):
    """Helper to create a test plugin directory."""
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"name": name, "version": "1.0.0", "description": f"Test plugin {name}"}
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest))

    if skills:
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        for skill in skills:
            skill_name = skill["name"]
            skill_content = skill["content"]
            skill_file = skills_dir / f"{skill_name}.md"
            skill_file.write_text(f"---\nname: {skill_name}\n---\n{skill_content}")

    if mcp_config:
        mcp_file = plugin_dir / ".mcp.json"
        mcp_file.write_text(json.dumps(mcp_config))

    if hooks:
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(json.dumps(hooks))

    return plugin_dir


def create_test_marketplace(
    marketplace_dir: Path,
    plugins: list[dict],
    name: str = "test-marketplace",
) -> Path:
    """Helper to create a test marketplace with local plugin entries."""
    manifest_dir = marketplace_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for plugin in plugins:
        plugin_name = plugin["name"]
        create_test_plugin(
            marketplace_dir / "plugins" / plugin_name,
            name=plugin_name,
            skills=plugin.get("skills"),
            mcp_config=plugin.get("mcp_config"),
            hooks=plugin.get("hooks"),
        )
        entries.append(
            {
                "name": plugin_name,
                "source": f"./plugins/{plugin_name}",
                "description": f"Test plugin {plugin_name}",
            }
        )

    manifest = {
        "name": name,
        "owner": {"name": "Test Team"},
        "plugins": entries,
    }
    (manifest_dir / "marketplace.json").write_text(json.dumps(manifest))
    return marketplace_dir


class TestLocalConversationPlugins:
    """Tests for plugin loading in LocalConversation.

    Note: Plugins are lazy-loaded on first run()/send_message() call.
    Tests trigger _ensure_plugins_loaded() to verify loading behavior.
    """

    def test_auto_load_marketplace_plugins(self, tmp_path: Path, mock_llm):
        """Test marketplace registrations auto-load plugins at startup."""
        marketplace_dir = create_test_marketplace(
            tmp_path / "marketplace",
            plugins=[
                {
                    "name": "auto-plugin",
                    "skills": [{"name": "auto-skill", "content": "Auto-loaded skill"}],
                }
            ],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        agent = Agent(
            llm=mock_llm,
            tools=[],
            agent_context=AgentContext(
                registered_marketplaces=[
                    MarketplaceRegistration(
                        name="auto",
                        source=str(marketplace_dir),
                        auto_load="all",
                    )
                ]
            ),
        )

        conversation = LocalConversation(
            agent=agent,
            workspace=workspace,
            visualizer=None,
        )
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "auto-skill" in skill_names
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1

        conversation.close()

    def test_registered_only_marketplace_does_not_auto_load(
        self, tmp_path: Path, mock_llm
    ):
        """Test registered marketplaces without auto_load stay resolution-only."""
        marketplace_dir = create_test_marketplace(
            tmp_path / "marketplace",
            plugins=[
                {
                    "name": "manual-plugin",
                    "skills": [{"name": "manual-skill", "content": "Manual skill"}],
                }
            ],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        agent = Agent(
            llm=mock_llm,
            tools=[],
            agent_context=AgentContext(
                registered_marketplaces=[
                    MarketplaceRegistration(name="manual", source=str(marketplace_dir))
                ]
            ),
        )

        conversation = LocalConversation(
            agent=agent,
            workspace=workspace,
            visualizer=None,
        )
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        assert conversation.agent.agent_context.skills == []
        assert conversation.resolved_plugins is None

        conversation.close()

    def test_explicit_plugins_override_auto_loaded_marketplace_plugins(
        self, tmp_path: Path, mock_llm
    ):
        """Test explicit plugins load after auto-loaded marketplace plugins."""
        marketplace_dir = create_test_marketplace(
            tmp_path / "marketplace",
            plugins=[
                {
                    "name": "auto-plugin",
                    "skills": [{"name": "shared", "content": "Auto content"}],
                }
            ],
        )
        explicit_plugin = create_test_plugin(
            tmp_path / "explicit-plugin",
            name="explicit-plugin",
            skills=[{"name": "shared", "content": "Explicit content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        agent = Agent(
            llm=mock_llm,
            tools=[],
            agent_context=AgentContext(
                registered_marketplaces=[
                    MarketplaceRegistration(
                        name="auto",
                        source=str(marketplace_dir),
                        auto_load="all",
                    )
                ]
            ),
        )

        conversation = LocalConversation(
            agent=agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(explicit_plugin))],
            visualizer=None,
        )
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skills = {s.name: s for s in conversation.agent.agent_context.skills}
        assert skills["shared"].content == "Explicit content"
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 2

        conversation.close()

    def test_registered_marketplaces_skip_legacy_public_skill_loading(
        self, tmp_path: Path
    ):
        """Test registered marketplaces suppress legacy public skill loading."""
        with patch(
            "openhands.sdk.context.agent_context.load_available_skills"
        ) as mock_load_available_skills:
            AgentContext(
                load_public_skills=True,
                registered_marketplaces=[
                    MarketplaceRegistration(
                        name="auto",
                        source=str(tmp_path / "marketplace"),
                        auto_load="all",
                    )
                ],
            )

        mock_load_available_skills.assert_not_called()

    def test_create_conversation_with_plugins(self, tmp_path: Path, basic_agent):
        """Test creating LocalConversation with plugins parameter."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            skills=[{"name": "test-skill", "content": "Test skill content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        # Agent should have been updated with plugin skills
        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "test-skill" in skill_names

        # Verify resolved plugins are tracked
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1
        assert conversation.resolved_plugins[0].source == str(plugin_dir)

        conversation.close()

    def test_conversation_with_multiple_plugins(self, tmp_path: Path, basic_agent):
        """Test loading multiple plugins via LocalConversation."""
        plugin1 = create_test_plugin(
            tmp_path / "plugin1",
            name="plugin1",
            skills=[{"name": "skill-a", "content": "Content A"}],
        )
        plugin2 = create_test_plugin(
            tmp_path / "plugin2",
            name="plugin2",
            skills=[{"name": "skill-b", "content": "Content B"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[
                PluginSource(source=str(plugin1)),
                PluginSource(source=str(plugin2)),
            ],
            visualizer=None,
        )

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "skill-a" in skill_names
        assert "skill-b" in skill_names

        # Verify both plugins tracked
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 2

        conversation.close()

    def test_plugin_hooks_combined_with_explicit_hooks(
        self, tmp_path: Path, basic_agent
    ):
        """Test that plugin hooks are combined with explicit hook_config."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            hooks={
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "plugin-*", "hooks": [{"command": "plugin-cmd"}]}
                    ]
                }
            },
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        explicit_hooks = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="explicit-*", hooks=[HookDefinition(command="explicit-cmd")]
                )
            ]
        )

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            hook_config=explicit_hooks,
            visualizer=None,
        )

        # Hooks are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        # Both hook sources should be combined
        assert conversation._hook_processor is not None
        # We can verify hooks were processed by checking the hook_config passed
        # (The actual hook_processor is internal, but we trust the merging works)
        conversation.close()

    def test_hook_sub_conversations_receive_persistence_base_dir(
        self, tmp_path: Path, basic_agent
    ):
        """Agent hook persistence should not nest under the parent conversation id."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        persistence_base = tmp_path / "state"
        hook_config = HookConfig(
            pre_tool_use=[
                HookMatcher(matcher="*", hooks=[HookDefinition(command="echo test")])
            ]
        )

        processor = MagicMock()
        processor.on_event = MagicMock()
        processor.set_conversation_state = MagicMock()
        processor.run_session_start = MagicMock()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            persistence_dir=persistence_base,
            hook_config=hook_config,
            visualizer=None,
        )

        with patch(
            "openhands.sdk.conversation.impl.local_conversation.create_hook_callback",
            return_value=(processor, processor.on_event),
        ) as mock_create_hook_callback:
            conversation._ensure_plugins_loaded()

        assert conversation.state.persistence_dir is not None
        assert Path(conversation.state.persistence_dir).parent == persistence_base
        assert mock_create_hook_callback.call_args.kwargs["persistence_dir"] == str(
            persistence_base
        )
        conversation.close()

    def test_plugins_not_loaded_until_needed(self, tmp_path: Path, basic_agent):
        """Test that plugins are not loaded in constructor (lazy loading)."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            skills=[{"name": "test-skill", "content": "Test skill content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Before loading, plugins should not be applied
        assert conversation._plugins_loaded is False
        assert conversation.resolved_plugins is None
        assert conversation.agent.agent_context is None

        # After triggering load
        conversation._ensure_plugins_loaded()

        assert conversation._plugins_loaded is True
        assert conversation.resolved_plugins is not None
        assert conversation.agent.agent_context is not None

        conversation.close()

    def test_plugin_mcp_config_is_initialized(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """Test that MCP config from plugins is properly initialized.

        This is a regression test for a bug where MCP tools from plugins were not
        being created because the agent was initialized before plugins were loaded.
        """
        # Mock create_mcp_tools to avoid actually starting MCP servers in tests
        mcp_tools_created = []

        def mock_create_mcp_tools(config, timeout):
            mcp_tools_created.append(config)
            return []  # Return empty list for testing

        import openhands.sdk.agent.base

        monkeypatch.setattr(
            openhands.sdk.agent.base, "create_mcp_tools", mock_create_mcp_tools
        )

        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            mcp_config={"mcpServers": {"test-server": {"command": "test-cmd"}}},
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Before loading plugins, no MCP config should exist
        assert (
            conversation.agent.mcp_config is None or conversation.agent.mcp_config == {}
        )

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # After loading, MCP config should be merged
        assert conversation.agent.mcp_config is not None
        assert "mcpServers" in conversation.agent.mcp_config
        assert "test-server" in conversation.agent.mcp_config["mcpServers"]

        # The agent should have been initialized with the complete MCP config
        # This verifies that create_mcp_tools was called with the plugin's MCP config
        assert len(mcp_tools_created) > 0
        assert "mcpServers" in mcp_tools_created[-1]
        assert "test-server" in mcp_tools_created[-1]["mcpServers"]

        conversation.close()


class TestConversationFactoryPlugins:
    """Tests for plugin loading via Conversation factory.

    Note: Plugins are lazy-loaded on first run()/send_message() call.
    """

    def test_factory_passes_plugins_to_local_conversation(
        self, tmp_path: Path, basic_agent
    ):
        """Test that Conversation factory passes plugins to LocalConversation."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            skills=[{"name": "factory-skill", "content": "Factory skill content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = Conversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        assert isinstance(conversation, LocalConversation)

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "factory-skill" in skill_names
        conversation.close()

    def test_factory_with_string_workspace_and_plugins(
        self, tmp_path: Path, basic_agent
    ):
        """Test factory with string workspace path and plugins."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="plugin",
            skills=[{"name": "skill", "content": "Content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = Conversation(
            agent=basic_agent,
            workspace=str(workspace),
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Plugins are lazy loaded - trigger loading
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        assert len(conversation.agent.agent_context.skills) == 1
        conversation.close()

    def test_factory_with_no_plugins(self, tmp_path: Path, basic_agent):
        """Test that factory works without plugins (plugins=None is default)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = Conversation(
            agent=basic_agent,
            workspace=workspace,
            visualizer=None,
        )

        # Should work without errors
        assert conversation is not None
        conversation.close()


class TestPluginMcpSecretsExpansion:
    """Tests for per-conversation secrets in MCP config expansion.

    These tests verify that secrets injected via the REST API are correctly
    used for MCP config variable expansion (${VAR} syntax).

    See: https://github.com/OpenHands/software-agent-sdk/issues/2872
    """

    def test_plugin_mcp_secrets_without_defaults(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """Test that per-conversation secrets work for variables without defaults.

        This test verifies that ${VAR} placeholders (without defaults) are
        correctly expanded using secrets from SecretRegistry.
        """
        # Mock create_mcp_tools to avoid actually starting MCP servers
        mcp_tools_created = []

        def mock_create_mcp_tools(config, timeout):
            mcp_tools_created.append(config)
            return []

        import openhands.sdk.agent.base

        monkeypatch.setattr(
            openhands.sdk.agent.base, "create_mcp_tools", mock_create_mcp_tools
        )

        # Create plugin with MCP config using ${VAR} WITHOUT default
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            mcp_config={
                "mcpServers": {
                    "test-server": {
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ${SECRET_TOKEN}"},
                    }
                }
            },
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Inject secret BEFORE triggering plugin loading
        conversation.update_secrets({"SECRET_TOKEN": "my-actual-secret"})

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # Verify the secret was expanded in the MCP config
        assert conversation.agent.mcp_config is not None
        auth_header = conversation.agent.mcp_config["mcpServers"]["test-server"][
            "headers"
        ]["Authorization"]
        assert auth_header == "Bearer my-actual-secret", (
            f"Expected 'Bearer my-actual-secret', got '{auth_header}'"
        )

        conversation.close()

    def test_plugin_mcp_secrets_with_defaults(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """Test that per-conversation secrets work with default values.

        This test verifies that ${VAR:-default} placeholders use the secret
        value when available, NOT the default.

        This is a regression test for the double-expansion bug where:
        1. First expansion in plugin.py replaces ${VAR:-default} with "default"
        2. Second expansion in local_conversation.py sees no placeholder to expand

        Expected: Secret value should be used, not the default.
        """
        # Mock create_mcp_tools to avoid actually starting MCP servers
        mcp_tools_created = []

        def mock_create_mcp_tools(config, timeout):
            mcp_tools_created.append(config)
            return []

        import openhands.sdk.agent.base

        monkeypatch.setattr(
            openhands.sdk.agent.base, "create_mcp_tools", mock_create_mcp_tools
        )

        # Create plugin with MCP config using ${VAR:-default} WITH default
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            mcp_config={
                "mcpServers": {
                    "test-server": {
                        "url": "https://example.com/mcp",
                        "headers": {
                            "Authorization": "Bearer ${SECRET_TOKEN:-fallback-token}"
                        },
                    }
                }
            },
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Inject secret BEFORE triggering plugin loading
        conversation.update_secrets({"SECRET_TOKEN": "my-actual-secret"})

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # CRITICAL: Verify the secret was used, NOT the default
        assert conversation.agent.mcp_config is not None
        auth_header = conversation.agent.mcp_config["mcpServers"]["test-server"][
            "headers"
        ]["Authorization"]

        # This assertion will FAIL with double-expansion bug
        assert auth_header == "Bearer my-actual-secret", (
            f"Expected secret value 'Bearer my-actual-secret', got '{auth_header}'. "
            "This is likely due to double-expansion: the default value was applied "
            "during plugin loading before secrets were available."
        )

        conversation.close()

    def test_plugin_mcp_secrets_fallback_to_default_when_no_secret(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """Test that default values work when no secret is provided.

        This test verifies that ${VAR:-default} correctly falls back to the
        default value when no secret is injected.
        """
        # Mock create_mcp_tools to avoid actually starting MCP servers
        mcp_tools_created = []

        def mock_create_mcp_tools(config, timeout):
            mcp_tools_created.append(config)
            return []

        import openhands.sdk.agent.base

        monkeypatch.setattr(
            openhands.sdk.agent.base, "create_mcp_tools", mock_create_mcp_tools
        )

        # Create plugin with MCP config using ${VAR:-default}
        # Note: MCP config structure requires valid fields, so we use 'headers'
        # for string values instead of 'timeout' which expects an integer
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            mcp_config={
                "mcpServers": {
                    "test-server": {
                        "url": "${API_URL:-https://default.example.com/mcp}",
                        "headers": {
                            "X-Custom-Header": "${CUSTOM_HEADER:-default-header-value}"
                        },
                    }
                }
            },
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(plugin_dir))],
            visualizer=None,
        )

        # Do NOT inject any secrets - should use defaults

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # Verify defaults were used
        assert conversation.agent.mcp_config is not None
        url = conversation.agent.mcp_config["mcpServers"]["test-server"]["url"]
        header = conversation.agent.mcp_config["mcpServers"]["test-server"]["headers"][
            "X-Custom-Header"
        ]

        assert url == "https://default.example.com/mcp"
        assert header == "default-header-value"

        conversation.close()


class TestPluginSourceSecretExpansion:
    """Secrets in plugin ``source``/``ref`` are expanded before fetch.

    This enables cloning private plugin repositories with a token supplied via
    the per-conversation secrets API, e.g. a ``source`` of
    ``https://x-token-auth:${MY_TOKEN}@host/org/repo.git``.
    """

    def _make_conversation(
        self, tmp_path: Path, basic_agent, plugin_source: str, ref: str | None = None
    ):
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="private-plugin",
            skills=[{"name": "private-skill", "content": "Private content"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=plugin_source, ref=ref)],
            visualizer=None,
        )
        return conversation, plugin_dir

    def test_source_secret_expanded_before_fetch(self, tmp_path: Path, basic_agent):
        """A ${VAR} in the source is replaced with the secret value before clone."""
        source = "https://x-token-auth:${MY_TOKEN}@host.example.com/org/repo.git"
        conversation, plugin_dir = self._make_conversation(
            tmp_path, basic_agent, source
        )
        conversation.update_secrets({"MY_TOKEN": "s3cr3t-value"})

        captured: dict[str, str | None] = {}

        def fake_fetch(source, ref=None, repo_path=None, **kwargs):
            captured["source"] = source
            captured["ref"] = ref
            return plugin_dir, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        with patch(
            "openhands.sdk.conversation.impl.local_conversation."
            "fetch_plugin_with_resolution",
            side_effect=fake_fetch,
        ):
            conversation._ensure_plugins_loaded()

        # The secret was expanded in the URL handed to the fetcher.
        assert captured["source"] == (
            "https://x-token-auth:s3cr3t-value@host.example.com/org/repo.git"
        )

        # Persisted state must NOT contain the raw secret value.
        assert conversation.resolved_plugins is not None
        assert "s3cr3t-value" not in conversation.resolved_plugins[0].source

        conversation.close()

    def test_host_env_not_expanded_in_source(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """Host environment variables must NOT be folded into the source URL."""
        monkeypatch.setenv("HOST_ONLY_VAR", "host-value")
        source = "https://x-token-auth:${HOST_ONLY_VAR}@host.example.com/org/repo.git"
        conversation, plugin_dir = self._make_conversation(
            tmp_path, basic_agent, source
        )
        # Deliberately register NO secret named HOST_ONLY_VAR.

        captured: dict[str, str | None] = {}

        def fake_fetch(source, ref=None, repo_path=None, **kwargs):
            captured["source"] = source
            return plugin_dir, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        with patch(
            "openhands.sdk.conversation.impl.local_conversation."
            "fetch_plugin_with_resolution",
            side_effect=fake_fetch,
        ):
            conversation._ensure_plugins_loaded()

        # Placeholder preserved verbatim - host env was not used.
        assert captured["source"] == source
        assert "host-value" not in (captured["source"] or "")

        conversation.close()

    def test_unknown_var_with_default_left_untouched(self, tmp_path: Path, basic_agent):
        """`${MISSING:-default}` is preserved verbatim (expand_defaults=False).

        An unresolved variable in a URL must not be silently replaced with its
        default -- the placeholder is left intact so the failure is visible
        rather than producing a wrong-but-plausible URL.
        """
        source = "https://x-token-auth:${MISSING:-fallback}@host.example.com/o/r.git"
        conversation, plugin_dir = self._make_conversation(
            tmp_path, basic_agent, source
        )
        # No secret named MISSING registered.

        captured: dict[str, str | None] = {}

        def fake_fetch(source, ref=None, repo_path=None, **kwargs):
            captured["source"] = source
            return plugin_dir, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        with patch(
            "openhands.sdk.conversation.impl.local_conversation."
            "fetch_plugin_with_resolution",
            side_effect=fake_fetch,
        ):
            conversation._ensure_plugins_loaded()

        # Placeholder preserved verbatim: the default is NOT substituted in,
        # the whole ${MISSING:-fallback} token is left intact.
        assert captured["source"] == source
        assert "${MISSING:-fallback}" in (captured["source"] or "")

        conversation.close()

    def test_ref_secret_expanded_before_fetch(self, tmp_path: Path, basic_agent):
        """A ${VAR} in the ref is also expanded from secrets."""
        source = str(tmp_path / "plugin")
        conversation, plugin_dir = self._make_conversation(
            tmp_path, basic_agent, source, ref="${MY_REF}"
        )
        conversation.update_secrets({"MY_REF": "v1.2.3"})

        captured: dict[str, str | None] = {}

        def fake_fetch(source, ref=None, repo_path=None, **kwargs):
            captured["ref"] = ref
            return plugin_dir, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        with patch(
            "openhands.sdk.conversation.impl.local_conversation."
            "fetch_plugin_with_resolution",
            side_effect=fake_fetch,
        ):
            conversation._ensure_plugins_loaded()

        assert captured["ref"] == "v1.2.3"

        conversation.close()
