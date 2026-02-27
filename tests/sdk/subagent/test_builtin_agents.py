"""Tests for SDK built-in agent definitions (explore, bash, default)."""

from typing import cast
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.subagent.load import load_agents_from_dir
from openhands.sdk.subagent.registry import (
    BUILTINS_DIR,
    _reset_registry_for_tests,
    agent_definition_to_factory,
    get_agent_factory,
    register_agent,
    register_builtins_agents,
)
from openhands.sdk.subagent.schema import AgentDefinition


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the agent registry before and after every test."""
    _reset_registry_for_tests()
    yield  # type: ignore[misc]
    _reset_registry_for_tests()


def _make_test_llm() -> LLM:
    return LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")


def _load_builtin(name: str) -> AgentDefinition:
    """Load a single builtin agent definition by name."""
    md_path = BUILTINS_DIR / f"{name}.md"
    assert md_path.exists(), f"Builtin agent file not found: {md_path}"
    return AgentDefinition.load(md_path)


class TestBuiltinsDirectory:
    """Ensure the builtins directory exists and contains expected files."""

    def test_builtins_dir_exists(self) -> None:
        assert BUILTINS_DIR.is_dir()

    def test_builtins_contains_expected_agents(self) -> None:
        md_files = {f.stem for f in BUILTINS_DIR.glob("*.md")}
        assert "default" in md_files
        assert "explore" in md_files
        assert "bash" in md_files

    def test_load_all_builtins(self) -> None:
        """Every .md file in builtins/ should parse without errors."""
        agents = load_agents_from_dir(BUILTINS_DIR)
        names = {a.name for a in agents}
        assert "default" in names
        assert "explore" in names
        assert "bash" in names


class TestExploreAgent:
    """Verify the explore builtin agent definition."""

    @pytest.fixture()
    def agent_def(self) -> AgentDefinition:
        return _load_builtin("explore")

    def test_name(self, agent_def: AgentDefinition) -> None:
        assert agent_def.name == "explore"

    def test_description_present(self, agent_def: AgentDefinition) -> None:
        assert agent_def.description
        assert len(agent_def.description) > 10

    def test_tools_are_read_oriented(self, agent_def: AgentDefinition) -> None:
        """Explore agent should only have read-oriented tools."""
        # terminal is allowed for read-only commands
        assert "terminal" in agent_def.tools
        # Must NOT include write-oriented tools
        assert "file_editor" not in agent_def.tools
        assert "browser_tool_set" not in agent_def.tools

    def test_system_prompt_mentions_read_only(self, agent_def: AgentDefinition) -> None:
        assert agent_def.system_prompt
        prompt_lower = agent_def.system_prompt.lower()
        assert "read-only" in prompt_lower or "read only" in prompt_lower

    def test_system_prompt_prohibits_writes(self, agent_def: AgentDefinition) -> None:
        prompt_lower = agent_def.system_prompt.lower()
        assert "not" in prompt_lower and (
            "create" in prompt_lower
            or "modify" in prompt_lower
            or "delete" in prompt_lower
        )

    def test_model_inherits(self, agent_def: AgentDefinition) -> None:
        assert agent_def.model == "inherit"

    def test_has_when_to_use_examples(self, agent_def: AgentDefinition) -> None:
        assert len(agent_def.when_to_use_examples) > 0

    def test_factory_creates_agent(self, agent_def: AgentDefinition) -> None:
        factory = agent_definition_to_factory(agent_def)
        agent = factory(_make_test_llm())
        assert isinstance(agent, Agent)
        tool_names = [t.name for t in agent.tools]
        assert "terminal" in tool_names

    def test_factory_sets_system_prompt(self, agent_def: AgentDefinition) -> None:
        factory = agent_definition_to_factory(agent_def)
        agent = factory(_make_test_llm())
        assert agent.agent_context is not None
        system_msg = agent.agent_context.system_message_suffix
        assert system_msg is not None
        assert "read-only" in system_msg.lower()


class TestBashAgent:
    """Verify the bash builtin agent definition."""

    @pytest.fixture()
    def agent_def(self) -> AgentDefinition:
        return _load_builtin("bash")

    def test_name(self, agent_def: AgentDefinition) -> None:
        assert agent_def.name == "bash"

    def test_description_present(self, agent_def: AgentDefinition) -> None:
        assert agent_def.description
        assert len(agent_def.description) > 10

    def test_tools_terminal_only(self, agent_def: AgentDefinition) -> None:
        """Bash agent should only have the terminal tool."""
        assert agent_def.tools == ["terminal"]

    def test_system_prompt_mentions_command(self, agent_def: AgentDefinition) -> None:
        assert agent_def.system_prompt
        prompt_lower = agent_def.system_prompt.lower()
        assert "command" in prompt_lower or "terminal" in prompt_lower

    def test_model_inherits(self, agent_def: AgentDefinition) -> None:
        assert agent_def.model == "inherit"

    def test_has_when_to_use_examples(self, agent_def: AgentDefinition) -> None:
        assert len(agent_def.when_to_use_examples) > 0

    def test_factory_creates_agent(self, agent_def: AgentDefinition) -> None:
        factory = agent_definition_to_factory(agent_def)
        agent = factory(_make_test_llm())
        assert isinstance(agent, Agent)
        tool_names = [t.name for t in agent.tools]
        assert tool_names == ["terminal"]

    def test_factory_sets_system_prompt(self, agent_def: AgentDefinition) -> None:
        factory = agent_definition_to_factory(agent_def)
        agent = factory(_make_test_llm())
        agent_context = agent.agent_context
        assert agent_context is not None
        system_msg = agent_context.system_message_suffix
        assert system_msg is not None
        assert len(system_msg) > 0


class TestDefaultAgent:
    """Verify the default builtin agent definition."""

    @pytest.fixture()
    def agent_def(self) -> AgentDefinition:
        return _load_builtin("default")

    def test_name(self, agent_def: AgentDefinition) -> None:
        assert agent_def.name == "default"

    def test_tools(self, agent_def: AgentDefinition) -> None:
        assert "terminal" in agent_def.tools
        assert "file_editor" in agent_def.tools

    def test_model_inherits(self, agent_def: AgentDefinition) -> None:
        assert agent_def.model == "inherit"


class TestBuiltinRegistration:
    """Test that builtin agents register correctly via register_builtins_agents."""

    def test_register_builtins_agents_returns_all(self) -> None:
        registered = register_builtins_agents()
        assert "default" in registered
        assert "explore" in registered
        assert "bash" in registered

    def test_registered_agents_are_retrievable(self) -> None:
        register_builtins_agents()
        for name in ("default", "explore", "bash"):
            factory = get_agent_factory(name)
            assert factory is not None
            assert factory.description

    def test_builtins_do_not_overwrite_programmatic(self) -> None:
        """Programmatic registrations take priority over builtins."""

        def custom_factory(llm: LLM) -> Agent:
            return cast(Agent, MagicMock())

        register_agent(
            name="explore",
            factory_func=custom_factory,
            description="Custom explore",
        )

        registered = register_builtins_agents()
        assert "explore" not in registered

        factory = get_agent_factory("explore")
        assert factory.description == "Custom explore"

    def test_builtin_agents_produce_valid_agents(self) -> None:
        """Each registered builtin should produce a valid Agent instance."""
        register_builtins_agents()
        llm = _make_test_llm()
        for name in ("default", "explore", "bash"):
            factory = get_agent_factory(name)
            agent = factory.factory_func(llm)
            assert isinstance(agent, Agent)
