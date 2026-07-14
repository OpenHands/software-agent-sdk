import pytest

import openhands.agent_server.default_tools as defaults
from openhands.sdk.tool import DEFAULT_EXEC_TOOL_NAMES, SUB_AGENT_TOOL_NAME


def test_resolve_default_tools_uses_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        defaults, "is_tool_usable", lambda name: name == "browser_tool_set"
    )
    monkeypatch.setattr(
        defaults, "list_usable_default_tools", lambda: ["canvas_ui", "terminal"]
    )

    tools = defaults.resolve_default_tools(enable_sub_agents=True)

    assert [tool.name for tool in tools] == [
        *DEFAULT_EXEC_TOOL_NAMES,
        SUB_AGENT_TOOL_NAME,
        "browser_tool_set",
        "canvas_ui",
    ]


def test_resolve_default_tools_omits_unusable_optional_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(defaults, "is_tool_usable", lambda name: False)
    monkeypatch.setattr(defaults, "list_usable_default_tools", lambda: [])

    tools = defaults.resolve_default_tools()

    assert [tool.name for tool in tools] == list(DEFAULT_EXEC_TOOL_NAMES)
