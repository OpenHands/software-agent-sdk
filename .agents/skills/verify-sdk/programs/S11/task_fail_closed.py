"""Sub-agent confirmation when nothing can confirm, and the parent's security
analyzer.

1. task tool, no handler: `careful` (permission_mode: always_confirm) runs
   `touch closed-task.txt`.
2. DelegateExecutor, no handler (wrapped in a small `delegate` tool, as in
   S11/delegate.py): `careful_worker` (permission_mode: always_confirm) runs
   `touch closed-delegate.txt`.
3. task tool with a recording handler that rejects: the parent has
   ConfirmRisky and a recording analyzer (LOW for `task`, HIGH for anything
   else); `inheritor` (no permission_mode) runs `touch risky.txt`.

Reports which files exist, which actions the parent's analyzer was asked
about, and the handler's calls.
"""

from s11_helpers import scripted, task_observations
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.subagent import AgentDefinition, register_agent
from openhands.sdk.tool import ToolDefinition, register_tool
from openhands.tools.delegate.definition import DelegateAction, DelegateObservation
from openhands.tools.delegate.impl import DelegateExecutor
from openhands.tools.task import TaskToolSet
from openhands.tools.terminal import TerminalTool


ws = run_paths()["workspace"]
ANALYZED: list[dict] = []
HANDLER_CALLS: list[dict] = []


class RecordingAnalyzer(SecurityAnalyzerBase):
    def security_risk(self, action):  # type: ignore[override]
        command = getattr(action.action, "command", None)
        ANALYZED.append({"tool": action.tool_name, "command": command})
        return SecurityRisk.LOW if action.tool_name == "task" else SecurityRisk.HIGH


def reject(task_id: str, pending: list) -> bool:
    HANDLER_CALLS.append({"task_id": task_id, "tools": [a.tool_name for a in pending]})
    return False


class RejectingTaskToolSet(TaskToolSet):
    @classmethod
    def create(cls, conv_state, **params):  # type: ignore[override]
        return TaskToolSet.create(conv_state, confirmation_handler=reject, **params)


class DelegateTool(ToolDefinition[DelegateAction, DelegateObservation]):
    @classmethod
    def create(cls, conv_state):  # type: ignore[override]  # noqa: ARG003
        return [
            cls(
                action_type=DelegateAction,
                observation_type=DelegateObservation,
                description="Spawn named sub-agents, then delegate tasks to them.",
                executor=DelegateExecutor(max_children=2),
            )
        ]


register_tool("rejecting_task_tool_set", RejectingTaskToolSet)
register_tool("delegate", DelegateTool)


def toucher(name: str, file: str, permission_mode: str | None):
    register_agent(
        name,
        lambda _llm: Agent(
            llm=scripted(
                [
                    {"tool": "terminal", "args": {"command": f"touch {file}"}},
                    {"finish": f"{name} done"},
                ],
                name,
            ),
            tools=[Tool(name=TerminalTool.name)],
        ),
        AgentDefinition(
            name=name,
            description=f"Touches {file}.",
            tools=["terminal"],
            permission_mode=permission_mode,
        ),
    )


toucher("careful", "closed-task.txt", "always_confirm")
toucher("careful_worker", "closed-delegate.txt", "always_confirm")
toucher("inheritor", "risky.txt", None)


def conversation_for(tool: str, script: list):
    return Conversation(
        agent=Agent(llm=scripted(script, "parent"), tools=[Tool(name=tool)]),
        workspace=str(ws),
        visualizer=None,
    )


# 1. The task tool without a handler.
plain = conversation_for(
    TaskToolSet.name,
    [
        {"tool": "task", "args": {"prompt": "touch", "subagent_type": "careful"}},
        {"finish": "delegated"},
    ],
)
plain.send_message("Delegate to careful.")
plain.run()
task_obs = task_observations(plain.state.events)
plain.close()

# 2. DelegateExecutor without a handler.
delegating = conversation_for(
    "delegate",
    [
        {
            "tool": "delegate",
            "args": {
                "command": "spawn",
                "ids": ["w"],
                "agent_types": ["careful_worker"],
            },
        },
        {
            "tool": "delegate",
            "args": {"command": "delegate", "tasks": {"w": "touch the file"}},
        },
        {"finish": "delegated"},
    ],
)
delegating.send_message("Delegate to a careful worker.")
delegating.run()
delegate_texts = [
    e.observation.text[:300]
    for e in delegating.state.events
    if type(getattr(e, "observation", None)).__name__ == "DelegateObservation"
]
delegating.close()

# 3. The parent's ConfirmRisky and analyzer; a handler that rejects.
risky = conversation_for(
    "rejecting_task_tool_set",
    [
        {"tool": "task", "args": {"prompt": "touch", "subagent_type": "inheritor"}},
        {"finish": "delegated"},
    ],
)
risky.set_security_analyzer(RecordingAnalyzer())
risky.set_confirmation_policy(ConfirmRisky())
risky.send_message("Delegate to inheritor.")
risky.run()
risky_status = risky.state.execution_status.value
risky.close()

emit(
    {
        "ok": True,
        "task_file": (ws / "closed-task.txt").exists(),
        "task_observations": task_obs,
        "delegate_file": (ws / "closed-delegate.txt").exists(),
        "delegate_observations": delegate_texts,
        "risky_status": risky_status,
        "analyzed_tools": [a["tool"] for a in ANALYZED],
        "sub_action_analyzed": any(a["tool"] == "terminal" for a in ANALYZED),
        "handler_calls": HANDLER_CALLS,
        "risky_file": (ws / "risky.txt").exists(),
    }
)
