"""Tests for plugin loading via LocalConversation and Conversation factory."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.hooks import HookConfig
from openhands.sdk.hooks.config import HookDefinition, HookMatcher
from openhands.sdk.marketplace import MarketplaceRegistration
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, dump_mcp_config
from openhands.sdk.plugin import (
    PluginSource,
    discovery,
    install_plugin,
    installed,
)
from openhands.sdk.tool.builtins import ThinkTool


class EmptyMCPClient:
    def __init__(self):
        self.tools = []


class RecordingMCPToolProvider:
    def __init__(
        self,
        created: list[Any],
        client: object | None = None,
        state_locked: Callable[[], bool] | None = None,
    ):
        self.created = created
        self.client = client or EmptyMCPClient()
        self.state_locked = state_locked

    def create_tools(
        self, mcp_config: dict[str, MCPServer], timeout: float = 30.0
    ) -> MCPClient:
        if self.state_locked is None:
            self.created.append(mcp_config)
        else:
            self.created.append((mcp_config, self.state_locked()))
        return cast(MCPClient, self.client)


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
        entry = {
            "name": plugin_name,
            "source": plugin.get("source", f"./plugins/{plugin_name}"),
            "description": f"Test plugin {plugin_name}",
        }
        if "ref" in plugin:
            entry["ref"] = plugin["ref"]
        if "repo_path" in plugin:
            entry["repo_path"] = plugin["repo_path"]
        entries.append(entry)

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
                        auto_load=True,
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

    def test_auto_load_marketplace_plugin_list_selects_plugins(
        self, tmp_path: Path, mock_llm
    ):
        marketplace_dir = create_test_marketplace(
            tmp_path / "marketplace",
            plugins=[
                {
                    "name": "formatter",
                    "skills": [{"name": "formatter-skill", "content": "Format"}],
                },
                {
                    "name": "linter",
                    "skills": [{"name": "linter-skill", "content": "Lint"}],
                },
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
                        name="selective",
                        source=str(marketplace_dir),
                        auto_load=["formatter"],
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
        assert [skill.name for skill in conversation.agent.agent_context.skills] == [
            "formatter-skill"
        ]
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1

        conversation.close()

    def test_auto_load_marketplace_expands_registration_secret_refs(
        self, tmp_path: Path, mock_llm
    ):
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
                        name="private",
                        source="https://${MARKETPLACE_TOKEN}@example.com/catalog.git",
                        ref="${MARKETPLACE_REF}",
                        repo_path="catalogs/team",
                        auto_load=True,
                    )
                ]
            ),
        )
        conversation = LocalConversation(
            agent=agent, workspace=workspace, visualizer=None
        )
        conversation.update_secrets(
            {
                "MARKETPLACE_TOKEN": "token-value",
                "MARKETPLACE_REF": "release-branch",
            }
        )

        with patch(
            "openhands.sdk.marketplace.registry.fetch_plugin_with_resolution",
            return_value=(marketplace_dir, "abc123"),
        ) as mock_fetch:
            conversation._ensure_plugins_loaded()

        mock_fetch.assert_called_once_with(
            source="https://token-value@example.com/catalog.git",
            ref="release-branch",
            repo_path="catalogs/team",
        )
        assert conversation.agent.agent_context is not None
        assert [skill.name for skill in conversation.agent.agent_context.skills] == [
            "auto-skill"
        ]
        conversation.close()

    def test_auto_load_marketplace_continues_after_fetch_failure(
        self, tmp_path: Path, mock_llm, caplog: pytest.LogCaptureFixture
    ):
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
                        name="broken",
                        source=str(tmp_path / "missing-marketplace"),
                        auto_load=True,
                    ),
                    MarketplaceRegistration(
                        name="working",
                        source=str(marketplace_dir),
                        auto_load=True,
                    ),
                ]
            ),
        )
        conversation = LocalConversation(
            agent=agent, workspace=workspace, visualizer=None
        )

        with caplog.at_level(
            "WARNING", logger="openhands.sdk.conversation.impl.local_conversation"
        ):
            conversation._ensure_plugins_loaded()

        assert (
            "Failed to load marketplace 'broken'; continuing without it" in caplog.text
        )
        assert conversation.agent.agent_context is not None
        assert [skill.name for skill in conversation.agent.agent_context.skills] == [
            "auto-skill"
        ]
        conversation.close()

    def test_auto_load_marketplace_duplicate_names_fail(self, tmp_path: Path, mock_llm):
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
                        name="duplicate",
                        source=str(marketplace_dir),
                        auto_load=True,
                    ),
                    MarketplaceRegistration(
                        name="duplicate",
                        source=str(marketplace_dir),
                        auto_load=True,
                    ),
                ]
            ),
        )
        conversation = LocalConversation(
            agent=agent, workspace=workspace, visualizer=None
        )

        try:
            with pytest.raises(ValueError, match="Duplicate marketplace registration"):
                conversation._ensure_plugins_loaded()
        finally:
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
                        auto_load=True,
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
                        auto_load=True,
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

    def test_load_plugin_from_registered_marketplace(self, tmp_path: Path, mock_llm):
        """Test runtime plugin loading from a registered marketplace."""
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
            agent=agent, workspace=workspace, visualizer=None
        )

        conversation.load_plugin("manual-plugin@manual")

        assert conversation.agent.agent_context is not None
        skills = {
            skill.name: skill for skill in conversation.agent.agent_context.skills
        }
        assert skills["manual-skill"].content == "Manual skill"
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1

        conversation.close()

    def test_load_plugin_expands_resolved_plugin_source_secret_refs(
        self,
        tmp_path: Path,
        mock_llm,
        caplog: pytest.LogCaptureFixture,
    ):
        marketplace_dir = create_test_marketplace(
            tmp_path / "marketplace",
            plugins=[
                {
                    "name": "private-plugin",
                    "source": {
                        "source": "url",
                        "url": "https://${PLUGIN_TOKEN}@example.com/private.git",
                        "ref": "${PLUGIN_REF}",
                        "path": "plugins/private-plugin",
                    },
                    "skills": [{"name": "private-skill", "content": "Private skill"}],
                }
            ],
        )
        plugin_dir = marketplace_dir / "plugins" / "private-plugin"
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
            agent=agent, workspace=workspace, visualizer=None
        )
        conversation.update_secrets(
            {"PLUGIN_TOKEN": "token-value", "PLUGIN_REF": "release-branch"}
        )

        with (
            caplog.at_level(logging.INFO),
            patch(
                "openhands.sdk.conversation.impl.local_conversation."
                "fetch_plugin_with_resolution",
                return_value=(plugin_dir, "abc123"),
            ) as mock_fetch,
        ):
            conversation.load_plugin("private-plugin")

        assert "token-value" not in caplog.text
        assert "https://" not in caplog.text
        mock_fetch.assert_called_once_with(
            source="https://token-value@example.com/private.git",
            ref="release-branch",
            repo_path="plugins/private-plugin",
        )
        assert conversation.agent.agent_context is not None
        assert [skill.name for skill in conversation.agent.agent_context.skills] == [
            "private-skill"
        ]
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1

        conversation.close()

    def test_load_plugin_adds_runtime_tools_without_reinitializing_existing_tools(
        self, tmp_path: Path, mock_llm
    ):
        mcp_tools_created = []

        class RuntimeOnlyTool(ThinkTool):
            name = "runtime_only"

        runtime_tool = RuntimeOnlyTool.create()[0]

        class RuntimeMCPClient:
            def __init__(self):
                self.tools = [runtime_tool]

        marketplace_dir = create_test_marketplace(
            tmp_path / "marketplace",
            plugins=[
                {
                    "name": "mcp-plugin",
                    "mcp_config": {
                        "mcpServers": {"runtime-server": {"command": "runtime"}}
                    },
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
            mcp_tool_provider=RecordingMCPToolProvider(
                mcp_tools_created,
                RuntimeMCPClient(),
                state_locked=lambda: conversation.state.locked(),
            ),
        )
        conversation._ensure_agent_ready()
        existing_tools = dict(conversation.agent.tools_map)

        conversation.load_plugin("mcp-plugin")

        for name, tool in existing_tools.items():
            assert conversation.agent.tools_map[name] is tool
        assert conversation.agent.tools_map[runtime_tool.name] is runtime_tool
        assert "runtime-server" in conversation.agent.mcp_config
        assert len(mcp_tools_created) == 1
        created_config, state_locked = mcp_tools_created[0]
        assert not state_locked
        assert "runtime-server" in created_config

        conversation.close()

    def test_load_plugin_merges_runtime_hooks_and_restarts_processor(
        self, tmp_path: Path, mock_llm
    ):
        marketplace_dir = create_test_marketplace(
            tmp_path / "marketplace",
            plugins=[
                {
                    "name": "hook-plugin",
                    "hooks": {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "runtime-*",
                                    "hooks": [{"command": "runtime-cmd"}],
                                }
                            ]
                        }
                    },
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
        explicit_hooks = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="explicit-*", hooks=[HookDefinition(command="explicit-cmd")]
                )
            ]
        )
        conversation = LocalConversation(
            agent=agent,
            workspace=workspace,
            hook_config=explicit_hooks,
            visualizer=None,
        )
        initial_processor = MagicMock()
        initial_processor.on_event = MagicMock()
        runtime_processor = MagicMock()
        runtime_processor.on_event = MagicMock()

        callback_lock_owned: list[bool] = []

        def mock_create_hook_callback(*args, **kwargs):
            callback_lock_owned.append(conversation.state._lock.owned())
            if len(callback_lock_owned) == 1:
                return initial_processor, initial_processor.on_event
            return runtime_processor, runtime_processor.on_event

        with patch(
            "openhands.sdk.conversation.impl.local_conversation.create_hook_callback",
            side_effect=mock_create_hook_callback,
        ) as mock_create_hook_callback:
            conversation.load_plugin("hook-plugin")

        assert conversation.state.hook_config is not None
        assert [
            matcher.matcher for matcher in conversation.state.hook_config.pre_tool_use
        ] == ["explicit-*", "runtime-*"]
        assert mock_create_hook_callback.call_count == 2
        assert callback_lock_owned[1]
        initial_processor.set_conversation_state.assert_called_once_with(
            conversation.state
        )
        initial_processor.run_session_start.assert_called_once()
        initial_processor.run_session_end.assert_called_once()
        runtime_processor.set_conversation_state.assert_called_once_with(
            conversation.state
        )
        runtime_processor.run_session_start.assert_called_once()

        conversation.close()

    def test_load_plugin_requires_registered_marketplaces(
        self, tmp_path: Path, basic_agent
    ):
        """Test runtime plugin loading requires registered marketplaces."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            visualizer=None,
        )

        with pytest.raises(ValueError, match="registered_marketplaces"):
            conversation.load_plugin("missing-plugin")

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

    def test_plugin_mcp_config_are_initialized(self, tmp_path: Path, basic_agent):
        """Test that MCP servers from plugins are properly initialized.

        This is a regression test for a bug where MCP tools from plugins were not
        being created because the agent was initialized before plugins were loaded.
        """
        # Inject an MCP provider to avoid actually starting MCP servers in tests
        mcp_tools_created = []
        mcp_tool_provider = RecordingMCPToolProvider(mcp_tools_created)

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
            mcp_tool_provider=mcp_tool_provider,
        )

        # Before loading plugins, no MCP servers should exist
        assert conversation.agent.mcp_config == {}

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # After loading, MCP servers should be merged
        assert "test-server" in conversation.agent.mcp_config

        # The agent should have been initialized with the complete MCP servers
        # This verifies that create_mcp_tools was called with the plugin's MCP servers
        assert len(mcp_tools_created) > 0
        assert "test-server" in mcp_tools_created[-1]

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

    def test_plugin_mcp_secrets_without_defaults(self, tmp_path: Path, basic_agent):
        """Test that per-conversation secrets work for variables without defaults.

        This test verifies that ${VAR} placeholders (without defaults) are
        correctly expanded using secrets from SecretRegistry.
        """
        # Inject an MCP provider to avoid actually starting MCP servers
        mcp_tools_created = []
        mcp_tool_provider = RecordingMCPToolProvider(mcp_tools_created)

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
            mcp_tool_provider=mcp_tool_provider,
        )

        # Inject secret BEFORE triggering plugin loading
        conversation.update_secrets({"SECRET_TOKEN": "my-actual-secret"})

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # Verify the secret was expanded in the MCP servers
        headers = dump_mcp_config(conversation.agent.mcp_config)["test-server"][
            "headers"
        ]
        assert isinstance(headers, dict)
        auth_header = headers["Authorization"]
        assert auth_header == "Bearer my-actual-secret", (
            f"Expected 'Bearer my-actual-secret', got '{auth_header}'"
        )

        conversation.close()

    def test_plugin_mcp_secrets_with_defaults(self, tmp_path: Path, basic_agent):
        """Test that per-conversation secrets work with default values.

        This test verifies that ${VAR:-default} placeholders use the secret
        value when available, NOT the default.

        This is a regression test for the double-expansion bug where:
        1. First expansion in plugin.py replaces ${VAR:-default} with "default"
        2. Second expansion in local_conversation.py sees no placeholder to expand

        Expected: Secret value should be used, not the default.
        """
        # Inject an MCP provider to avoid actually starting MCP servers
        mcp_tools_created = []
        mcp_tool_provider = RecordingMCPToolProvider(mcp_tools_created)

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
            mcp_tool_provider=mcp_tool_provider,
        )

        # Inject secret BEFORE triggering plugin loading
        conversation.update_secrets({"SECRET_TOKEN": "my-actual-secret"})

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # CRITICAL: Verify the secret was used, NOT the default
        headers = dump_mcp_config(conversation.agent.mcp_config)["test-server"][
            "headers"
        ]
        assert isinstance(headers, dict)
        auth_header = headers["Authorization"]

        # This assertion will FAIL with double-expansion bug
        assert auth_header == "Bearer my-actual-secret", (
            f"Expected secret value 'Bearer my-actual-secret', got '{auth_header}'. "
            "This is likely due to double-expansion: the default value was applied "
            "during plugin loading before secrets were available."
        )

        conversation.close()

    def test_plugin_mcp_secrets_fallback_to_default_when_no_secret(
        self, tmp_path: Path, basic_agent
    ):
        """Test that default values work when no secret is provided.

        This test verifies that ${VAR:-default} correctly falls back to the
        default value when no secret is injected.
        """
        # Inject an MCP provider to avoid actually starting MCP servers
        mcp_tools_created = []
        mcp_tool_provider = RecordingMCPToolProvider(mcp_tools_created)

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
            mcp_tool_provider=mcp_tool_provider,
        )

        # Do NOT inject any secrets - should use defaults

        # Trigger plugin loading and agent initialization
        conversation._ensure_agent_ready()

        # Verify defaults were used
        mcp_config = dump_mcp_config(conversation.agent.mcp_config)
        url = mcp_config["test-server"]["url"]
        headers = mcp_config["test-server"]["headers"]
        assert isinstance(headers, dict)
        header = headers["X-Custom-Header"]

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


class TestAmbientPluginAutoLoad:
    """Ambient auto-load: enabled installed + local plugins load into a
    conversation alongside (and below) the explicit-attach path.
    """

    def _isolate(self, monkeypatch, user_dirs: list[Path], install_store: Path):
        """Point discovery at test directories instead of the real home."""
        monkeypatch.setattr(discovery, "USER_PLUGINS_DIRS", user_dirs)
        monkeypatch.setattr(installed, "DEFAULT_INSTALLED_PLUGINS_DIR", install_store)

    def test_enabled_installed_plugin_auto_loads_into_conversation(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """An installed + enabled plugin loads with no explicit attach.

        The plugin contributes only a skill (no MCP / no explicit specs), so this
        also covers that a skills-only ambient plugin still updates the agent.
        """
        install_store = tmp_path / "installed-store"
        install_store.mkdir()
        source = create_test_plugin(
            tmp_path / "src",
            name="ambient-plugin",
            skills=[{"name": "ambient-skill", "content": "Ambient content"}],
        )
        install_plugin(str(source), installed_dir=install_store)
        self._isolate(monkeypatch, [tmp_path / "empty-user"], install_store)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent, workspace=workspace, visualizer=None
        )
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "ambient-skill" in skill_names
        conversation.close()

    def test_explicitly_attached_plugin_overrides_ambient_plugin(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """A same-named explicit plugin wins; the ambient one is skipped entirely."""
        user_dir = tmp_path / ".agents" / "plugins"
        create_test_plugin(
            user_dir / "shared",
            name="shared",
            skills=[{"name": "ambient-skill", "content": "Ambient"}],
        )
        explicit_src = create_test_plugin(
            tmp_path / "explicit",
            name="shared",
            skills=[{"name": "explicit-skill", "content": "Explicit"}],
        )
        install_store = tmp_path / "installed-store"
        install_store.mkdir()
        self._isolate(monkeypatch, [user_dir], install_store)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(explicit_src))],
            visualizer=None,
        )
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "explicit-skill" in skill_names
        assert "ambient-skill" not in skill_names
        conversation.close()

    def test_ambient_plugins_are_not_recorded_in_resolved_plugins(
        self, tmp_path: Path, basic_agent, monkeypatch
    ):
        """Ambient plugins load but are not pinned (resume re-discovers them)."""
        user_dir = tmp_path / ".agents" / "plugins"
        create_test_plugin(
            user_dir / "ambient",
            name="ambient-plugin",
            skills=[{"name": "ambient-skill", "content": "Ambient"}],
        )
        install_store = tmp_path / "installed-store"
        install_store.mkdir()
        self._isolate(monkeypatch, [user_dir], install_store)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        conversation = LocalConversation(
            agent=basic_agent, workspace=workspace, visualizer=None
        )
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "ambient-skill" in skill_names
        assert conversation.resolved_plugins is None
        conversation.close()

    def test_plugin_load_log_never_leaks_credentials(
        self, tmp_path: Path, basic_agent, caplog: pytest.LogCaptureFixture
    ):
        """Plugin-load logs must never contain the source credential. A serializer
        covers model dumps, not f-string log lines, so this guards against anyone
        re-adding spec.source to that log (issue #3752)."""
        plugin_dir = create_test_plugin(
            tmp_path / "plugin",
            name="test-plugin",
            skills=[{"name": "s", "content": "c"}],
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        conversation = LocalConversation(
            agent=basic_agent,
            workspace=workspace,
            plugins=[
                PluginSource(source="https://oauth2:LEAKME@host.example.com/o/r.git")
            ],
            visualizer=None,
        )
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "openhands.sdk.conversation.impl.local_conversation."
                "fetch_plugin_with_resolution",
                return_value=(plugin_dir, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"),
            ),
        ):
            conversation._ensure_plugins_loaded()

        assert "LEAKME" not in caplog.text
        conversation.close()
