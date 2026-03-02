from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
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
    assert factory.description == "Project version"


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
    assert factory.description == "Programmatic version"


def test_register_plugin_agents() -> None:
    """Plugin agents are registered via register_agent_if_absent."""
    plugin_agent = AgentDefinition(
        name="plugin-agent",
        description="From plugin",
        model="inherit",
        tools=["ReadTool"],
        system_prompt="Plugin prompt.",
    )

    registered = register_plugin_agents([plugin_agent])

    assert registered == ["plugin-agent"]
    factory = get_agent_factory("plugin-agent")
    assert factory.description == "From plugin"


def test_register_plugin_agents_skips_existing() -> None:
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

    registered = register_plugin_agents([plugin_agent])
    assert registered == []
    # Programmatic version still there
    factory = get_agent_factory("my-agent")
    assert factory.description == "Programmatic"


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
    assert factory.description == "First"


def test_agent_definition_to_factory_basic() -> None:
    """Factory creates Agent with correct tools, system prompt, and LLM."""
    agent_def = AgentDefinition(
        name="test-agent",
        description="A test agent",
        model="inherit",
        tools=["ReadTool", "GlobTool"],
        system_prompt="You are a test agent.",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert isinstance(agent, Agent)
    # Check tools
    tool_names = [t.name for t in agent.tools]
    assert "ReadTool" in tool_names
    assert "GlobTool" in tool_names
    # Check skill (system prompt as always-active skill)
    assert agent.agent_context is not None
    assert agent.agent_context.system_message_suffix == "You are a test agent."


def test_agent_definition_to_factory_model_inherit() -> None:
    """Model 'inherit' preserves the parent LLM without modification."""
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

    # LLM should be the same instance (not copied)
    assert agent.llm is llm
    assert agent.llm.model == "gpt-4o"


def test_agent_definition_to_factory_model_override() -> None:
    """Non-inherit model creates a copy with the new model name."""
    agent_def = AgentDefinition(
        name="override-agent",
        description="Uses specific model",
        model="claude-sonnet-4-20250514",
        tools=[],
        system_prompt="Test prompt.",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    # LLM should be a different instance with the overridden model
    assert agent.llm is not llm
    assert agent.llm.model == "claude-sonnet-4-20250514"


def test_agent_definition_to_factory_no_system_prompt() -> None:
    """Factory with empty system prompt creates agent without agent_context."""
    agent_def = AgentDefinition(
        name="no-prompt-agent",
        description="No prompt",
        model="inherit",
        tools=["ReadTool"],
        system_prompt="",
    )

    factory = agent_definition_to_factory(agent_def)
    llm = _make_test_llm()
    agent = factory(llm)

    assert agent.agent_context is None


def test_factory_info() -> None:
    """get_factory_info returns formatted listing of registered agents."""
    info = get_factory_info()
    assert "default" in info
    assert "No user-registered agents" in info

    # Register some agents
    def factory_a(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    def factory_b(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(name="alpha-agent", factory_func=factory_a, description="Alpha desc")
    register_agent(name="beta-agent", factory_func=factory_b, description="Beta desc")

    info = get_factory_info()
    assert "default" in info
    assert "No user-registered agents" not in info
    assert "**alpha-agent**: Alpha desc" in info
    assert "**beta-agent**: Beta desc" in info
    # Verify alphabetical ordering: alpha before beta
    assert info.index("alpha-agent") < info.index("beta-agent")


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
    assert factory.description == "Custom agent for testing"
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
    assert factory.description == "New agent"


def test_end_to_end_md_to_factory_to_registry(tmp_path: Path) -> None:
    """End-to-end: .md file -> AgentDefinition.load() -> factory -> register -> get."""
    md_file = tmp_path / "test-agent.md"
    md_file.write_text(
        "---\n"
        "name: e2e-test-agent\n"
        "description: End-to-end test agent\n"
        "model: inherit\n"
        "tools:\n"
        "  - ReadTool\n"
        "  - GrepTool\n"
        "---\n\n"
        "You are a test agent for end-to-end testing.\n"
        "Focus on correctness and clarity.\n"
    )

    # Load from file
    agent_def = AgentDefinition.load(md_file)
    assert agent_def.name == "e2e-test-agent"
    assert agent_def.description == "End-to-end test agent"
    assert agent_def.tools == ["ReadTool", "GrepTool"]

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
    assert retrieved.description == "End-to-end test agent"

    # Create agent from factory (with real LLM)
    test_llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )
    agent = retrieved.factory_func(test_llm)
    assert isinstance(agent, Agent)
    tool_names = [t.name for t in agent.tools]
    assert "ReadTool" in tool_names
    assert "GrepTool" in tool_names
