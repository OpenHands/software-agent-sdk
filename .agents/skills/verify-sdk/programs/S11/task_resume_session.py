"""Resuming a sub-agent task after the parent conversation is reopened.

Usage: task_resume_session.py create | resume CONVERSATION_ID | retype

create   a persisted parent delegates to `echoer` (task_00000001), finishes
         and closes; prints the parent's conversation id.
resume   a new process reopens that parent by id (a new task tool and task
         manager) and its model calls task(resume="task_00000001").
retype   in one session, the parent delegates to `echoer`, then resumes
         task_00000001 naming another type (`other`, which has no tools).

Each echoer turn has its own scripted LLM; the program reports the task
observations and whether the sub-agent was asked again in the conversation
it started in.
"""

import sys
import uuid
from pathlib import Path

from s11_helpers import calls_for, scripted, task_observations
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.subagent import AgentDefinition, register_agent
from openhands.tools.task import TaskToolSet
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()
echoer_scripts = [
    [{"finish": "said hello"}],
    [{"finish": "you asked me to say hello"}],
]
register_agent(
    "echoer",
    lambda _llm: Agent(
        llm=scripted(echoer_scripts.pop(0), "echoer"),
        tools=[Tool(name=TerminalTool.name)],
    ),
    AgentDefinition(name="echoer", description="Echoes.", tools=["terminal"]),
)
register_agent(
    "other",
    lambda _llm: Agent(llm=scripted([{"finish": "other"}], "other"), tools=[]),
    AgentDefinition(name="other", description="No tools.", tools=[]),
)


def parent(script, conversation_id=None):
    options = {}
    if conversation_id is not None:
        options["conversation_id"] = uuid.UUID(conversation_id)
    return Conversation(
        agent=Agent(
            llm=scripted(script, "parent"), tools=[Tool(name=TaskToolSet.name)]
        ),
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        visualizer=None,
        **options,
    )


def task(**args):
    return {"tool": "task", "args": {"prompt": "say hello", **args}}


if mode == "create":
    conversation = parent([task(subagent_type="echoer"), {"finish": "first done"}])
elif mode == "resume":
    conversation = parent(
        [
            task(subagent_type="echoer", resume="task_00000001"),
            {"finish": "second done"},
        ],
        sys.argv[2],
    )
elif mode == "retype":
    conversation = parent(
        [
            task(subagent_type="echoer"),
            task(subagent_type="other", resume="task_00000001"),
            {"finish": "done"},
        ]
    )
else:
    raise SystemExit(f"unknown mode {mode}")

conversation.send_message("Delegate.")
conversation.run()
subagents = Path(conversation.state.persistence_dir) / "subagents"
out = {
    "ok": True,
    "mode": mode,
    "id": str(conversation.id),
    "execution_status": conversation.state.execution_status.value,
    "observations": task_observations(conversation.state.events),
    "echoer_calls": len(calls_for("echoer")),
    "other_calls": len(calls_for("other")),
    "subagent_dirs": sorted(p.name for p in subagents.glob("*"))
    if subagents.is_dir()
    else [],
}
conversation.close()
emit(out)
