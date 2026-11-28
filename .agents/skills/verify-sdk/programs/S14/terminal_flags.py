"""What a terminal observation says about a timed-out command, and a command
that starts with a Bash comment.

Usage: terminal_flags.py subprocess|tmux

`Tool(name="terminal", params={"terminal_type": MODE,
"no_change_timeout_seconds": 2})`. A scripted agent runs:
  control   sleep 2 && printf '%s-%s\n' synthetic status-output (the
            output, not the command line, holds synthetic-status-output)
  comment   the same command after a `# ...` comment line
  hard      sleep 5 with timeout=1 (hard limit), then C-c
  idle      sleep 30 with no timeout: no new output for 2 s, then C-c
The program reports each call's exit code, `timeout` flag, text and how long
it took (action to observation).
"""

import sys
from datetime import datetime

from S14.toolkit import converse, tool_results
from sdk_verify import emit

from openhands.sdk import Tool
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
params = {"terminal_type": mode, "no_change_timeout_seconds": 2}
command = "sleep 2 && printf '%s-%s\\n' synthetic status-output"
steps = [
    ("control", {"command": command, "timeout": 12}),
    (
        "comment",
        {"command": f"# Read-only: print a status line.\n{command}", "timeout": 12},
    ),
    ("hard", {"command": "sleep 5", "timeout": 1}),
    ("hard_stop", {"command": "C-c", "is_input": True}),
    ("idle", {"command": "sleep 30"}),
    ("idle_stop", {"command": "C-c", "is_input": True}),
    ("after", {"command": "echo after-ok"}),
]
script = [{"tool": "terminal", "args": args} for _, args in steps] + [
    {"finish": "done"}
]
conversation = converse([Tool(name=TerminalTool.name, params=params)], script)
rows = [r for r in tool_results(conversation) if r["tool"] == "terminal"]
events = list(conversation.state.events)


def seconds(event) -> float:
    return datetime.fromisoformat(event.timestamp).timestamp()


actions = {e.id: e for e in events if isinstance(e, ActionEvent)}
durations = [
    round(seconds(e) - seconds(actions[e.action_id]), 2)
    for e in events
    if isinstance(e, ObservationEvent) and e.tool_name == "terminal"
]
named = {}
for (name, _), row, took in zip(steps, rows, durations):
    named[name] = {
        "exit_code": row.get("exit_code"),
        "timeout": row.get("timeout"),
        "is_error": row["is_error"],
        "text": row["text"][:600],
        "seconds": took,
    }
status = conversation.state.execution_status.value
conversation.close()

emit({"ok": True, "mode": mode, "execution_status": status, "steps": named})
