"""Beats 1+2: resume-path refusal and the settings wire path.

Same script on both checkouts. No LLM calls anywhere.
"""

import tempfile

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task.manager import TaskManager


register_builtins_agents()

workspace = tempfile.mkdtemp(prefix="demo_disabled_agents_2_")
llm = LLM(model="gpt-4o", api_key=SecretStr("demo-key"), usage_id="demo")

try:
    ctx = AgentContext(disabled_agents=["general-purpose"])
    have_field = True
except Exception:
    ctx = AgentContext()
    have_field = False

agent = Agent(llm=llm, tools=[], agent_context=ctx)
parent = LocalConversation(
    agent=agent, workspace=workspace, visualizer=None, delete_on_close=False
)
manager = TaskManager()
manager._ensure_parent(parent)

print("=== Beat 1: resume with a since-disabled type ===")
task = manager._create_task(subagent_type="default", description="demo")
manager._evict_task(task)
print(f"created + evicted {task.id} (type was 'default')")

try:
    resumed = manager._resume_task(resume=task.id, subagent_type="general-purpose")
    print(f"resume as 'general-purpose': {resumed.status}  <- nothing refused it")
except ValueError as e:
    print(f"resume as 'general-purpose': refused: {e}")

resumed = manager._resume_task(resume=task.id, subagent_type="default")
print(f"resume as 'default': {resumed.status}  (resume itself works)")

print()
print("=== Beat 2: the wire path (what a frontend POST carries) ===")
body = {
    "agent_settings": {
        "agent_kind": "openhands",
        "llm": {"model": "gpt-4o", "api_key": "demo-key", "usage_id": "demo"},
        "agent_context": {"disabled_agents": ["general-purpose"]},
    }
}
sent = body["agent_settings"]["agent_context"]
print(f"client sends: agent_settings.agent_context = {sent}")

req = StartConversationRequest(
    agent_settings=body["agent_settings"],
    workspace=LocalWorkspace(working_dir=workspace),
)
built_ctx = req.agent.agent_context
landed = getattr(built_ctx, "disabled_agents", "<silently dropped during validation>")
print(f"server-side agent.agent_context.disabled_agents = {landed}")
