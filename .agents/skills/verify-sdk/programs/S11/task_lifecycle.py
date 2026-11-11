"""A parent agent with the task tool delegates to programmatic sub-agents,
each with its own scripted LLM, the way example 41 registers `animal_expert`.

The parent's script:
  1. task(echoer)                    -> echoer runs `echo hello`, finishes
  2. task(echoer, resume=task_1)     -> the same sub-conversation, reopened
  3. task(nobody)                    -> unknown type
  4. task(echoer, resume=task_ffff)  -> unknown task id
  5. task(looper)                    -> stops at its definition's 1 iteration
  6. task(crasher)                   -> its LLM raises
  7. finish
A second, small parent (max_iteration_per_run=2) delegates to `plodder`,
which has no limit of its own. The echoer's definition carries a PostToolUse
hook. The parent persists, so sub-conversations land under <parent>/subagents/.
"""

from pathlib import Path

from s11_helpers import CALLS, calls_for, scripted, task_observations
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.subagent import AgentDefinition, register_agent
from openhands.tools.task import TaskToolSet
from openhands.tools.terminal import TerminalTool


paths = run_paths()
workspace = paths["workspace"]

echoer_scripts = [
    [
        {"tool": "terminal", "args": {"command": "echo hello > hello.txt"}},
        {"finish": "said hello"},
    ],
    [{"finish": "you asked me to say hello"}],
]


def make_echoer(_llm):
    return Agent(
        llm=scripted(echoer_scripts.pop(0), usage_id="echoer"),
        tools=[Tool(name=TerminalTool.name)],
    )


register_agent(
    "echoer",
    make_echoer,
    AgentDefinition(
        name="echoer",
        description="Echoes things in the terminal.",
        tools=["terminal"],
        hooks=HookConfig(
            post_tool_use=[
                HookMatcher(
                    matcher="terminal",
                    hooks=[HookDefinition(command="echo sub-hook >> sub_hooks.log")],
                )
            ]
        ),
    ),
)
register_agent(
    "looper",
    lambda _llm: Agent(
        llm=scripted(
            [
                {"tool": "terminal", "args": {"command": "echo partial"}},
                {"tool": "terminal", "args": {"command": "echo more"}},
                {"finish": "never reached"},
            ],
            usage_id="looper",
        ),
        tools=[Tool(name=TerminalTool.name)],
    ),
    AgentDefinition(name="looper", description="Loops.", max_iteration_per_run=1),
)
register_agent(
    "crasher",
    lambda _llm: Agent(
        llm=scripted(
            [{"error": "LLMAuthenticationError", "message": "bad key"}],
            usage_id="crasher",
        ),
        tools=[],
    ),
    "Fails at once.",
)

parent_llm = scripted(
    [
        {
            "tool": "task",
            "args": {
                "prompt": "Say hello",
                "subagent_type": "echoer",
                "description": "greet",
            },
        },
        {
            "tool": "task",
            "args": {
                "prompt": "What did I ask you before?",
                "subagent_type": "echoer",
                "resume": "task_00000001",
            },
        },
        {"tool": "task", "args": {"prompt": "hi", "subagent_type": "nobody"}},
        {
            "tool": "task",
            "args": {
                "prompt": "hi",
                "subagent_type": "echoer",
                "resume": "task_0000ffff",
            },
        },
        {"tool": "task", "args": {"prompt": "loop", "subagent_type": "looper"}},
        {"tool": "task", "args": {"prompt": "crash", "subagent_type": "crasher"}},
        {"finish": "delegation done"},
    ],
    usage_id="parent",
)
conversation = Conversation(
    agent=Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)]),
    workspace=str(workspace),
    persistence_dir=str(paths["conversations"]),
    max_iteration_per_run=30,
    visualizer=None,
)
conversation.send_message("Delegate the greeting.")
conversation.run()
status = conversation.state.execution_status.value
observations = task_observations(conversation.state.events)
usage = conversation.conversation_stats.usage_to_metrics
parent_dir = Path(conversation.state.persistence_dir)
parent_id = str(conversation.id)
conversation.close()
subagent_dirs = (
    sorted(p.name for p in (parent_dir / "subagents").iterdir())
    if (parent_dir / "subagents").is_dir()
    else []
)

# A sub-agent without its own limit inherits the parent's (2 here).
register_agent(
    "plodder",
    lambda _llm: Agent(
        llm=scripted(
            [
                {"tool": "terminal", "args": {"command": f"echo step{i}"}}
                for i in range(3)
            ]
            + [{"finish": "never reached"}],
            usage_id="plodder",
        ),
        tools=[Tool(name=TerminalTool.name)],
    ),
    "Takes many steps.",
)
small = Conversation(
    agent=Agent(
        llm=scripted(
            [
                {
                    "tool": "task",
                    "args": {"prompt": "plod", "subagent_type": "plodder"},
                },
                {"finish": "ok"},
            ],
            usage_id="small-parent",
        ),
        tools=[Tool(name=TaskToolSet.name)],
    ),
    workspace=str(workspace),
    max_iteration_per_run=2,
    visualizer=None,
)
small.send_message("Plod.")
small.run()
plodder = task_observations(small.state.events)
small.close()

echoer_calls = calls_for("echoer")
looper_calls = calls_for("looper")
by_id = {o["task_id"]: o for o in observations}
emit(
    {
        "ok": True,
        "execution_status": status,
        "observations": observations,
        "first_task": observations[0] if observations else None,
        "hello_written": (workspace / "hello.txt").exists(),
        # resume: the echoer's second LLM call sees the first prompt
        "resume_task": observations[1] if len(observations) > 1 else None,
        "resume_saw_first_prompt": bool(echoer_calls)
        and "Say hello" in echoer_calls[-1]["text"],
        "unknown_type": observations[2] if len(observations) > 2 else None,
        "unknown_resume": observations[3] if len(observations) > 3 else None,
        "looper": observations[4] if len(observations) > 4 else None,
        "looper_llm_calls": len(looper_calls),
        "crasher": observations[5] if len(observations) > 5 else None,
        "plodder": plodder[0] if plodder else None,
        "plodder_llm_calls": len(calls_for("plodder")),
        # metrics, persistence, hooks, cache key
        "usage_ids": sorted(usage),
        "subagent_dirs": len(subagent_dirs),
        "sub_hook_lines": (workspace / "sub_hooks.log").read_text().split()
        if (workspace / "sub_hooks.log").exists()
        else [],
        # one entry per echoer LLM call: create (call 1, 2) then resume (call 3)
        "sub_cache_key_is_parent": [
            (c["prompt_cache_key"] or "").replace("-", "") == parent_id.replace("-", "")
            for c in echoer_calls
        ],
        "sub_session_differs": all(
            (c["session_id"] or "").replace("-", "") != parent_id.replace("-", "")
            for c in echoer_calls
        ),
        "parent_calls": len(calls_for("parent")),
        "task_ids": sorted(by_id),
        "all_calls": len(CALLS),
    }
)
