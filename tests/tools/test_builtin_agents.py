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


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Reset the agent registry before and after every test."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _make_test_llm() -> LLM:
    return LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")


def test_builtins_contains_expected_agents() -> None:
    md_files = {f.stem for f in SUBAGENTS_DIR.glob("*.md")}
    assert {"default", "code_explorer", "bash_runner", "web_researcher"}.issubset(
        md_files
    )


def test_load_all_builtins() -> None:
    """Every .md file in subagents/ should parse without errors."""
    agents = load_agents_from_dir(SUBAGENTS_DIR)
    names = {a.name for a in agents}
    assert {
        "general purpose",
        "code-explorer",
        "bash-runner",
        "web researcher",
    }.issubset(names)


@pytest.mark.parametrize(
    "enable_browser, expected_agents",
    [
        (
            True,
            ["general purpose", "code-explorer", "bash-runner", "web researcher"],
        ),
        (
            False,
            ["general purpose", "code-explorer", "bash-runner"],
        ),
    ],
)
def test_register_builtins_agents_registers_expected_factories(
    enable_browser: bool, expected_agents: list[str]
) -> None:
    register_builtins_agents(enable_browser=enable_browser)

    llm = _make_test_llm()
    agent_tool_names: dict[str, list[str]] = {}
    for name in expected_agents:
        factory = get_agent_factory(name)
        agent = factory.factory_func(llm)
        assert isinstance(agent, Agent)
        agent_tool_names[name] = [t.name for t in agent.tools]

    assert len(agent_tool_names) == len(expected_agents)

    # general purpose agent should never include browser tools
    assert agent_tool_names["general purpose"] == [
        "terminal",
        "file_editor",
        "task_tracker",
    ]

    assert agent_tool_names["code-explorer"] == ["terminal"]
    assert agent_tool_names["bash-runner"] == ["terminal"]

    if enable_browser:
        assert "browser_tool_set" in agent_tool_names["web researcher"]


def test_register_builtins_agents_skips_web_researcher_without_browser() -> None:
    """When enable_browser=False, the web researcher agent should not be registered."""
    register_builtins_agents(enable_browser=False)
    with pytest.raises(ValueError, match="Unknown agent 'web researcher'"):
        get_agent_factory("web researcher")
