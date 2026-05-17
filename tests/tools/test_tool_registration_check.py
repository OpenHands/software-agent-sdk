from pathlib import Path

from scripts.check_tool_registration import main


def test_browser_definition_special_case_handles_platform_path_separator():
    repo_root = Path(__file__).parents[2]
    browser_definition = (
        repo_root
        / "openhands-tools"
        / "openhands"
        / "tools"
        / "browser_use"
        / "definition.py"
    )

    assert main([str(browser_definition)]) == 0
