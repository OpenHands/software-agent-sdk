"""SubAgentScope: a parent with only the terminal limits its sub-agents to
the terminal, set as the task tool's `sub_agent_scope` param.

Registered: `editor` (file_editor), `sheller` (terminal, plus its own task
tool so the scope must reach the nested delegation tool). The parent:
  task(editor)  -> refused
  task(sheller) -> runs
  finish
A second agent with no tools of its own gets a description
that says no sub-agent fits.
"""

from pathlib import Path

from s11_helpers import scripted, task_observations
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.subagent import AgentDefinition, SubAgentScope, register_agent
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task import TaskToolSet
from openhands.tools.terminal import TerminalTool


paths = run_paths()
ws = paths["workspace"]
register_agent(
    "editor",
    lambda llm: Agent(llm=llm, tools=[Tool(name=FileEditorTool.name)]),
    AgentDefinition(name="editor", description="Edits files.", tools=["file_editor"]),
)
register_agent(
    "sheller",
    lambda _llm: Agent(
        llm=scripted(
            [
                {"tool": "terminal", "args": {"command": "echo scoped > scoped.txt"}},
                {"finish": "shell ok"},
            ],
            "sheller",
        ),
        tools=[Tool(name=TerminalTool.name), Tool(name=TaskToolSet.name)],
    ),
    AgentDefinition(
        name="sheller", description="Runs shell.", tools=["terminal", "task_tool_set"]
    ),
)
scope = {"sub_agent_scope": {"tools": True}}
conversation = Conversation(
    agent=Agent(
        llm=scripted(
            [
                {"tool": "task", "args": {"prompt": "edit", "subagent_type": "editor"}},
                {"tool": "task", "args": {"prompt": "run", "subagent_type": "sheller"}},
                {"finish": "scoped"},
            ],
            "parent",
        ),
        tools=[Tool(name=TerminalTool.name), Tool(name=TaskToolSet.name, params=scope)],
    ),
    workspace=str(ws),
    persistence_dir=str(paths["conversations"]),
    visualizer=None,
)
conversation.send_message("Delegate an edit and a shell command.")
conversation.run()
description = conversation.agent.tools_map["task"].description
observations = task_observations(conversation.state.events)
# The sub-conversations persist their agent, tools and params included.
status = conversation.state.execution_status.value
sub_states = list(
    (Path(conversation.state.persistence_dir) / "subagents").glob("*/base_state.json")
)
nested_scoped = any(
    '"sub_agent_scope":{"tools":true' in p.read_text().replace(" ", "")
    for p in sub_states
)
conversation.close()

# A parent with no tools of its own: no registered sub-agent fits.
lonely = Conversation(
    agent=Agent(
        llm=scripted([{"finish": "x"}], "lonely2"),
        tools=[Tool(name=TaskToolSet.name, params=scope)],
    ),
    workspace=str(ws),
    visualizer=None,
)
lonely.send_message("hi")
lonely_description = lonely.agent.tools_map["task"].description
lonely.close()

emit(
    {
        "ok": True,
        "execution_status": status,
        "lists_sheller": "**sheller**" in description,
        "lists_editor": "**editor**" in description,
        "observations": observations,
        "scoped_written": (ws / "scoped.txt").exists(),
        "nested_task_tool_scoped": nested_scoped,
        "sub_states": len(sub_states),
        "lonely_says_none": "None: every registered agent" in lonely_description,
        "scope_model": SubAgentScope(tools=True).model_dump(),
    }
)
