from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from openhands.sdk.tool import ToolExecutor


REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[1]))
WORKSPACE_DIR = Path(
    os.environ.get("WORKSPACE_DIR", REPO_ROOT / ".pr" / "description_workspace")
)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

PYTHONPATH_ENTRIES = [
    REPO_ROOT / "openhands-sdk",
    REPO_ROOT / "openhands-tools",
    REPO_ROOT / "openhands-workspace",
    REPO_ROOT / "openhands-agent-server",
]
for entry in PYTHONPATH_ENTRIES:
    sys.path.insert(0, str(entry))


class FakeLLM:
    def __init__(self, vision_enabled: bool) -> None:
        self._vision_enabled = vision_enabled

    def vision_is_active(self) -> bool:
        return self._vision_enabled


class FakeAgent:
    def __init__(self, vision_enabled: bool) -> None:
        self.llm = FakeLLM(vision_enabled)


class FakeWorkspace:
    def __init__(self, working_dir: str) -> None:
        self.working_dir = working_dir


class DummyExecutor(ToolExecutor[object, object]):
    def __call__(self, _action: object, _conversation=None) -> object:  # noqa: ANN001
        raise RuntimeError("Dummy executor should not be called")


def build_conv_state(vision_enabled: bool) -> SimpleNamespace:
    env_dir = WORKSPACE_DIR / ".agent_tmp" / "terminal_outputs"
    task_dir = WORKSPACE_DIR / ".agent_tmp" / "task_tracker"
    env_dir.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        workspace=FakeWorkspace(str(WORKSPACE_DIR)),
        agent=FakeAgent(vision_enabled),
        env_observation_persistence_dir=str(env_dir),
        persistence_dir=str(task_dir),
    )


def get_apply_patch_description() -> str:
    from openhands.tools.apply_patch import definition as module

    if hasattr(module, "_DESCRIPTION"):
        return module._DESCRIPTION

    return module.render_template(
        prompt_dir=str(module.PROMPT_DIR),
        template_name="tool_description.j2",
    )


def get_terminal_description(conv_state: SimpleNamespace) -> str:
    from openhands.tools.terminal.definition import TerminalTool

    return TerminalTool.create(conv_state, executor=DummyExecutor())[0].description


def get_file_editor_description(conv_state: SimpleNamespace) -> str:
    from openhands.tools.file_editor.definition import FileEditorTool

    return FileEditorTool.create(conv_state)[0].description


def get_planning_file_editor_description(conv_state: SimpleNamespace) -> str:
    from openhands.tools.planning_file_editor.definition import PlanningFileEditorTool

    return PlanningFileEditorTool.create(conv_state)[0].description


def get_grep_description(conv_state: SimpleNamespace) -> str:
    from openhands.tools.grep.definition import GrepTool

    return GrepTool.create(conv_state)[0].description


def get_glob_description(conv_state: SimpleNamespace) -> str:
    from openhands.tools.glob.definition import GlobTool

    return GlobTool.create(conv_state)[0].description


def get_gemini_descriptions(conv_state: SimpleNamespace) -> dict[str, str]:
    from openhands.tools.gemini.edit.definition import EditTool
    from openhands.tools.gemini.list_directory.definition import ListDirectoryTool
    from openhands.tools.gemini.read_file.definition import ReadFileTool
    from openhands.tools.gemini.write_file.definition import WriteFileTool

    return {
        "edit": EditTool.create(conv_state)[0].description,
        "list_directory": ListDirectoryTool.create(conv_state)[0].description,
        "read_file": ReadFileTool.create(conv_state)[0].description,
        "write_file": WriteFileTool.create(conv_state)[0].description,
    }


def get_task_tracker_description(conv_state: SimpleNamespace) -> str:
    from openhands.tools.task_tracker.definition import TaskTrackerTool

    return TaskTrackerTool.create(conv_state)[0].description


def get_tom_consult_descriptions() -> dict[str, str]:
    from openhands.tools.tom_consult import definition as module

    consult = getattr(module, "_CONSULT_DESCRIPTION", None)
    if consult is None:
        consult = module.render_template(
            prompt_dir=str(module.PROMPT_DIR),
            template_name="consult_tool_description.j2",
        )

    sleeptime = getattr(module, "_SLEEPTIME_DESCRIPTION", None)
    if sleeptime is None:
        sleeptime = module.render_template(
            prompt_dir=str(module.PROMPT_DIR),
            template_name="sleeptime_tool_description.j2",
        )

    return {
        "consult": consult,
        "sleeptime": sleeptime,
    }


def get_browser_descriptions() -> dict[str, str]:
    from openhands.tools.browser_use import definition as module

    return {
        "browser_navigate": module.BROWSER_NAVIGATE_DESCRIPTION,
        "browser_click": module.BROWSER_CLICK_DESCRIPTION,
        "browser_type": module.BROWSER_TYPE_DESCRIPTION,
        "browser_get_state": module.BROWSER_GET_STATE_DESCRIPTION,
        "browser_get_content": module.BROWSER_GET_CONTENT_DESCRIPTION,
        "browser_scroll": module.BROWSER_SCROLL_DESCRIPTION,
        "browser_go_back": module.BROWSER_GO_BACK_DESCRIPTION,
        "browser_list_tabs": module.BROWSER_LIST_TABS_DESCRIPTION,
        "browser_switch_tab": module.BROWSER_SWITCH_TAB_DESCRIPTION,
        "browser_close_tab": module.BROWSER_CLOSE_TAB_DESCRIPTION,
        "browser_get_storage": module.BROWSER_GET_STORAGE_DESCRIPTION,
        "browser_set_storage": module.BROWSER_SET_STORAGE_DESCRIPTION,
        "browser_start_recording": module.BROWSER_START_RECORDING_DESCRIPTION,
        "browser_stop_recording": module.BROWSER_STOP_RECORDING_DESCRIPTION,
    }


def main() -> None:
    results: dict[str, object] = {
        "metadata": {
            "repo_root": str(REPO_ROOT),
            "workspace_dir": str(WORKSPACE_DIR),
        },
        "tools": {},
    }

    conv_state_no_vision = build_conv_state(vision_enabled=False)
    conv_state_vision = build_conv_state(vision_enabled=True)

    tools: dict[str, object] = {}
    tools["apply_patch"] = {"description": get_apply_patch_description()}
    tools["terminal"] = {"description": get_terminal_description(conv_state_no_vision)}
    tools["file_editor"] = {
        "vision_disabled": get_file_editor_description(conv_state_no_vision),
        "vision_enabled": get_file_editor_description(conv_state_vision),
    }
    tools["planning_file_editor"] = {
        "vision_disabled": get_planning_file_editor_description(conv_state_no_vision),
        "vision_enabled": get_planning_file_editor_description(conv_state_vision),
    }
    tools["grep"] = {"description": get_grep_description(conv_state_no_vision)}
    tools["glob"] = {"description": get_glob_description(conv_state_no_vision)}
    tools["gemini"] = get_gemini_descriptions(conv_state_no_vision)
    tools["task_tracker"] = {
        "description": get_task_tracker_description(conv_state_no_vision)
    }
    tools["tom_consult"] = get_tom_consult_descriptions()
    tools["browser_use"] = get_browser_descriptions()

    results["tools"] = tools

    output_path = Path(
        os.environ.get("OUTPUT_PATH", REPO_ROOT / ".pr" / "tool_descriptions.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
