"""Tests for built-in subagents definitions."""

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from pydantic import SecretStr

import openhands.tools.preset.default as _preset_default
from openhands.sdk import LLM, Agent
from openhands.sdk.subagent.load import load_agents_from_dir
from openhands.sdk.subagent.registry import (
    _reset_registry_for_tests,
    get_agent_factory,
)
from openhands.tools.preset.default import register_builtins_agents


# Resolve once from the installed package — works regardless of cwd.
SUBAGENTS_DIR: Final[Path] = Path(_preset_default.__file__).parent / "subagents"

# The expected agent names as defined in the .md frontmatter.
EXPECTED_MD_FILES: Final[set[str]] = {
    "default",
    "explore",
    "bash",
    "web_researcher",
}
EXPECTED_AGENT_NAMES: Final[set[str]] = {
    "general purpose",
    "explore",
    "bash",
    "web fetcher",
}


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Reset the agent registry before and after every test."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _make_test_llm() -> LLM:
    return LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")


# ---------------------------------------------------------------------------
# Subagent directory structure
# ---------------------------------------------------------------------------


def test_subagents_directory_exists() -> None:
    assert SUBAGENTS_DIR.is_dir(), f"Subagents dir missing: {SUBAGENTS_DIR}"


def test_builtins_contains_expected_md_files() -> None:
    md_files = {f.stem for f in SUBAGENTS_DIR.glob("*.md")}
    assert EXPECTED_MD_FILES.issubset(md_files), (
        f"Missing md files: {EXPECTED_MD_FILES - md_files}"
    )


# ---------------------------------------------------------------------------
# Loading definitions
# ---------------------------------------------------------------------------


def test_load_all_builtins() -> None:
    """Every .md file in subagents/ should parse without errors."""
    agents = load_agents_from_dir(SUBAGENTS_DIR)
    names = {a.name for a in agents}
    assert EXPECTED_AGENT_NAMES.issubset(names), (
        f"Missing agents: {EXPECTED_AGENT_NAMES - names}"
    )


def test_each_builtin_has_nonempty_system_prompt() -> None:
    """Every agent definition must have a non-empty system prompt."""
    agents = load_agents_from_dir(SUBAGENTS_DIR)
    for agent_def in agents:
        assert agent_def.system_prompt.strip(), (
            f"Agent '{agent_def.name}' has an empty system prompt"
        )


def test_each_builtin_has_nonempty_description() -> None:
    """Every agent definition must have a non-empty description."""
    agents = load_agents_from_dir(SUBAGENTS_DIR)
    for agent_def in agents:
        assert agent_def.description.strip(), (
            f"Agent '{agent_def.name}' has an empty description"
        )


def test_each_builtin_has_tools_or_mcp() -> None:
    """Every agent definition must specify at least one tool or MCP server."""
    agents = load_agents_from_dir(SUBAGENTS_DIR)
    for agent_def in agents:
        has_tools = len(agent_def.tools) > 0
        has_mcp = agent_def.mcp_servers is not None and len(agent_def.mcp_servers) > 0
        assert has_tools or has_mcp, (
            f"Agent '{agent_def.name}' has no tools or MCP servers defined"
        )


# ---------------------------------------------------------------------------
# register_builtins_agents — with browser enabled
# ---------------------------------------------------------------------------


def test_register_builtins_agents_with_browser() -> None:
    """With browser enabled, all agents should be registered."""
    registered = register_builtins_agents(enable_browser=True)
    registered_set = set(registered)
    expected = {"general purpose", "explore", "bash", "web fetcher"}
    assert expected.issubset(registered_set), (
        f"Missing registrations: {expected - registered_set}"
    )


def test_general_purpose_agent_tools() -> None:
    """'general purpose' agent should have terminal, file_editor, task_tracker."""
    register_builtins_agents(enable_browser=True)
    llm = _make_test_llm()
    factory = get_agent_factory("general purpose")
    agent = factory.factory_func(llm)
    assert isinstance(agent, Agent)
    tool_names = [t.name for t in agent.tools]
    assert tool_names == ["terminal", "file_editor", "task_tracker"]


def test_explore_agent_tools() -> None:
    register_builtins_agents(enable_browser=True)
    llm = _make_test_llm()
    factory = get_agent_factory("explore")
    agent = factory.factory_func(llm)
    assert isinstance(agent, Agent)
    assert [t.name for t in agent.tools] == ["terminal"]


def test_bash_agent_tools() -> None:
    register_builtins_agents(enable_browser=True)
    llm = _make_test_llm()
    factory = get_agent_factory("bash")
    agent = factory.factory_func(llm)
    assert isinstance(agent, Agent)
    assert [t.name for t in agent.tools] == ["terminal"]


def test_web_fetcher_agent() -> None:
    """'web fetcher' agent must have no tools and MCP fetch configured."""
    register_builtins_agents(enable_browser=True)
    llm = _make_test_llm()
    factory = get_agent_factory("web fetcher")
    agent = factory.factory_func(llm)
    assert isinstance(agent, Agent)
    assert agent.tools == []
    assert agent.mcp_config is not None
    assert "mcpServers" in agent.mcp_config
    assert "fetch" in agent.mcp_config["mcpServers"]


# ---------------------------------------------------------------------------
# register_builtins_agents — without browser
# ---------------------------------------------------------------------------


def test_register_builtins_agents_no_browser() -> None:
    """Without browser, 'general purpose', 'explore', 'bash' should register."""
    registered = register_builtins_agents(enable_browser=False)
    registered_set = set(registered)
    assert {"general purpose", "explore", "bash"}.issubset(registered_set)


def test_no_browser_excludes_web_fetcher() -> None:
    """Without browser/web access, web fetcher should not be registered."""
    registered = register_builtins_agents(enable_browser=False)
    assert "web fetcher" not in registered


# ---------------------------------------------------------------------------
# Idempotency and non-overwrite behavior
# ---------------------------------------------------------------------------


def test_register_builtins_agents_idempotent() -> None:
    """Calling register_builtins_agents twice should not fail or duplicate."""
    first = register_builtins_agents(enable_browser=True)
    second = register_builtins_agents(enable_browser=True)
    # Second call should register nothing (agents already present).
    assert len(first) > 0
    assert len(second) == 0


def test_register_builtins_does_not_overwrite_existing() -> None:
    """
    If an agent is already registered,
    register_builtins_agents must not replace it.
    """
    from openhands.sdk.subagent.registry import register_agent_if_absent

    sentinel_called = False

    def sentinel_factory(llm: LLM) -> Agent:
        nonlocal sentinel_called
        sentinel_called = True
        return Agent(llm=llm, tools=[])

    # Pre-register "explore" with a custom factory
    register_agent_if_absent(
        name="explore",
        factory_func=sentinel_factory,
        description="custom explore",
    )

    register_builtins_agents(enable_browser=True)

    # The factory should still be our sentinel, not the builtin one
    factory = get_agent_factory("explore")
    llm = _make_test_llm()
    factory.factory_func(llm)
    assert sentinel_called, "Builtin registration overwrote a pre-existing agent"
