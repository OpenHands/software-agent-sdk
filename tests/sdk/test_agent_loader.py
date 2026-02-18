"""Tests for file-based agent loading and registration."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.agent.agent_loader import (
    agent_definition_to_factory,
    load_project_agents,
    load_user_agents,
    register_file_agents,
    register_plugin_agents,
)
from openhands.sdk.plugin.types import AgentDefinition
from openhands.tools.delegate.registration import (
    _reset_registry_for_tests,
    get_agent_factory,
    register_agent,
)


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


# --- agent_definition_to_factory ---


def test_agent_definition_to_factory_basic() -> None:
    """Factory creates Agent with correct tools, skills, and LLM."""
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
    assert len(agent.agent_context.skills) == 1
    assert agent.agent_context.skills[0].name == "test-agent_prompt"
    assert agent.agent_context.skills[0].content == "You are a test agent."
    assert agent.agent_context.skills[0].trigger is None


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


# --- load_project_agents ---


def test_load_project_agents(tmp_path: Path) -> None:
    """Loads .md files from .agents/ root directory."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()

    (agents_dir / "code-reviewer.md").write_text(
        "---\n"
        "name: code-reviewer\n"
        "description: Reviews code\n"
        "tools:\n"
        "  - ReadTool\n"
        "---\n\n"
        "You are a code reviewer."
    )
    (agents_dir / "security-expert.md").write_text(
        "---\n"
        "name: security-expert\n"
        "description: Security analysis\n"
        "---\n\n"
        "You are a security expert."
    )

    agents = load_project_agents(tmp_path)
    names = {a.name for a in agents}
    assert names == {"code-reviewer", "security-expert"}

    # Verify the code-reviewer was parsed correctly
    reviewer = next(a for a in agents if a.name == "code-reviewer")
    assert reviewer.description == "Reviews code"
    assert "ReadTool" in reviewer.tools
    assert reviewer.system_prompt == "You are a code reviewer."


def test_load_project_agents_skips_subdirs(tmp_path: Path) -> None:
    """Does not recurse into subdirectories like skills/."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()

    # Top-level agent
    (agents_dir / "top-agent.md").write_text(
        "---\nname: top-agent\ndescription: Top\n---\nPrompt."
    )

    # Subdirectory (should be skipped)
    skills_dir = agents_dir / "skills"
    skills_dir.mkdir()
    (skills_dir / "nested-agent.md").write_text(
        "---\nname: nested-agent\ndescription: Nested\n---\nPrompt."
    )

    agents = load_project_agents(tmp_path)
    names = {a.name for a in agents}
    assert names == {"top-agent"}
    assert "nested-agent" not in names


def test_load_project_agents_empty(tmp_path: Path) -> None:
    """Returns [] for missing .agents/ directory."""
    agents = load_project_agents(tmp_path)
    assert agents == []


def test_load_project_agents_skips_readme(tmp_path: Path) -> None:
    """README.md is skipped."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()

    (agents_dir / "README.md").write_text("# Agents directory")
    (agents_dir / "real-agent.md").write_text(
        "---\nname: real-agent\ndescription: Real\n---\nPrompt."
    )

    agents = load_project_agents(tmp_path)
    names = {a.name for a in agents}
    assert names == {"real-agent"}


def test_load_project_agents_allowed_commands(tmp_path: Path) -> None:
    """Parses allowed_commands from frontmatter."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()

    (agents_dir / "runner.md").write_text(
        "---\n"
        "name: runner\n"
        "description: Runs commands\n"
        "allowed-commands:\n"
        "  - pytest\n"
        "  - make\n"
        "---\n\n"
        "You run commands."
    )

    agents = load_project_agents(tmp_path)
    assert len(agents) == 1
    assert agents[0].allowed_commands == ["pytest", "make"]


# --- load_user_agents ---


def test_load_user_agents(tmp_path: Path) -> None:
    """Loads from ~/.agents/ directory."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()

    (agents_dir / "global-agent.md").write_text(
        "---\nname: global-agent\ndescription: Global\n---\nGlobal prompt."
    )

    with patch("openhands.sdk.agent_loader.Path.home", return_value=tmp_path):
        agents = load_user_agents()

    assert len(agents) == 1
    assert agents[0].name == "global-agent"


# --- register_file_agents ---


def test_register_file_agents_project_priority(tmp_path: Path) -> None:
    """Project-level agents take priority over user-level agents with same name."""
    # Project .agents/
    project_agents_dir = tmp_path / ".agents"
    project_agents_dir.mkdir()
    (project_agents_dir / "shared-agent.md").write_text(
        "---\nname: shared-agent\ndescription: Project version\n---\n\nProject prompt."
    )

    # User ~/.agents/ (using a separate temp dir)
    user_home = tmp_path / "fake_home"
    user_home.mkdir()
    user_agents_dir = user_home / ".agents"
    user_agents_dir.mkdir()
    (user_agents_dir / "shared-agent.md").write_text(
        "---\nname: shared-agent\ndescription: User version\n---\n\nUser prompt."
    )

    with patch("openhands.sdk.agent_loader.Path.home", return_value=user_home):
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
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir()
    (agents_dir / "existing-agent.md").write_text(
        "---\nname: existing-agent\ndescription: File version\n---\n\nFile prompt."
    )

    with patch(
        "openhands.sdk.agent_loader.Path.home", return_value=tmp_path / "no_user"
    ):
        registered = register_file_agents(tmp_path)

    # File agent should NOT have been registered (programmatic wins)
    assert "existing-agent" not in registered
    # Verify the programmatic version is still there
    factory = get_agent_factory("existing-agent")
    assert factory.description == "Programmatic version"


# --- register_plugin_agents ---


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
