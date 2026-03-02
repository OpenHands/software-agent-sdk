"""Tests for SDK built-in agent definitions (default, explore, bash)."""

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
    assert {"default", "explore", "bash"}.issubset(md_files)


def test_load_all_builtins() -> None:
    """Every .md file in subagents/ should parse without errors."""
    agents = load_agents_from_dir(SUBAGENTS_DIR)
    names = {a.name for a in agents}
    assert {"default", "explore", "bash"}.issubset(names)


@pytest.mark.parametrize(
    "cli_mode, agent_names",
    [
        (False, ["default", "explore", "bash"]),
        (True, ["default cli mode", "explore", "bash"]),
    ],
)
def test_register_builtins_agents_registers_expected_factories(
    cli_mode: bool, agent_names: list[str]
) -> None:
    register_builtins_agents(cli_mode=cli_mode)

    llm = _make_test_llm()
    agent_tool_names: dict[str, list[str]] = {}
    for name in agent_names:
        factory = get_agent_factory(name)
        agent = factory.factory_func(llm)
        assert isinstance(agent, Agent)
        agent_tool_names[name] = [t.name for t in agent.tools]

    assert len(agent_tool_names) == 3

    if cli_mode:
        assert agent_tool_names["default cli mode"] == [
            "terminal",
            "file_editor",
            "task_tracker",
        ]
    else:
        assert agent_tool_names["default"] == [
            "terminal",
            "file_editor",
            "task_tracker",
            "browser_tool_set",
        ]

    assert agent_tool_names["explore"] == ["terminal"]
    assert agent_tool_names["bash"] == ["terminal"]
