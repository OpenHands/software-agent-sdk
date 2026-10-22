"""Run dynamic workflows through a scripted agent with `workflow_tool_set`.

The program registers three sub-agent types the way a program registers its
own (`register_agent_if_absent`): `summarizer` finishes with a fixed
report, `breaker` fails on its first model call, and `sleeper` takes a
second to build. The scripted parent agent then calls `workflow` with
scripts that fan out, pipeline, reduce, catch and leak failures, break the
sandbox rules, return a huge value, and run four sleepers with
`max_concurrency` 1 and 4. Finally it uses `WorkflowContext` and
`WorkflowExecutor` directly for the lifecycle and no-conversation cases.
"""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from S14.toolkit import content_text, converse
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Tool
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.subagent import register_agent_if_absent
from openhands.tools.workflow import WorkflowToolSet
from openhands.tools.workflow.definition import WorkflowAction
from openhands.tools.workflow.impl import WorkflowContext, WorkflowExecutor


def summarizer(llm):  # noqa: ARG001 - each sub-agent gets its own scripted model
    return Agent(
        llm=make_llm([{"finish": "report: all good"}], usage_id="sub"), tools=[]
    )


def breaker(llm):  # noqa: ARG001
    return Agent(
        llm=make_llm(
            [{"error": "LLMBadRequestError", "message": "sub-agent model refused"}],
            usage_id="sub",
        ),
        tools=[],
    )


def sleeper(llm):  # noqa: ARG001
    time.sleep(1)
    return Agent(llm=make_llm([{"finish": "slept"}], usage_id="sub"), tools=[])


register_agent_if_absent("summarizer", summarizer, "Summarizes one thing.")
register_agent_if_absent("breaker", breaker, "Always fails.")
register_agent_if_absent("sleeper", sleeper, "Takes a second to start.")

FAN_OUT = (
    "\n"
    "async def main(wf):\n"
    '    one = await wf.run_agent("Summarize module A", subagent_type="summarizer")\n'
    '    many = await wf.map_agents(["a.py", "b.py", "c.py"], lambda f: "Audit " + f, '
    'subagent_type="summarizer")\n'
    "    piped = await wf.pipeline([10, 20, 30], lambda x: x - 20, "
    "lambda d: 100 // d)\n"
    '    reduced = await wf.reduce_agent(many, "Combine the audits", '
    'subagent_type="summarizer")\n'
    '    return {"one": one, "many": many, "flat": wf.flatten([[1, 2], [3], 4]), '
    '"piped": piped, "reduced": reduced}\n'
)
CAUGHT = """
async def main(wf):
    caught = "nothing"
    try:
        await wf.map_agents(["x", "y"], "Try {item}", subagent_type="breaker")
    except* RuntimeError as group:
        caught = "caught " + str(len(group.exceptions))
    try:
        await wf.run_agent("Once", subagent_type="breaker")
    except RuntimeError as error:
        caught = caught + "; single: " + str(error)[:40]
    return caught
"""
REDUCE_HUGE = (
    "\n"
    "async def main(wf):\n"
    '    findings = ["finding " + str(i) + ": " + "x" * 500 for i in range(60)]\n'
    '    return await wf.reduce_agent(findings, "Combine the findings", '
    'subagent_type="summarizer")\n'
)
UNCAUGHT = """
async def main(wf):
    return await wf.map_agents(["x", "y"], "Try {item}", subagent_type="breaker")
"""
HUGE = """
async def main(wf):
    return list(range(100000))
"""
SLEEPERS = """
async def main(wf):
    return await wf.map_agents([1, 2, 3, 4], "Nap {item}", subagent_type="sleeper")
"""
steps = [
    ("fan_out", {"name": "fan-out", "script": FAN_OUT}),
    ("caught", {"name": "caught", "script": CAUGHT}),
    ("uncaught", {"name": "uncaught", "script": UNCAUGHT}),
    (
        "import",
        {"name": "bad", "script": "import os\nasync def main(wf):\n    return 1\n"},
    ),
    (
        "private",
        {"name": "bad", "script": "async def main(wf):\n    return wf._manager\n"},
    ),
    ("close", {"name": "bad", "script": "async def main(wf):\n    wf.close()\n"}),
    (
        "open_call",
        {"name": "bad", "script": "async def main(wf):\n    return open('x').read()\n"},
    ),
    ("no_main", {"name": "bad", "script": "def helper():\n    return 1\n"}),
    ("huge", {"name": "huge", "script": HUGE}),
    ("reduce_huge", {"name": "reduce-huge", "script": REDUCE_HUGE}),
    ("serial", {"name": "serial", "script": SLEEPERS, "max_concurrency": 1}),
    ("parallel", {"name": "parallel", "script": SLEEPERS, "max_concurrency": 4}),
]
script = [{"tool": "workflow", "args": args} for _, args in steps] + [
    {"finish": "done"}
]
conversation = converse([Tool(name=WorkflowToolSet.name)], script, "Run the workflows.")

actions = {e.id: e for e in conversation.state.events if isinstance(e, ActionEvent)}
observations = [
    e
    for e in conversation.state.events
    if isinstance(e, ObservationEvent) and e.tool_name == "workflow"
]
results = {}
for (name, _), event in zip(steps, observations):
    obs = event.observation
    started = actions[event.action_id].timestamp
    results[name] = {
        "is_error": obs.is_error,
        "status": obs.status,
        "text": obs.text[:600],
        "text_len": len(obs.text),
        "llm_text_len": len(content_text(obs)),
        "seconds": round(
            (
                datetime.fromisoformat(event.timestamp)
                - datetime.fromisoformat(started)
            ).total_seconds(),
            2,
        ),
    }
tools_map = sorted(conversation.agent.tools_map)
# What the reducer sub-agent was asked: its first user message on disk.
sub_prompts = [
    p.read_text()
    for p in sorted(
        Path(run_paths()["conversations"]).rglob("subagents/*/events/*.json")
    )
    if '"MessageEvent"' in p.read_text() and "Combine the findings" in p.read_text()
]
status = conversation.state.execution_status.value

# Direct use: a closed context refuses work; the executor needs a conversation.
context = WorkflowContext(parent_conversation=conversation, max_concurrency=2)
context.close()
context.close()  # idempotent
try:
    asyncio.run(context.run_agent("late", subagent_type="summarizer"))
    after_close = "ran"
except Exception as exc:
    after_close = f"{type(exc).__name__}: {exc}"
no_conversation = WorkflowExecutor()(
    WorkflowAction(name="x", script="async def main(wf):\n    return 1\n")
)
conversation.close()

emit(
    {
        "ok": True,
        "status": status,
        "tools_map": tools_map,
        "steps": results,
        "reducer_prompt_truncated": any(
            "items omitted to fit" in p for p in sub_prompts
        ),
        "reducer_prompt_seen": bool(sub_prompts),
        "run_after_close": after_close,
        "no_conversation_error": no_conversation.is_error,
        "no_conversation_text": no_conversation.text,
    }
)
