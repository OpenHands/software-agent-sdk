"""Run a bounded native Windows Cursor ACP create/resume probe via the public SDK."""

import argparse
import ctypes
import hashlib
import inspect
import json
import os
import secrets
import subprocess
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.event import ACPToolCallEvent


parser = argparse.ArgumentParser()
parser.add_argument("revision", choices=["base", "fixed"])
parser.add_argument("--phases", default="new,resume")
parser.add_argument("--control-cwd", action="store_true")
parser.add_argument("--resume-conversation")
parser.add_argument("--prior-session-fingerprint")
args = parser.parse_args()
root = Path(__file__).resolve().parent
source_file = Path(inspect.getfile(ACPAgent)).resolve()
assert (
    source_file
    == (
        root
        / f"sdk-{args.revision}"
        / "openhands-sdk"
        / "openhands"
        / "sdk"
        / "agent"
        / "acp_agent.py"
    ).resolve()
), source_file
workspace = root / "ACP Workspace With Spaces"
workspace.mkdir(exist_ok=True)
if not (workspace / ".git").exists():
    subprocess.run(["git.exe", "init", "--quiet", str(workspace)], check=True)
parent = root / "Parent With Spaces"
parent.mkdir(exist_ok=True)
os.chdir(parent)
fixture = root / "fixture.json"
if not fixture.exists():
    token = "ACP_SUBAGENT_OK_" + secrets.token_hex(8)
    fixture.write_text(json.dumps({"token": token}), encoding="utf-8")
else:
    token = json.loads(fixture.read_text(encoding="utf-8"))["token"]
agents = workspace / ".cursor" / "agents"
agents.mkdir(parents=True, exist_ok=True)
(agents / "auto-commit.md").write_text(
    "---\nname: auto-commit\n"
    "description: Project-only ACP validation subagent. "
    "Invoke when asked to verify the custom auto-commit agent.\n"
    "model: inherit\nreadonly: true\n---\n"
    f"Reply with exactly {token}. Do not run commands, read files, edit files, "
    "or perform Git operations. Do not explain.\n",
    encoding="utf-8",
)
agent_cmd = Path(
    os.environ.get(
        "PR_CURSOR_AGENT",
        str(Path(os.environ["LOCALAPPDATA"]) / "cursor-agent" / "agent.cmd"),
    )
)
label = args.revision + ("-control" if args.control_cwd else "")
run_dir = root / label
run_dir.mkdir(exist_ok=True)
report = run_dir / "result.json"
records = (
    json.loads(report.read_text(encoding="utf-8"))
    if args.resume_conversation and report.exists()
    else []
)
conversation_id = (
    uuid.UUID(args.resume_conversation) if args.resume_conversation else None
)
prior_session = None

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def process_handles(pid):
    command = (
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId "
        "| ConvertTo-Json -Compress"
    )
    data = json.loads(
        subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            encoding="utf-8",
            timeout=20,
        )
    )
    owned = {pid}
    while True:
        expanded = owned | {
            p["ProcessId"] for p in data if p["ParentProcessId"] in owned
        }
        if expanded == owned:
            break
        owned = expanded
    result = []
    for candidate in owned:
        handle = kernel.OpenProcess(0x100000 | 0x1000, False, candidate)
        if handle:
            result.append((candidate, handle))
    return result


for phase in args.phases.split(","):
    startup = run_dir / f"startup-{phase}.json"
    startup.unlink(missing_ok=True)
    statements = []
    if args.control_cwd:
        statements.append(
            "Set-Location -LiteralPath " + ps_literal(workspace) + " -ErrorAction Stop"
        )
    statements.append(
        "[IO.File]::WriteAllText("
        + ps_literal(startup)
        + ", (@{cwd=(Get-Location).Path;pid=$PID} | ConvertTo-Json -Compress))"
    )
    statements.append("& " + ps_literal(agent_cmd) + " acp")
    command = [
        str(
            Path(os.environ["SystemRoot"])
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        ),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "; ".join(statements),
    ]
    agent = ACPAgent(
        acp_command=command,
        acp_model="composer-2.5[fast=true]",
        acp_startup_timeout=120,
        acp_prompt_timeout=150,
    )
    events = []
    conversation = None
    handles = []
    started = time.monotonic()
    item = {
        "revision": args.revision,
        "phase": phase,
        "control_cwd": args.control_cwd,
        "parent_cwd": str(parent),
        "workspace": str(workspace),
        "model": "composer-2.5[fast=true]",
        "cursor_version": os.environ.get("PR_CURSOR_VERSION"),
        "cursor_command": str(agent_cmd),
        "sdk_source": str(source_file),
        "sdk_source_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
    }
    try:
        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            persistence_dir=run_dir / "conversation",
            conversation_id=conversation_id,
            callbacks=[events.append],
            visualizer=None,
            delete_on_close=False,
            max_iteration_per_run=2,
        )
        conversation_id = conversation.id
        conversation.send_message(
            "Actually invoke the project custom subagent named auto-commit "
            "using the Task tool. "
            "Use subagentType.custom.name=auto-commit, not a built-in agent. "
            "Ask it to return its configured ACP_SUBAGENT_OK identifier only. "
            "Do not read its definition yourself, do not run commands, "
            "do not edit files or do Git operations. "
            "Do not fall back to generalPurpose or any other agent. "
            "If Task rejects auto-commit, report the exact tool error. "
            "Otherwise reply only with the exact identifier returned by that subagent."
        )
        conversation.run()
        session = conversation.state.agent_state.get("acp_session_id")
        item["session_preserved"] = (
            phase == "new"
            or session == prior_session
            or hashlib.sha256(str(session).encode()).hexdigest()[:10]
            == args.prior_session_fingerprint
        )
        item["conversation_id"] = str(conversation_id)
        item["session_fingerprint"] = hashlib.sha256(str(session).encode()).hexdigest()[
            :10
        ]
        prior_session = session
        item["execution_status"] = conversation.state.execution_status.value
        item["selected_model"] = conversation.state.agent_state.get(
            "acp_current_model_id"
        )
    except BaseException as error:
        item["error_type"] = type(error).__name__
        item["error"] = str(error)[-1500:]
    finally:
        if startup.exists():
            launch = json.loads(startup.read_text(encoding="utf-8-sig"))
            item["startup_cwd"] = launch["cwd"]
            item["startup_cwd_matches_workspace"] = (
                Path(launch["cwd"]).resolve() == workspace.resolve()
            )
            handles = process_handles(launch["pid"])
            item["owned_processes_before_close"] = [pid for pid, _ in handles]
        if conversation is not None:
            conversation.close()
        else:
            agent.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
            kernel.WaitForSingleObject(h, 0) == 258 for _, h in handles
        ):
            time.sleep(0.1)
        lingering = [
            pid for pid, h in handles if kernel.WaitForSingleObject(h, 0) == 258
        ]
        item["owned_processes_after_sdk_close"] = lingering
        item["cleanup_passed"] = bool(handles) and not lingering
        for pid, h in handles:
            if kernel.WaitForSingleObject(h, 0) == 258:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                )
            kernel.CloseHandle(h)
    raw = [event.model_dump(mode="json", exclude_none=True) for event in events]
    (run_dir / f"events-{phase}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    joined = json.dumps(raw, ensure_ascii=False)
    tool_events = [event for event in events if isinstance(event, ACPToolCallEvent)]
    relevant = [
        event
        for event in tool_events
        if "auto-commit" in json.dumps(event.raw_input, ensure_ascii=False)
    ]
    item["custom_task_completed"] = any(
        event.status == "completed" and not event.is_error for event in relevant
    )
    item["custom_task_updates"] = [
        event.model_dump(mode="json", exclude_none=True) for event in relevant
    ]
    item["project_token_observed"] = token in joined
    item["reported_enum_rejection"] = (
        "Invalid enum value" in joined and "auto-commit" in joined
    )
    item["missing_custom_agent_rejection"] = item["reported_enum_rejection"] or (
        "Invalid option" in joined
        and "subagent_type" in joined
        and "auto-commit" in joined
    )
    item["all_tool_titles"] = sorted({event.title for event in tool_events})
    item["seconds"] = round(time.monotonic() - started, 2)
    records.append(item)
    report.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in item.items() if key != "custom_task_updates"},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if "error_type" in item:
        break
