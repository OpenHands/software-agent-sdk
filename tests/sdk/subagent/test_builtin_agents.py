"""Tests for SDK built-in agent definitions (default, explore, bash)."""

from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.subagent.load import load_agents_from_dir
from openhands.sdk.subagent.registry import (
    BUILTINS_DIR,
    _reset_registry_for_tests,
    get_agent_factory,
    register_agent,
    register_builtins_agents,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Reset the agent registry before and after every test."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _make_test_llm() -> LLM:
    return LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")


def test_builtins_contains_expected_agents() -> None:
    md_files = {f.stem for f in BUILTINS_DIR.glob("*.md")}
    assert {"default", "explore", "bash"}.issubset(md_files)


def test_load_all_builtins() -> None:
    """Every .md file in builtins/ should parse without errors."""
    agents = load_agents_from_dir(BUILTINS_DIR)
    names = {a.name for a in agents}
    assert {"default", "explore", "bash"}.issubset(names)


def test_register_builtins_agents_registers_expected_factories() -> None:
    register_builtins_agents()

    llm = _make_test_llm()
    agent_tool_names: dict[str, list[str]] = {}
    for name in ("default", "explore", "bash"):
        factory = get_agent_factory(name)
        agent = factory.factory_func(llm)
        assert isinstance(agent, Agent)
        agent_tool_names[name] = [t.name for t in agent.tools]

    assert agent_tool_names["default"] == [
        "terminal",
        "file_editor",
        "task_tracker",
        "browser_tool_set",
    ]
    assert agent_tool_names["explore"] == ["terminal"]
    assert agent_tool_names["bash"] == ["terminal"]


def test_builtins_do_not_overwrite_programmatic() -> None:
    """Programmatic registrations take priority over builtins."""

    def custom_factory(llm: LLM) -> Agent:
        return Agent(llm=llm, tools=[])

    register_agent(
        name="explore",
        factory_func=custom_factory,
        description="Custom explore",
    )

    registered = register_builtins_agents()
    assert "explore" not in registered

    factory = get_agent_factory("explore")
    assert factory.description == "Custom explore"
    assert factory.factory_func(_make_test_llm()).tools == []
