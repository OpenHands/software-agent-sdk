from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.hooks.config import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    agent_definition_to_factory,
    get_agent_factory,
    get_factory_info,
    register_agent,
    register_agent_if_absent,
    register_file_agents,
    register_plugin_agents,
)
from openhands.sdk.subagent.schema import AgentDefinition


def setup_function() -> None:
    _reset_registry_for_tests()


def teardown_function() -> None:
    _reset_registry_for_tests()


def _make_test_llm() -> LLM:
    """Create a real LLM instance for testing."""
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )


def _create_skill_file(skills_dir: Path, name: str, content: str) -> None:
    """Create a skill .md file in the given skills directory."""
    skill_file = skills_dir / f"{name}.md"
    skill_file.write_text(
        f"---\nname: {name}\ntriggers:\n  - {name}\n---\n\n{content}\n"
    )


def test_register_file_agents_project_priority(tmp_path: Path) -> None:
    """Project-level agents take priority over user-level agents with same name."""
    # Project .agents/
    project_agents_dir = tmp_path / ".agents" / "agents"
    project_agents_dir.mkdir(parents=True)
    (project_agents_dir / "shared-agent.md").write_text(
        "---\nname: shared-agent\ndescription: Project version\n---\n\nProject prompt."
    )

    # User ~/.agents/ (using a separate temp dir)
    user_home = tmp_path / "fake_home"
    user_home.mkdir(parents=True)
    user_agents_dir = user_home / ".agents" / "agents"
    user_agents_dir.mkdir(parents=True)
    (user_agents_dir / "shared-agent.md").write_text(
        "---\nname: shared-agent\ndescription: User version\n---\n\nUser prompt."
    )

    with patch("openhands.sdk.subagent.load.Path.home", return_value=user_home):
        registered = register_file_agents(tmp_path)

    assert "shared-agent" in registered
    # Verify the project version won
    factory = get_agent_factory("shared-agent")
    assert factory.definition.description == "Project version"


def test_register_file_agents_skips_programmatic(tmp_path: Path) -> None:
    """Does not overwrite agents registered programmatically."""

    # Register an agent programmatically first
    def existing_factory(llm: LLM) -> Agent:
        return cast(Agent, MagicMock())

    register_agent(
        name="existing-agent",
        factory_func=existing_factory,
        description="Programmatic version",
    )

    # Create file-based agent with same name
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "existing-agent.md").write_text(
        "---\nname: existing-agent\ndescription: File version\n---\n\nFile prompt."
    )

    with patch(
        "openhands.sdk.subagent.load.Path.home", return_value=tmp_path / "no_user"
    ):
        registered = register_file_agents(tmp_path)

    # File agent should NOT have been registered (programmatic wins)
    assert "existing-agent" not in registered
    # Verify the programmatic version is still there
    factory = get_agent_factory("existing-agent")
    assert factory.definition.description == "Programmatic version"


def test_register_plugin_agents(tmp_path: Path) -> None:
    """Plugin agents are registered via register_agent_if_absent."""
    plugin_agent = AgentDefinition(
        name="plugin-agent",
        description="From plugin",
        model="inherit",
        tools=["ReadTool"],
        system_prompt="Plugin prompt.",
    )

    registered = register_plugin_agents([plugin_agent], work_dir=tmp_path)

    assert registered == ["plugin-agent"]
    factory = get_agent_factory("plugin-agent")
    assert factory.definition.description == "From plugin"


def test_register_plugin_agents_skips_existing(tmp_path: Path) -> None:
    """Plugin agents don't overwrite programmatically registered agents."""

    def existing_factory(llm: LLM) -> Agent:
        return cast(Agent, MagicMock())

    register_agent(
        name="my-agent",
        factory_func=existing_factory,
        description="Programmatic",
    )

    plugin_agent = AgentDefinition(
        name="my-agent",
        description="Plugin version",
        model="inherit",
        tools=[],
        system_prompt="",
    )

    registered = register_plugin_agents([plugin_agent], work_dir=tmp_path)
    assert registered == []
    # Programmatic version still there
    factory = get_agent_factory("my-agent")
    assert factory.definition.description == "Programmatic"


def test_register_agent_if_absent_existing() -> None:
    """register_agent_if_absent returns False for existing agents."""

    def factory1(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    def factory2(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(name="dup_agent", factory_func=factory1, description="First")

    result = register_agent_if_absent(
        name="dup_agent",
        factory_func=factory2,
        description="Second",
    )
    assert result is False

    # First registration should be preserved
    factory = get_agent_factory("dup_agent")
    assert factory.definition.description == "First"


def test_agent_definition_to_factory_basic() -> None:
    """Factory creates Agent with correct tools, system prompt, and LLM."""
    agent_def = AgentDefinition(
        name="test-agent",
        description="A test agent",
        model="inherit",
        tools=[],
        system_prompt="You are a test agent.",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert isinstance(agent, Agent)
    # Check tools are empty
    assert agent.tools == []
    # Check skill (system prompt as always-active skill)
    assert agent.agent_context is not None
    assert agent.agent_context.system_message_suffix == "You are a test agent."


def test_agent_definition_to_factory_model_inherit() -> None:
    """Model 'inherit' preserves the parent LLM."""
    agent_def = AgentDefinition(
        name="inherit-agent",
        description="Uses parent model",
        model="inherit",
        tools=[],
        system_prompt="Test prompt.",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.llm is llm
    assert agent.llm.model == "gpt-4o"


def test_agent_definition_to_factory_model_override() -> None:
    """Non-inherit model that isn't a stored profile raises ValueError."""
    agent_def = AgentDefinition(
        name="override-agent",
        description="Uses specific model",
        model="claude-sonnet-4-20250514",
        tools=[],
        system_prompt="Test prompt.",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()

    with pytest.raises(ValueError, match="not found in profile store"):
        factory(llm)


def test_agent_definition_to_factory_no_system_prompt() -> None:
    """Factory with empty system prompt creates agent without agent_context."""
    agent_def = AgentDefinition(
        name="no-prompt-agent",
        description="No prompt",
        model="inherit",
        system_prompt="",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.agent_context is None


def test_agent_definition_to_factory_with_skills(tmp_path: Path) -> None:
    """Factory resolves skill names and passes them to AgentContext."""
    # Create a skill file in project directory
    skills_dir = tmp_path / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    _create_skill_file(skills_dir, "test-skill", "Skill content here.")

    agent_def = AgentDefinition(
        name="skilled-agent",
        description="Agent with skills",
        model="inherit",
        tools=[],
        skills=["test-skill"],
        system_prompt="You are a skilled agent.",
    )

    factory = agent_definition_to_factory(agent_def, work_dir=tmp_path)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.agent_context is not None
    assert len(agent.agent_context.skills) == 1
    assert agent.agent_context.skills[0].name == "test-skill"
    assert "Skill content here." in agent.agent_context.skills[0].content
    assert agent.agent_context.system_message_suffix == "You are a skilled agent."


def test_agent_definition_to_factory_skills_only_no_prompt(tmp_path: Path) -> None:
    """Factory with skills but no system prompt still creates AgentContext."""
    skills_dir = tmp_path / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    _create_skill_file(skills_dir, "only-skill", "Only skill content.")

    agent_def = AgentDefinition(
        name="skills-only-agent",
        description="Agent with skills but no prompt",
        model="inherit",
        tools=[],
        skills=["only-skill"],
        system_prompt="",
    )

    factory = agent_definition_to_factory(agent_def, work_dir=tmp_path)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.agent_context is not None
    assert len(agent.agent_context.skills) == 1
    assert agent.agent_context.skills[0].name == "only-skill"
    assert agent.agent_context.system_message_suffix is None


def test_agent_definition_to_factory_no_skills_no_prompt() -> None:
    """Factory with no skills and no prompt creates no AgentContext."""
    agent_def = AgentDefinition(
        name="empty-agent",
        description="No skills no prompt",
        model="inherit",
        tools=[],
        skills=[],
        system_prompt="",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.agent_context is None


def test_agent_definition_to_factory_skill_not_found() -> None:
    """Factory raises ValueError when a skill name is not found."""
    agent_def = AgentDefinition(
        name="missing-skill-agent",
        description="Agent with missing skill",
        model="inherit",
        skills=["nonexistent-skill"],
    )

    with pytest.raises(ValueError, match="Skill 'nonexistent-skill' not found"):
        agent_definition_to_factory(agent_def)


def test_agent_definition_to_factory_skills_project_over_user(tmp_path: Path) -> None:
    """Project skills take priority over user skills with the same name."""
    # Create project-level skill
    project_skills_dir = tmp_path / ".agents" / "skills"
    project_skills_dir.mkdir(parents=True)
    _create_skill_file(project_skills_dir, "shared-skill", "Project version.")

    # Create user-level skill with same name
    user_home = tmp_path / "fake_home"
    user_skills_dir = user_home / ".agents" / "skills"
    user_skills_dir.mkdir(parents=True)
    _create_skill_file(user_skills_dir, "shared-skill", "User version.")

    agent_def = AgentDefinition(
        name="priority-agent",
        skills=["shared-skill"],
    )

    with patch("openhands.sdk.context.skills.skill.Path.home", return_value=user_home):
        factory = agent_definition_to_factory(agent_def, work_dir=tmp_path)

    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.agent_context is not None
    assert len(agent.agent_context.skills) == 1
    # Project version should win
    assert "Project version." in agent.agent_context.skills[0].content


def test_factory_info() -> None:
    """get_factory_info returns formatted listing of registered agents."""
    info = get_factory_info()
    assert "No user-registered agents" in info

    # Register some agents
    def factory_a(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    def factory_b(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(name="alpha-agent", factory_func=factory_a, description="Alpha desc")
    register_agent(name="beta-agent", factory_func=factory_b, description="Beta desc")

    info = get_factory_info()
    assert "No user-registered agents" not in info
    assert "**alpha-agent**: Alpha desc" in info
    assert "**beta-agent**: Beta desc" in info
    # Verify alphabetical ordering: alpha before beta
    assert info.index("alpha-agent") < info.index("beta-agent")


def test_factory_info_mixed_tools_and_no_tools() -> None:
    """get_factory_info correctly shows tools only for agents that have them."""

    def dummy(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    agent_with = AgentDefinition(
        name="with-tools",
        description="Has tools",
        tools=["TerminalTool"],
    )
    agent_without = AgentDefinition(
        name="without-tools",
        description="No tools",
        tools=[],
    )
    register_agent(name="with-tools", factory_func=dummy, description=agent_with)
    register_agent(name="without-tools", factory_func=dummy, description=agent_without)

    info = get_factory_info()
    assert info == (
        "- **with-tools**: Has tools (tools: TerminalTool)\n"
        "- **without-tools**: No tools"
    )


def test_factory_info_single_agent() -> None:
    """get_factory_info works correctly with a single registered agent."""

    def dummy(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(name="solo-agent", factory_func=dummy, description="Only agent")

    info = get_factory_info()
    assert info == "- **solo-agent**: Only agent"


@pytest.mark.parametrize("name", [None, "", "default", "alpha"])
def test_error_default_factory_empty(name: str | None) -> None:
    """Ensure default agent factory is used when no type is provided."""
    with pytest.raises(ValueError, match=f"Unknown agent '{name}'"):
        _ = get_agent_factory(name)


def test_register_and_retrieve_custom_agent_factory() -> None:
    """User-registered agent factories should be retrievable by name."""

    def dummy_factory(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(
        name="custom_agent",
        factory_func=dummy_factory,
        description="Custom agent for testing",
    )

    factory = get_agent_factory("custom_agent")
    assert factory.definition.description == "Custom agent for testing"
    assert factory.factory_func is dummy_factory


def test_unknown_agent_type_raises_value_error() -> None:
    """Retrieving an unknown agent type should provide a helpful error."""
    with pytest.raises(ValueError) as excinfo:
        get_agent_factory("missing")

    assert "Unknown agent 'missing'" in str(excinfo.value)


def test_register_agent_if_absent_new() -> None:
    """register_agent_if_absent returns True for new agents."""

    def dummy_factory(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    result = register_agent_if_absent(
        name="new_agent",
        factory_func=dummy_factory,
        description="New agent",
    )
    assert result is True

    factory = get_agent_factory("new_agent")
    assert factory.definition.description == "New agent"


def test_agent_definition_to_factory_model_profile(tmp_path: Path) -> None:
    """Profile name loads a complete LLM from the profile store."""
    store = LLMProfileStore(base_dir=tmp_path)
    profile_llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("profile-key"),
        usage_id="profile-llm",
        temperature=0.3,
    )
    store.save("fast-gpt", profile_llm, include_secrets=True)

    agent_def = AgentDefinition(
        name="profile-agent",
        description="Uses a profile",
        model="fast-gpt",
        tools=[],
        system_prompt="Profile test.",
    )

    factory = agent_definition_to_factory(agent_def)
    parent_llm = _make_test_llm()
    with patch(
        "openhands.sdk.subagent.registry._get_profile_store", return_value=store
    ):
        agent = factory(parent_llm)

    # The agent's LLM should come from the profile, not the parent
    assert agent.llm is not parent_llm
    assert agent.llm.model == "claude-sonnet-4-20250514"
    assert agent.llm.temperature == 0.3
    assert agent.llm.stream is False
    # Metrics must be independent from the parent LLM
    assert agent.llm.metrics is not parent_llm.metrics


def test_agent_definition_to_factory_model_profile_with_json_suffix(
    tmp_path: Path,
) -> None:
    """Profile name with .json suffix is accepted and loads correctly."""
    store = LLMProfileStore(base_dir=tmp_path)
    profile_llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("profile-key"),
        usage_id="profile-llm",
        temperature=0.3,
    )
    store.save("fast-gpt", profile_llm, include_secrets=True)

    agent_def = AgentDefinition(
        name="profile-agent",
        description="Uses a profile with .json suffix",
        model="fast-gpt.json",
        tools=[],
        system_prompt="Profile test.",
    )

    factory = agent_definition_to_factory(agent_def)
    parent_llm = _make_test_llm()
    with patch(
        "openhands.sdk.subagent.registry._get_profile_store", return_value=store
    ):
        agent = factory(parent_llm)

    assert agent.llm is not parent_llm
    assert agent.llm.model == "claude-sonnet-4-20250514"
    assert agent.llm.temperature == 0.3


def test_agent_definition_to_factory_model_profile_not_found(tmp_path: Path) -> None:
    """Missing profile raises ValueError."""
    store = LLMProfileStore(base_dir=tmp_path)

    agent_def = AgentDefinition(
        name="missing-profile-agent",
        description="Profile does not exist",
        model="nonexistent.json",
        tools=[],
        system_prompt="",
    )

    factory = agent_definition_to_factory(agent_def)
    parent_llm = _make_test_llm()

    with patch(
        "openhands.sdk.subagent.registry._get_profile_store", return_value=store
    ):
        with pytest.raises(ValueError, match="nonexistent"):
            factory(parent_llm)


def test_agent_definition_to_factory_model_profile_custom_store(tmp_path: Path) -> None:
    """Patched profile store is used by the factory."""
    custom_store = LLMProfileStore(base_dir=tmp_path)
    profile_llm = LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("custom-store-key"),
        usage_id="custom-store-llm",
    )
    custom_store.save("my-profile", profile_llm, include_secrets=True)

    agent_def = AgentDefinition(
        name="custom-store-agent",
        description="Uses custom store",
        model="my-profile",
        tools=[],
        system_prompt="",
    )

    factory = agent_definition_to_factory(agent_def)
    parent_llm = _make_test_llm()
    with patch(
        "openhands.sdk.subagent.registry._get_profile_store", return_value=custom_store
    ):
        agent = factory(parent_llm)

    assert agent.llm.model == "gpt-4o-mini"
    assert agent.llm.stream is False
    # Metrics must be independent from the parent LLM
    assert agent.llm.metrics is not parent_llm.metrics


def test_agent_definition_to_factory_profile_store_dir(tmp_path: Path) -> None:
    """profile_store_dir on AgentDefinition is used by the factory."""
    store = LLMProfileStore(base_dir=tmp_path)
    profile_llm = LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("dir-key"),
        usage_id="dir-llm",
    )
    store.save("my-profile", profile_llm, include_secrets=True)
    agent_def = AgentDefinition(
        name="dir-agent",
        description="Uses profile_store_dir",
        model="my-profile",
        tools=[],
        system_prompt="",
        profile_store_dir=str(tmp_path),
    )

    factory = agent_definition_to_factory(agent_def)
    parent_llm = _make_test_llm()
    agent = factory(parent_llm)

    assert agent.llm.model == "gpt-4o-mini"


def test_agent_definition_to_factory_profile_store_dir_not_found(
    tmp_path: Path,
) -> None:
    """Missing profile in custom profile_store_dir raises ValueError."""
    agent_def = AgentDefinition(
        name="missing-dir-agent",
        model="nonexistent",
        tools=[],
        system_prompt="",
        profile_store_dir=str(tmp_path),
    )

    factory = agent_definition_to_factory(agent_def)
    parent_llm = _make_test_llm()

    with pytest.raises(ValueError, match="nonexistent"):
        factory(parent_llm)


def test_agent_definition_to_factory_profile_store_dir_none_uses_default(
    tmp_path: Path,
) -> None:
    """When profile_store_dir is None, the default cached store is used."""
    store = LLMProfileStore(base_dir=tmp_path)
    profile_llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("default-key"),
        usage_id="default-llm",
    )
    store.save("default-profile", profile_llm, include_secrets=True)

    agent_def = AgentDefinition(
        name="default-store-agent",
        model="default-profile",
        tools=[],
        system_prompt="",
        profile_store_dir=None,
    )

    factory = agent_definition_to_factory(agent_def)
    parent_llm = _make_test_llm()

    with patch(
        "openhands.sdk.subagent.registry._get_profile_store", return_value=store
    ):
        agent = factory(parent_llm)

    assert agent.llm.model == "claude-sonnet-4-20250514"


def test_register_agent_with_hook_config() -> None:
    """register_agent stores hook_config in the AgentFactory via AgentDefinition."""
    hook_config = HookConfig(
        pre_tool_use=[
            HookMatcher(
                matcher="terminal",
                hooks=[HookDefinition(command="./validate.sh")],
            )
        ]
    )

    def dummy_factory(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    agent_def = AgentDefinition(
        name="hooked-agent",
        description="Agent with hooks",
        hooks=hook_config,
    )

    register_agent(
        name="hooked-agent",
        factory_func=dummy_factory,
        description=agent_def,
    )

    factory = get_agent_factory("hooked-agent")
    assert factory.definition.hooks is not None
    assert len(factory.definition.hooks.pre_tool_use) == 1
    assert factory.definition.hooks.pre_tool_use[0].matcher == "terminal"


def test_register_agent_hook_config_defaults_to_none() -> None:
    """AgentFactory.hook_config defaults to None when not provided."""

    def dummy_factory(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(
        name="no-hooks-agent",
        factory_func=dummy_factory,
        description="Agent without hooks",
    )

    factory = get_agent_factory("no-hooks-agent")
    assert factory.definition.hooks is None


def test_register_file_agents_with_hooks(tmp_path: Path) -> None:
    """File-based agents with hooks have hook_config stored in the factory."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "hooked.md").write_text(
        "---\n"
        "name: hooked-file-agent\n"
        "description: File agent with hooks\n"
        "hooks:\n"
        "  pre_tool_use:\n"
        "    - matcher: '*'\n"
        "      hooks:\n"
        "        - command: ./log.sh\n"
        "---\n\n"
        "You are an agent with hooks.\n"
    )

    with patch(
        "openhands.sdk.subagent.load.Path.home", return_value=tmp_path / "no_user"
    ):
        registered = register_file_agents(tmp_path)

    assert "hooked-file-agent" in registered
    factory = get_agent_factory("hooked-file-agent")
    assert factory.definition.hooks is not None
    assert len(factory.definition.hooks.pre_tool_use) == 1


def test_register_plugin_agents_with_hooks() -> None:
    """Plugin agents with hooks have hook_config stored in the factory."""
    hook_config = HookConfig(
        stop=[
            HookMatcher(
                matcher="*",
                hooks=[HookDefinition(command="./check_stop.sh")],
            )
        ]
    )
    plugin_agent = AgentDefinition(
        name="plugin-hooked",
        description="Plugin agent with hooks",
        model="inherit",
        tools=[],
        system_prompt="Plugin prompt.",
        hooks=hook_config,
    )

    registered = register_plugin_agents([plugin_agent])
    assert "plugin-hooked" in registered

    factory = get_agent_factory("plugin-hooked")
    assert factory.definition.hooks is not None
    assert len(factory.definition.hooks.stop) == 1


def test_end_to_end_md_to_factory_to_registry(tmp_path: Path) -> None:
    """End-to-end: .md file -> AgentDefinition.load() -> factory -> register -> get."""
    md_file = tmp_path / "test-agent.md"
    md_file.write_text(
        "---\n"
        "name: e2e-test-agent\n"
        "description: End-to-end test agent\n"
        "model: inherit\n"
        "---\n\n"
        "You are a test agent for end-to-end testing.\n"
        "Focus on correctness and clarity.\n"
    )

    # Load from file
    agent_def = AgentDefinition.load(md_file)
    assert agent_def.name == "e2e-test-agent"
    assert agent_def.description == "End-to-end test agent"

    # Convert to factory
    factory = agent_definition_to_factory(agent_def)

    # Register
    result = register_agent_if_absent(
        name=agent_def.name,
        factory_func=factory,
        description=agent_def.description,
    )
    assert result is True

    # Retrieve and verify
    retrieved = get_agent_factory("e2e-test-agent")
    assert retrieved.definition.description == "End-to-end test agent"

    # Create agent from factory (with real LLM)
    test_llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )
    agent = retrieved.factory_func(test_llm)
    assert isinstance(agent, Agent)


def test_agent_definition_to_factory_mcp_servers() -> None:
    """Factory passes mcp_servers as mcp_config to the Agent."""
    agent_def = AgentDefinition(
        name="mcp-agent",
        description="Agent with MCP servers",
        model="inherit",
        tools=[],
        system_prompt="",
        mcp_servers={
            "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
        },
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.mcp_config == {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }


def test_agent_definition_to_factory_no_mcp_servers() -> None:
    """Factory without mcp_servers passes empty mcp_config."""
    agent_def = AgentDefinition(
        name="no-mcp-agent",
        model="inherit",
        tools=[],
        system_prompt="",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.mcp_config == {}


def test_register_file_agents_passes_mcp_config_to_agent(tmp_path: Path) -> None:
    """Integration: mcp_servers in markdown flows through registry to Agent."""
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "mcp-agent.md").write_text(
        "---\n"
        "name: mcp-agent\n"
        "description: Agent with MCP servers\n"
        "mcp_servers:\n"
        "  fetch:\n"
        "    command: uvx\n"
        "    args: [mcp-server-fetch]\n"
        "---\n\n"
        "Agent with MCP.\n"
    )

    with patch(
        "openhands.sdk.subagent.load.Path.home", return_value=tmp_path / "no_user"
    ):
        registered = register_file_agents(tmp_path)

    assert "mcp-agent" in registered

    factory = get_agent_factory("mcp-agent")
    llm = _make_test_llm()
    agent = factory.factory_func(llm)

    assert agent.mcp_config == {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }
