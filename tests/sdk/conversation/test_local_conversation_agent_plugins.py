"""End to end: an Agent Plugins package driven to a configured Conversation.

The mirror of ``test_local_conversation_plugins.py`` for the portable format.
It matters that these assertions run here and not only on ``Plugin``:
``LocalConversation`` merges plugins itself and never calls ``load_plugins()``,
so this is the path a user actually hits.

Packages come from ``tests/fixtures/plugins/agent_plugins/`` -- a local
directory source, so no plugin is fetched and no network is touched.
"""

from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.plugin import PluginSource
from openhands.sdk.plugin.format.agent_plugins_mcp import get_plugin_data_dir


FIXTURES = Path(__file__).parents[2] / "fixtures" / "plugins" / "agent_plugins"


def fixture(name: str) -> Path:
    return (FIXTURES / name).resolve()


class EmptyMCPClient:
    def __init__(self):
        self.tools = []
        self._tools_reconciled_callback: Any = None


class RecordingMCPToolProvider:
    """Records the MCP config that reaches tool creation, and starts nothing."""

    def __init__(self, created: list[dict[str, MCPServer]]):
        self.created = created
        self.client = EmptyMCPClient()

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: Any = None,
        on_tools_reconciled: Any = None,
    ) -> MCPClient:
        self.created.append(mcp_config)
        self.client._tools_reconciled_callback = on_tools_reconciled
        return cast(MCPClient, self.client)


@pytest.fixture(autouse=True)
def plugin_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep ``PLUGIN_DATA`` out of the real user directory."""
    root = tmp_path / "plugin-data"
    monkeypatch.setattr(
        "openhands.sdk.plugin.format.agent_plugins_mcp.DEFAULT_PLUGIN_DATA_DIR", root
    )
    return root


@pytest.fixture(autouse=True)
def no_ambient_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load only the attached package, whatever the developer has installed."""
    monkeypatch.setattr(
        "openhands.sdk.conversation.impl.local_conversation.load_available_plugins",
        lambda **kwargs: {},
    )


@pytest.fixture
def agent() -> Agent:
    return Agent(llm=LLM(model="test/model", api_key=SecretStr("test-key")), tools=[])


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def conversation_with(
    package: str, agent: Agent, workspace: Path, **kwargs
) -> LocalConversation:
    return LocalConversation(
        agent=agent,
        workspace=workspace,
        plugins=[PluginSource(source=str(fixture(package)))],
        visualizer=None,
        **kwargs,
    )


def env_of(server: MCPServer) -> dict[str, str]:
    return {key: value.get_secret_value() for key, value in (server.env or {}).items()}


class TestSkills:
    """Skills from the portable layout reach the agent context."""

    def test_skills_reach_the_agent_context(self, agent: Agent, workspace: Path):
        conversation = conversation_with("full-package", agent, workspace)

        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        names = {s.name for s in conversation.agent.agent_context.skills}
        assert {"greet", "summarize"} <= names
        conversation.close()

    def test_the_entry_command_arrives_as_a_skill(self, agent: Agent, workspace: Path):
        """Our extension's commands become keyword-triggered skills."""
        conversation = conversation_with("full-package", agent, workspace)

        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        skills = {s.name: s for s in conversation.agent.agent_context.skills}
        assert "full-package:report" in skills
        assert skills["full-package:report"].trigger is not None
        conversation.close()

    def test_the_vendored_example_package_works(self, agent: Agent, workspace: Path):
        """The official example plugin, end to end, from vendored bytes."""
        conversation = conversation_with("agent-plugins-example", agent, workspace)

        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        names = {s.name for s in conversation.agent.agent_context.skills}
        assert "migrate-agent-plugin" in names
        assert conversation.resolved_plugins is not None
        assert len(conversation.resolved_plugins) == 1
        conversation.close()

    def test_the_conversation_factory_takes_the_same_path(
        self, agent: Agent, workspace: Path
    ):
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            plugins=[PluginSource(source=str(fixture("full-package")))],
            visualizer=None,
        )

        assert isinstance(conversation, LocalConversation)
        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        names = {s.name for s in conversation.agent.agent_context.skills}
        assert {"greet", "summarize"} <= names
        conversation.close()

    def test_a_manifest_only_package_configures_an_agent(
        self, agent: Agent, workspace: Path
    ):
        """§6.2: absent component locations are not an error at this level either."""
        conversation = conversation_with("manifest-only", agent, workspace)

        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        assert conversation.agent.agent_context.skills == []
        assert conversation.agent.mcp_config == {}
        conversation.close()


class TestMCPServers:
    """``mcp.json`` servers reach actual tool creation, fully expanded."""

    def test_servers_reach_tool_creation(self, agent: Agent, workspace: Path):
        created: list[dict[str, MCPServer]] = []
        conversation = conversation_with(
            "full-package",
            agent,
            workspace,
            mcp_tool_provider=RecordingMCPToolProvider(created),
        )
        assert conversation.agent.mcp_config == {}

        conversation._ensure_agent_ready()

        assert set(conversation.agent.mcp_config) == {"local-tools", "remote-api"}
        assert created, "plugin MCP servers never reached create_tools()"
        assert set(created[-1]) == {"local-tools", "remote-api"}
        conversation.close()

    def test_placeholders_are_expanded_on_the_constructed_server(
        self, agent: Agent, workspace: Path, plugin_data_root: Path
    ):
        created: list[dict[str, MCPServer]] = []
        conversation = conversation_with(
            "full-package",
            agent,
            workspace,
            mcp_tool_provider=RecordingMCPToolProvider(created),
        )

        conversation._ensure_agent_ready()

        root = str(fixture("full-package"))
        data = str(
            get_plugin_data_dir(fixture("full-package"), data_root=plugin_data_root)
        )
        server = created[-1]["local-tools"]
        assert server.args == ["--root", root, "--cache", f"{data}/cache"]
        assert env_of(server)["TOOLS_STATE"] == f"{data}/state"
        assert server.cwd == f"{data}/work"
        assert env_of(server)["PLUGIN_ROOT"] == root
        assert env_of(server)["PLUGIN_DATA"] == data
        conversation.close()

    def test_the_declared_transport_reaches_the_tool_provider(
        self, agent: Agent, workspace: Path
    ):
        """§7.2.1: the transport the entry declares is the one tools connect with."""
        created: list[dict[str, MCPServer]] = []
        conversation = conversation_with(
            "full-package",
            agent,
            workspace,
            mcp_tool_provider=RecordingMCPToolProvider(created),
        )

        conversation._ensure_agent_ready()

        assert created[-1]["local-tools"].transport == "stdio"
        assert created[-1]["remote-api"].transport == "streamable-http"
        conversation.close()

    def test_placeholders_are_left_untouched_elsewhere(
        self, agent: Agent, workspace: Path
    ):
        """Command, env keys, URL and headers carry no expansion (§9.2).

        The configured ``Host`` is gone by this point: the client generates that
        one itself, so it never reaches the server that gets built.
        """
        created: list[dict[str, MCPServer]] = []
        conversation = conversation_with(
            "full-package",
            agent,
            workspace,
            mcp_tool_provider=RecordingMCPToolProvider(created),
        )

        conversation._ensure_agent_ready()

        stdio = created[-1]["local-tools"]
        remote = created[-1]["remote-api"]
        assert stdio.command == str(fixture("full-package") / "bin" / "serve.sh")
        assert "${PLUGIN_ROOT}" in env_of(stdio)
        assert remote.url == "https://api.example.com/mcp?root=${PLUGIN_ROOT}"
        assert remote.headers is not None
        assert {
            key: value.get_secret_value() for key, value in remote.headers.items()
        } == {
            "Authorization": "Bearer ${PLUGIN_DATA}",
            "X-Plugin-Root": "${PLUGIN_ROOT}",
        }
        conversation.close()


class TestClientExtension:
    """``dev.openhands`` components are applied to the conversation."""

    def test_hooks_reach_the_hook_processor(self, agent: Agent, workspace: Path):
        conversation = conversation_with("full-package", agent, workspace)
        processor = MagicMock()
        processor.on_event = MagicMock()

        with patch(
            "openhands.sdk.conversation.impl.local_conversation.create_hook_callback",
            return_value=(processor, processor.on_event),
        ) as create_hook_callback:
            conversation._ensure_plugins_loaded()

        hook_config = create_hook_callback.call_args.kwargs["hook_config"]
        assert hook_config is not None
        assert [matcher.matcher for matcher in hook_config.pre_tool_use] == [
            "full-package-*"
        ]
        conversation.close()

    def test_agents_are_registered(self, agent: Agent, workspace: Path):
        conversation = conversation_with("full-package", agent, workspace)

        with patch(
            "openhands.sdk.conversation.impl.local_conversation.register_plugin_agents"
        ) as register:
            conversation._ensure_plugins_loaded()

        registered = register.call_args.kwargs["agents"]
        assert [definition.name for definition in registered] == ["full-package-helper"]
        conversation.close()


class TestFailureBoundaries:
    """A package's broken parts stay broken alone, or stop the plugin entirely."""

    def test_broken_entries_do_not_stop_the_conversation(
        self, agent: Agent, workspace: Path
    ):
        created: list[dict[str, MCPServer]] = []
        conversation = conversation_with(
            "partial-failures",
            agent,
            workspace,
            mcp_tool_provider=RecordingMCPToolProvider(created),
        )

        conversation._ensure_agent_ready()

        assert conversation.agent.agent_context is not None
        assert [s.name for s in conversation.agent.agent_context.skills] == ["good"]
        assert set(conversation.agent.mcp_config) == {"good-server"}
        assert set(created[-1]) == {"good-server"}
        conversation.close()

    @pytest.mark.parametrize(
        "package, message",
        [
            ("fatal-manifest", "Invalid Agent Plugins manifest"),
            ("unsupported-schema", r"Unsupported or missing \$schema"),
        ],
    )
    def test_a_rejected_package_stops_the_conversation(
        self, agent: Agent, workspace: Path, package: str, message: str
    ):
        """A rejected plugin is not silently dropped: attaching it is an error."""
        conversation = conversation_with(package, agent, workspace)

        with pytest.raises(ValueError, match=message):
            conversation._ensure_plugins_loaded()

        assert conversation.agent.mcp_config == {}
        conversation.close()

    def test_a_disabled_mcp_component_still_yields_skills(
        self, agent: Agent, workspace: Path
    ):
        """An ``mcp.json`` targeting another version disables MCP, not the plugin."""
        created: list[dict[str, MCPServer]] = []
        conversation = conversation_with(
            "mcp-version-mismatch",
            agent,
            workspace,
            mcp_tool_provider=RecordingMCPToolProvider(created),
        )

        conversation._ensure_agent_ready()

        assert conversation.agent.agent_context is not None
        assert [s.name for s in conversation.agent.agent_context.skills] == [
            "still-here"
        ]
        assert conversation.agent.mcp_config == {}
        conversation.close()

    def test_components_outside_the_fixed_locations_never_reach_the_agent(
        self, agent: Agent, workspace: Path
    ):
        """The Claude Code layout at the root contributes nothing here."""
        conversation = conversation_with("wrong-locations", agent, workspace)

        with patch(
            "openhands.sdk.conversation.impl.local_conversation.register_plugin_agents"
        ) as register:
            conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        assert [s.name for s in conversation.agent.agent_context.skills] == ["only"]
        assert conversation.agent.mcp_config == {}
        register.assert_not_called()
        conversation.close()

    def test_non_fatal_manifest_violations_still_configure_the_agent(
        self, agent: Agent, workspace: Path
    ):
        conversation = conversation_with("non-fatal-manifest", agent, workspace)

        conversation._ensure_plugins_loaded()

        assert conversation.agent.agent_context is not None
        assert [s.name for s in conversation.agent.agent_context.skills] == ["ok"]
        conversation.close()
