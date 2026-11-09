"""A conversation with command hooks at every lifecycle event, as a program
that keeps its hooks in `.openhands/hooks.json` would run it.

The hook scripts live in programs/S10/hooks/ and run in the workspace. The
scripted agent tries `rm -rf build` and creates notes.txt in one response,
tries to finish (the Stop hook sends it back), creates done.txt, and
finishes. The program then closes the conversation, reloads the saved
events, and reports what the hooks did.
"""

import json
import time
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import (
    Event,
    HookExecutionEvent,
    MessageEvent,
    UserRejectObservation,
)
from openhands.sdk.hooks import HookConfig
from openhands.sdk.testing import TestLLM
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


HOOKS = Path(__file__).resolve().parent / "hooks"
workspace = run_paths()["workspace"]
(workspace / "build").mkdir(exist_ok=True)
(workspace / "build" / "keep.txt").write_text("keep me\n")

# hooks.json in the legacy wrapper with PascalCase keys, the way Claude Code
# plugin hook files look; HookConfig.load(working_dir=...) discovers it.
hooks_json = {
    "hooks": {
        "SessionStart": [{"hooks": [{"command": f"{HOOKS}/record.sh session_start"}]}],
        "UserPromptSubmit": [{"hooks": [{"command": f"{HOOKS}/add_context.sh"}]}],
        "PreToolUse": [
            {
                "matcher": "terminal",
                "hooks": [{"command": f"{HOOKS}/guard_terminal.sh"}],
            },
            {
                "matcher": "/file_.*/",
                "hooks": [{"command": f"{HOOKS}/record.sh pre_file"}],
            },
        ],
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {"command": f"{HOOKS}/record.sh post"},
                    {"command": f"{HOOKS}/fail_soft.sh"},
                ],
            },
            {
                "matcher": "file_editor",
                "hooks": [
                    {
                        "command": f"{HOOKS}/async_sleeper.sh",
                        "async": True,
                        "timeout": 60,
                    }
                ],
            },
        ],
        "Stop": [{"hooks": [{"command": f"{HOOKS}/require_done.sh"}]}],
        "SessionEnd": [{"hooks": [{"command": "touch session_ended"}]}],
    }
}
(workspace / ".openhands").mkdir(exist_ok=True)
(workspace / ".openhands" / "hooks.json").write_text(json.dumps(hooks_json))
hook_config = HookConfig.load(working_dir=workspace)

# Record what the LLM is sent, to see the hook's context reach it.
seen: list[str] = []


class RecordingLLM(TestLLM):
    def completion(self, messages, *args, **kwargs):  # type: ignore[override]
        seen.append(
            "\n".join(
                getattr(c, "text", "") for m in messages for c in (m.content or [])
            )
        )
        return super().completion(messages, *args, **kwargs)


scripted = make_llm(
    [
        {
            "text": "Cleaning up and writing notes.",
            "tools": [
                {"tool": "terminal", "args": {"command": "rm -rf build"}},
                {
                    "tool": "file_editor",
                    "args": {
                        "command": "create",
                        "path": str(workspace / "notes.txt"),
                        "file_text": "hello\n",
                    },
                },
            ],
        },
        {"finish": "first try"},
        {"tool": "terminal", "args": {"command": "touch done.txt"}},
        {"finish": "all done"},
    ]
)
llm = RecordingLLM.from_messages(list(scripted._scripted_responses), usage_id="agent")
agent = Agent(
    llm=llm, tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]
)
conversation = Conversation(
    agent=agent,
    workspace=str(workspace),
    persistence_dir=str(run_paths()["conversations"]),
    hook_config=hook_config,
    visualizer=None,
)
conversation_id = str(conversation.id)
conversation.send_message("Clean the build directory and write notes.txt.")
conversation.run()
status = conversation.state.execution_status.value
stored_hook_config = conversation.state.hook_config
time.sleep(0.5)  # let the async hook write its input
async_pid = (
    (workspace / "async_pid.txt").read_text().strip()
    if (workspace / "async_pid.txt").exists()
    else None
)
conversation.close()


def alive(pid: str | None) -> bool | None:
    if pid is None:
        return None
    # A killed process that nobody reaped yet is a zombie: not alive.
    try:
        status = Path(f"/proc/{int(pid)}/status").read_text()
    except OSError:
        return False
    return "State:\tZ" not in status


# Reload the saved events from disk, as a later program would.
events_dir = run_paths()["conversations"] / conversation_id.replace("-", "") / "events"
events = [
    Event.model_validate_json(p.read_text()) for p in sorted(events_dir.glob("*.json"))
]
hook_events = [e for e in events if isinstance(e, HookExecutionEvent)]
by_type: dict[str, int] = {}
for e in hook_events:
    by_type[e.hook_event_type] = by_type.get(e.hook_event_type, 0) + 1
rejections = [e for e in events if isinstance(e, UserRejectObservation)]
user_messages = [
    e for e in events if isinstance(e, MessageEvent) and e.source == "user"
]
feedback = [
    e for e in events if isinstance(e, MessageEvent) and e.source == "environment"
]
stop_events = [e for e in hook_events if e.hook_event_type == "Stop"]
soft = [e for e in hook_events if "fail_soft" in e.hook_command]
pre_terminal = [
    e
    for e in hook_events
    if e.hook_event_type == "PreToolUse" and e.tool_name == "terminal"
]
calls = (
    (workspace / "hook_calls.log").read_text().splitlines()
    if (workspace / "hook_calls.log").exists()
    else []
)
post_inputs = [json.loads(c.split(" ", 1)[1]) for c in calls if c.startswith("post ")]
env = dict(
    line.split("=", 1)
    for line in (workspace / "hook_env.txt").read_text().splitlines()
    if "=" in line
)
async_input = (
    json.loads((workspace / "async_input.json").read_text())
    if (workspace / "async_input.json").exists()
    else {}
)

emit(
    {
        "ok": True,
        "execution_status": status,
        "hook_events_by_type": by_type,
        "hook_event_fields": sorted(hook_events[0].model_dump().keys())
        if hook_events
        else [],
        "stored_hook_config_events": sorted(
            k for k, v in stored_hook_config.model_dump().items() if v
        )
        if stored_hook_config
        else [],
        # PreToolUse block
        "build_kept": (workspace / "build" / "keep.txt").exists(),
        "notes_written": (workspace / "notes.txt").exists(),
        "rejections": len(rejections),
        "rejection_source": rejections[0].rejection_source if rejections else None,
        "rejection_reason": rejections[0].rejection_reason if rejections else None,
        "pre_terminal_blocked": [e.blocked for e in pre_terminal],
        "pre_terminal_exit_codes": [e.exit_code for e in pre_terminal],
        "pre_terminal_action_ids_set": all(e.action_id for e in pre_terminal),
        # matcher: the regex matcher saw only file_editor
        "pre_file_calls": sum(1 for c in calls if c.startswith("pre_file ")),
        # command environment
        "env_event_type": env.get("OPENHANDS_EVENT_TYPE"),
        "env_tool_name": env.get("OPENHANDS_TOOL_NAME"),
        "env_project_dir_is_workspace": env.get("OPENHANDS_PROJECT_DIR")
        == str(workspace),
        "env_session_id_is_conversation": env.get("OPENHANDS_SESSION_ID", "").replace(
            "-", ""
        )
        == conversation_id.replace("-", ""),
        "cwd_is_workspace": env.get("PWD") == str(workspace),
        # PostToolUse
        "post_tools": [p.get("tool_name") for p in post_inputs],
        "post_has_tool_response": all(p.get("tool_response") for p in post_inputs),
        "post_file_editor_input_command": next(
            (
                p["tool_input"].get("command")
                for p in post_inputs
                if p.get("tool_name") == "file_editor"
            ),
            None,
        ),
        # exit code 1 is a non-blocking failure
        "soft_fail_exit_codes": sorted({e.exit_code for e in soft}),
        "soft_fail_blocked": any(e.blocked for e in soft),
        "soft_fail_success": any(e.success for e in soft),
        "soft_fail_stderr": soft[0].stderr.strip() if soft else None,
        # async hook
        "async_got_stdin": async_input.get("event_type"),
        "async_alive_after_close": alive(async_pid),
        # UserPromptSubmit context
        "user_extended_content": [
            c.text for m in user_messages for c in m.extended_content
        ],
        "llm_saw_context": bool(seen) and "HOOK-CONTEXT" in seen[0],
        # Stop hook
        "stop_blocked": [e.blocked for e in stop_events],
        "stop_feedback": [
            "".join(c.text for c in e.llm_message.content) for e in feedback
        ],
        "llm_calls": len(seen),
        # SessionStart / SessionEnd
        "session_start_log_lines": sum(
            1 for c in calls if c.startswith("session_start ")
        ),
        "session_ended_file": (workspace / "session_ended").exists(),
    }
)
