"""The terminal's observation wait cap and a shell that exits.

Usage: terminal_wait.py subprocess|tmux

`Tool(name="terminal", params={"terminal_type": MODE,
"max_observation_wait_seconds": 1})`. A scripted agent starts a 3-second
command with a 600-second `timeout`, then polls it with empty `is_input` calls
four times (a poll after it finished is refused). On the subprocess backend
it then ends the shell with `exit 7`, runs one more command, resets and runs
again. The program reports each call's observation and how long it took
(action to observation), and what an invalid cap does.
"""

import sys
from datetime import datetime

from S14.toolkit import converse, tool_results
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.testing import TestLLM
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
params = {"terminal_type": mode, "max_observation_wait_seconds": 1}
steps = [
    ("warm", {"command": "echo ready", "timeout": 10}),
    ("long", {"command": "sleep 3; echo finished", "timeout": 600}),
    *[
        (f"poll{i}", {"command": "", "is_input": True, "timeout": 600})
        for i in range(4)
    ],
]
if mode == "subprocess":
    steps += [
        ("exit", {"command": "printf '%s%s\\n' 'final-' 'output'; exit 7"}),
        ("after_exit", {"command": "echo still-here"}),
        ("reset", {"command": "", "reset": True}),
        ("after_reset", {"command": "echo back"}),
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
        "is_error": row["is_error"],
        "text": row["text"][-400:],
        "seconds": took,
    }
polls = [named[n] for n, _ in steps if n.startswith("poll")]
done = [p for p in polls if p["exit_code"] == 0 and "finished" in p["text"]]
status = conversation.state.execution_status.value
conversation.close()

invalid = {}
for value in (0, -1):
    try:
        bad = Conversation(
            agent=Agent(
                llm=TestLLM.from_messages([]),
                tools=[
                    Tool(
                        name=TerminalTool.name,
                        params={
                            "terminal_type": mode,
                            "max_observation_wait_seconds": value,
                        },
                    )
                ],
            ),
            workspace=str(run_paths()["workspace"]),
            visualizer=None,
        )
        bad.send_message("hi")
        bad.agent.tools_map  # noqa: B018 - resolves the tools
        invalid[str(value)] = "no error"
        bad.close()
    except Exception as exc:  # noqa: BLE001
        invalid[str(value)] = f"{type(exc).__name__}: {exc}"[:300]

emit(
    {
        "ok": True,
        "mode": mode,
        "execution_status": status,
        "steps": named,
        "max_poll_seconds": max(p["seconds"] for p in polls),
        "polls_until_done": len(polls[: polls.index(done[0]) + 1]) if done else None,
        "finished_seen": bool(done),
        "invalid": invalid,
    }
)
