"""Tests for the canonical default tool specs (openhands.sdk.tool.defaults)."""

import pytest

from openhands.sdk.tool import registry
from openhands.sdk.tool.defaults import (
    BROWSER_TOOL_NAME,
    DEFAULT_EXEC_TOOL_NAMES,
    SUB_AGENT_TOOL_NAME,
    default_tool_specs,
)


def _names(**kwargs) -> list[str]:
    return [t.name for t in default_tool_specs(**kwargs)]


def test_default_is_deterministic_exec_set() -> None:
    """The default never depends on runtime/registry state — browser is a
    serving-layer injection (see BROWSER_TOOL_NAME), not part of the default."""
    assert _names() == list(DEFAULT_EXEC_TOOL_NAMES)
    assert BROWSER_TOOL_NAME not in _names()


def test_enable_sub_agents_appends_task_tool_set() -> None:
    assert _names(enable_sub_agents=True) == [
        *DEFAULT_EXEC_TOOL_NAMES,
        SUB_AGENT_TOOL_NAME,
    ]


def test_explicit_browser_appends_before_sub_agents() -> None:
    assert _names(enable_browser=True, enable_sub_agents=True) == [
        *DEFAULT_EXEC_TOOL_NAMES,
        BROWSER_TOOL_NAME,
        SUB_AGENT_TOOL_NAME,
    ]


def test_is_tool_usable_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    assert registry.is_tool_usable("definitely-not-registered") is False
    monkeypatch.setitem(registry._REG, "probe", lambda params, conv: [])
    monkeypatch.setitem(registry._USABILITY_REG, "probe", lambda: True)
    assert registry.is_tool_usable("probe") is True

    def _boom() -> bool:
        raise RuntimeError("checker crashed")

    monkeypatch.setitem(registry._USABILITY_REG, "probe", _boom)
    assert registry.is_tool_usable("probe") is False
