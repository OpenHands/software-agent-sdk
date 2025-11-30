from openhands.sdk.tool import Tool
from openhands.tools.preset.default import get_default_tools


def _tool_names(tools: list[Tool]) -> list[str]:
    return [t.name for t in tools]


def test_default_tools_non_gpt5_use_file_editor() -> None:
    tools = get_default_tools(enable_browser=True, model_name="openai/gpt-4.1-mini")
    names = _tool_names(tools)

    assert "file_editor" in names
    assert "apply_patch" not in names
    assert "terminal" in names
    assert "task_tracker" in names
    assert "browser_tool_set" in names


def test_default_tools_gpt5_use_apply_patch() -> None:
    tools = get_default_tools(
        enable_browser=True, model_name="openai/gpt-5.1-codex-mini"
    )
    names = _tool_names(tools)

    assert "apply_patch" in names
    assert "file_editor" not in names
    assert "terminal" in names
    assert "task_tracker" in names
    assert "browser_tool_set" in names


def test_default_tools_respect_enable_browser_flag() -> None:
    tools = get_default_tools(enable_browser=False, model_name="openai/gpt-5.1-mini")
    names = _tool_names(tools)

    assert "browser_use" not in names
    assert "terminal" in names
    assert "task_tracker" in names
    # still switching to apply_patch for GPT-5 when browser disabled
    assert "apply_patch" in names
