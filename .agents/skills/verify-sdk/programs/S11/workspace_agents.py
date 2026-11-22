"""Markdown sub-agents in the workspace, picked up by the conversation itself,
next to the built-ins a program registers with register_builtins_agents().

The workspace has .agents/agents/ws-reviewer.md (model: inherit, tools:
terminal); the program never calls register_file_agents. Both sub-agents
inherit the parent's scripted LLM (copies share one script queue):
  parent: task(ws-reviewer)  -> sub: cat notes.txt, finish
  parent: task(bash-runner)  -> sub: echo > builtin.txt, finish
  parent: finish
"""

import tempfile

from s11_helpers import scripted, task_observations
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.subagent import get_registered_agent_definitions
from openhands.tools import register_builtins_agents
from openhands.tools.task import TaskToolSet


ws = run_paths()["workspace"]
# A private temp dir, to watch the task tool's openhands_tasks_* directory.
tmp = run_paths()["run"] / "tmp"
tmp.mkdir(exist_ok=True)
tempfile.tempdir = str(tmp)
(ws / ".agents/agents").mkdir(parents=True, exist_ok=True)
(ws / ".agents/agents/ws-reviewer.md").write_text(
    "---\nname: ws-reviewer\ndescription: Reviews notes in the workspace.\n"
    "model: inherit\ntools: [terminal]\n---\nYou review notes. Be brief.\n"
)
(ws / "notes.txt").write_text("hi from the notes\n")
register_builtins_agents(enable_browser=False)

llm = scripted(
    [
        {
            "tool": "task",
            "args": {"prompt": "Check notes.txt", "subagent_type": "ws-reviewer"},
        },
        {"tool": "terminal", "args": {"command": "cat notes.txt"}},
        {"finish": "notes say hi"},
        {
            "tool": "task",
            "args": {"prompt": "Write builtin.txt", "subagent_type": "bash-runner"},
        },
        {"tool": "terminal", "args": {"command": "echo built-in > builtin.txt"}},
        {"finish": "wrote builtin.txt"},
        {"finish": "both delegated"},
    ],
    usage_id="parent",
)
conversation = Conversation(
    agent=Agent(llm=llm, tools=[Tool(name=TaskToolSet.name)]),
    workspace=str(ws),
    visualizer=None,
)
before = sorted(d.name for d in get_registered_agent_definitions())
conversation.send_message(
    "Have the reviewer check notes.txt, then have bash-runner write a file."
)
after_send = sorted(d.name for d in get_registered_agent_definitions())
conversation.run()
observations = task_observations(conversation.state.events)
status = conversation.state.execution_status.value
task_tool = conversation.agent.tools_map.get("task")
description = task_tool.description if task_tool else ""
temp_dirs_open = len(list(tmp.glob("openhands_tasks_*")))
conversation.close()
temp_dirs_closed = len(list(tmp.glob("openhands_tasks_*")))

emit(
    {
        "ok": True,
        "execution_status": status,
        "registered_before_send": "ws-reviewer" in before,
        "registered_after_send": "ws-reviewer" in after_send,
        "observations": observations,
        "builtin_written": (ws / "builtin.txt").exists(),
        "description_lists_ws_reviewer": "ws-reviewer" in description,
        "script_left": len(llm._scripted_responses),
        "temp_task_dirs_before_close": temp_dirs_open,
        "temp_task_dirs_after_close": temp_dirs_closed,
    }
)
