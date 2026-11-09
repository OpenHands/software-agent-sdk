"""Sub-agent confirmation: a definition's permission_mode, inheritance of the
parent's policy, and the confirmation handler a program gives the task tool.

A program passes a handler by registering its own task tool set whose
create() calls TaskToolSet.create(conv_state, confirmation_handler=...).

Conversation 1 (handler: reject task 1, approve task 2):
  task(careful)   careful has permission_mode: always_confirm -> handler says no
  finish
  -- the program sets AlwaysConfirm on the parent and sends another message --
  task(inheritor) the parent waits for confirmation; run() again approves it;
                  inheritor has no permission_mode, inherits AlwaysConfirm,
                  and the handler says yes
  finish
Conversation 2 (plain TaskToolSet, no handler):
  task(careful)   the sub-agent's actions are approved without asking
"""

from s11_helpers import scripted, task_observations
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.subagent import AgentDefinition, register_agent
from openhands.sdk.tool import register_tool
from openhands.tools.task import TaskToolSet
from openhands.tools.terminal import TerminalTool


ws = run_paths()["workspace"]
handler_calls: list[dict] = []


def handler(task_id: str, pending: list) -> bool:
    approve = task_id != "task_00000001"
    handler_calls.append(
        {
            "task_id": task_id,
            "tools": [a.tool_name for a in pending],
            "approved": approve,
        }
    )
    return approve


class HandledTaskToolSet(TaskToolSet):
    @classmethod
    def create(cls, conv_state, **params):  # type: ignore[override]
        return TaskToolSet.create(conv_state, confirmation_handler=handler, **params)


register_tool("handled_task_tool_set", HandledTaskToolSet)


def sub_agent(script: list, usage_id: str):
    return lambda _llm: Agent(
        llm=scripted(script, usage_id), tools=[Tool(name=TerminalTool.name)]
    )


careful_scripts = [
    [
        {"tool": "terminal", "args": {"command": "touch careful.txt"}},
        {"finish": "careful done"},
    ],
    [
        {"tool": "terminal", "args": {"command": "touch auto.txt"}},
        {"finish": "auto done"},
    ],
]
register_agent(
    "careful",
    lambda _llm: Agent(
        llm=scripted(careful_scripts.pop(0), "careful"),
        tools=[Tool(name=TerminalTool.name)],
    ),
    AgentDefinition(
        name="careful",
        description="Asks first.",
        tools=["terminal"],
        permission_mode="always_confirm",
    ),
)
register_agent(
    "inheritor",
    sub_agent(
        [
            {"tool": "terminal", "args": {"command": "touch inherited.txt"}},
            {"finish": "inherited done"},
        ],
        "inheritor",
    ),
    AgentDefinition(name="inheritor", description="Inherits.", tools=["terminal"]),
)

parent = scripted(
    [
        {
            "tool": "task",
            "args": {"prompt": "touch careful.txt", "subagent_type": "careful"},
        },
        {"finish": "first done"},
        {
            "tool": "task",
            "args": {"prompt": "touch inherited.txt", "subagent_type": "inheritor"},
        },
        {"finish": "second done"},
    ],
    "parent",
)
conversation = Conversation(
    agent=Agent(llm=parent, tools=[Tool(name="handled_task_tool_set")]),
    workspace=str(ws),
    visualizer=None,
)
conversation.send_message("Delegate to careful.")
conversation.run()
conversation.set_confirmation_policy(AlwaysConfirm())
conversation.send_message("Delegate to inheritor.")
conversation.run()
parent_waited = conversation.state.execution_status.value
conversation.run()  # approve the pending task call
status = conversation.state.execution_status.value
observations = task_observations(conversation.state.events)
conversation.close()

# No handler: the sub-agent's confirmations are approved.
plain = Conversation(
    agent=Agent(
        llm=scripted(
            [
                {
                    "tool": "task",
                    "args": {"prompt": "touch auto.txt", "subagent_type": "careful"},
                },
                {"finish": "auto delegated"},
            ],
            "plain-parent",
        ),
        tools=[Tool(name=TaskToolSet.name)],
    ),
    workspace=str(ws),
    visualizer=None,
)
plain.send_message("Delegate to careful without a handler.")
plain.run()
plain_observations = task_observations(plain.state.events)
plain.close()

emit(
    {
        "ok": True,
        "handler_calls": handler_calls,
        "careful_file": (ws / "careful.txt").exists(),
        "parent_waited": parent_waited,
        "execution_status": status,
        "inherited_file": (ws / "inherited.txt").exists(),
        "observations": observations,
        "auto_file": (ws / "auto.txt").exists(),
        "plain_observations": plain_observations,
    }
)
