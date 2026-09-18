"""Show-and-tell for the disabled_agents deny-list.

Runs against the checkout's editable install, so it demonstrates whatever
the current branch contains. No LLM calls: every path shown stops before
any model invocation (task creation and delegate spawn only construct
conversations; the model runs later).
"""

import tempfile

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task.definition import TaskAction
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager


register_builtins_agents()

workspace = tempfile.mkdtemp(prefix="demo_disabled_agents_")
llm = LLM(model="gpt-4o", api_key=SecretStr("demo-key"), usage_id="demo")

print("=== 1. Can the preference even be expressed? ===")
try:
    ctx = AgentContext(disabled_agents=["general-purpose"])
    print(f"AgentContext(disabled_agents=['general-purpose']) -> {ctx.disabled_agents}")
except Exception as e:
    ctx = AgentContext()
    print(
        f"AgentContext(disabled_agents=[...]) -> {type(e).__name__}: "
        "field does not exist; the preference has nowhere to live"
    )

agent = Agent(llm=llm, tools=[], agent_context=ctx)
parent = LocalConversation(
    agent=agent, workspace=workspace, visualizer=None, delete_on_close=False
)

manager = TaskManager()
manager._ensure_parent(parent)

print()
print("=== 2. Does anything stop the spawn? (no LLM involved) ===")
refused = False
try:
    task = manager._create_task(subagent_type="general-purpose", description="demo")
    print(
        f"spawned: {task.id} status={task.status}  "
        "<- the 'disabled' agent runs; nothing enforced the preference"
    )
except ValueError as e:
    refused = True
    print(f"refused: {e}")

print()
print("=== 3. What the calling LLM sees (full tool path, TaskExecutor) ===")
if refused:
    obs = TaskExecutor(manager)(
        TaskAction(prompt="demo", subagent_type="general-purpose"),
        conversation=parent,
    )
    print(f"is_error={obs.is_error}")
    print(f"text: {obs.text}")
else:
    print("skipped: with no guard this call would proceed into a real LLM run")

print()
print("=== 4. A type not on the list still spawns ===")
task = manager._create_task(subagent_type="default", description="demo")
print(f"spawned: {task.id} status={task.status}")

print()
print("=== 5. delegate tool spawn path ===")
dobs = DelegateExecutor()(
    DelegateAction(command="spawn", ids=["s1"], agent_types=["general-purpose"]),
    parent,
)
print(f"is_error={dobs.is_error}")
print(f"text: {dobs.text}")
