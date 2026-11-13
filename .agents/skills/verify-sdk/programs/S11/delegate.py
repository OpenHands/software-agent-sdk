"""The older DelegateExecutor (spawn named sub-agents, then delegate a dict of
tasks run in parallel threads), wired into an agent the way a program does
it: no tool is registered for it in this checkout, so the program defines a
small `delegate` tool around the executor. The parent uses a
DelegationVisualizer named "Lead"; its output is captured to read the
sender/agent labels.

Each worker type has its own scripted LLM (the workers run in parallel).
The parent:
  delegate spawn [lodging_expert, activity_planner]
  delegate {lodging_expert: ..., activity_planner: ...}
  delegate spawn [x1..x4]            -> more than max_children=5 in total
  delegate spawn [p, q] with q unknown -> nothing spawned
  delegate {p: ...}                  -> p does not exist
  finish
"""

import contextlib
import io
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.subagent import register_agent
from openhands.sdk.tool import ToolDefinition, register_tool
from openhands.tools.delegate import DelegationVisualizer
from openhands.tools.delegate.definition import DelegateAction, DelegateObservation
from openhands.tools.delegate.impl import DelegateExecutor
from openhands.tools.terminal import TerminalTool


paths = run_paths()
ws = paths["workspace"]


class DelegateTool(ToolDefinition[DelegateAction, DelegateObservation]):
    @classmethod
    def create(cls, conv_state, max_children: int = 5):  # type: ignore[override]  # noqa: ARG003
        return [
            cls(
                action_type=DelegateAction,
                observation_type=DelegateObservation,
                description="Spawn named sub-agents, then delegate tasks to them.",
                executor=DelegateExecutor(max_children=max_children),
            )
        ]


register_tool("delegate", DelegateTool)


def worker(name: str):
    def factory(_llm):
        return Agent(
            llm=make_llm(
                [
                    {
                        "tool": "terminal",
                        "args": {"command": f"echo {name} > {name}.txt"},
                    },
                    {"finish": f"{name} wrote {name}.txt"},
                ],
                usage_id=name,
            ),
            tools=[Tool(name=TerminalTool.name)],
        )

    return factory


register_agent("lodging", worker("lodging"), "Finds lodging.")
register_agent("activities", worker("activities"), "Plans activities.")

parent = make_llm(
    [
        {
            "tool": "delegate",
            "args": {
                "command": "spawn",
                "ids": ["lodging_expert", "activity_planner"],
                "agent_types": ["lodging", "activities"],
            },
        },
        {
            "tool": "delegate",
            "args": {
                "command": "delegate",
                "tasks": {
                    "lodging_expert": "Write lodging.txt",
                    "activity_planner": "Write activities.txt",
                },
            },
        },
        {
            "tool": "delegate",
            "args": {"command": "spawn", "ids": ["x1", "x2", "x3", "x4"]},
        },
        {
            "tool": "delegate",
            "args": {
                "command": "spawn",
                "ids": ["p", "q"],
                "agent_types": ["lodging", "no_such_type"],
            },
        },
        {
            "tool": "delegate",
            "args": {"command": "delegate", "tasks": {"p": "anything"}},
        },
        {"finish": "trip planned"},
    ],
    usage_id="lead",
)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    conversation = Conversation(
        agent=Agent(llm=parent, tools=[Tool(name="delegate")]),
        workspace=str(ws),
        persistence_dir=str(paths["conversations"]),
        visualizer=DelegationVisualizer(name="Lead"),
    )
    conversation.send_message("Plan the trip with two helpers.")
    conversation.run()
status = conversation.state.execution_status.value
observations = [
    {
        "command": e.observation.command,
        "is_error": e.observation.is_error,
        "text": e.observation.text[:400],
    }
    for e in conversation.state.events
    if type(getattr(e, "observation", None)).__name__ == "DelegateObservation"
]
usage = sorted(conversation.conversation_stats.usage_to_metrics)
subagents_dir = Path(conversation.state.persistence_dir) / "subagents"
sub_dirs = len(list(subagents_dir.glob("*"))) if subagents_dir.is_dir() else 0
conversation.close()
shown = out.getvalue()

emit(
    {
        "ok": True,
        "execution_status": status,
        "observations": observations,
        "files": sorted(p.name for p in ws.glob("*.txt")),
        "usage_ids": usage,
        "subagent_dirs": sub_dirs,
        "visualizer": {
            "lead_titles": "Lead Agent Action" in shown,
            "sub_agent_titles": "Lodging Expert Agent Action" in shown,
            "sender_titles": "Lead Agent Message to Lodging Expert Agent" in shown,
        },
    }
)
