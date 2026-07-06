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


def test_forced_browser_off_is_exec_set_only() -> None:
    assert _names(enable_browser=False) == list(DEFAULT_EXEC_TOOL_NAMES)


def test_forced_browser_on_appends_browser_before_sub_agents() -> None:
    assert _names(enable_browser=True, enable_sub_agents=True) == [
        *DEFAULT_EXEC_TOOL_NAMES,
        BROWSER_TOOL_NAME,
        SUB_AGENT_TOOL_NAME,
    ]


def test_auto_excludes_browser_when_unregistered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(registry._REG, BROWSER_TOOL_NAME, raising=False)
    monkeypatch.delitem(registry._USABILITY_REG, BROWSER_TOOL_NAME, raising=False)
    assert _names() == list(DEFAULT_EXEC_TOOL_NAMES)


def test_auto_includes_browser_when_registered_and_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(registry._REG, BROWSER_TOOL_NAME, lambda params, conv: [])
    monkeypatch.setitem(registry._USABILITY_REG, BROWSER_TOOL_NAME, lambda: True)
    assert _names() == [*DEFAULT_EXEC_TOOL_NAMES, BROWSER_TOOL_NAME]


def test_auto_excludes_browser_when_registered_but_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registered but no chromium: the default must not produce a spec that
    fails at conversation start."""
    monkeypatch.setitem(registry._REG, BROWSER_TOOL_NAME, lambda params, conv: [])
    monkeypatch.setitem(registry._USABILITY_REG, BROWSER_TOOL_NAME, lambda: False)
    assert _names() == list(DEFAULT_EXEC_TOOL_NAMES)


def test_is_tool_usable_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    assert registry.is_tool_usable("definitely-not-registered") is False
    monkeypatch.setitem(registry._REG, "probe", lambda params, conv: [])
    monkeypatch.setitem(registry._USABILITY_REG, "probe", lambda: True)
    assert registry.is_tool_usable("probe") is True

    def _boom() -> bool:
        raise RuntimeError("checker crashed")

    monkeypatch.setitem(registry._USABILITY_REG, "probe", _boom)
    assert registry.is_tool_usable("probe") is False
