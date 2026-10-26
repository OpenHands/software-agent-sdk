"""Actual Python SDK creation, run, attach, request creation, and fork."""

import json
import os
import sys
from pathlib import Path

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.workspace import LocalWorkspace, RemoteWorkspace

working_dir = sys.argv[1]
workspace = RemoteWorkspace(
    host=os.environ["AGENT_SERVER_URL"],
    api_key=os.environ["SESSION_API_KEY"],
    working_dir=working_dir,
)
agent = Agent(llm=workspace.get_llm(max_output_tokens=128, num_retries=0), tools=[])
conversation = Conversation(
    agent=agent,
    workspace=workspace,
    max_budget_per_run=0.05,
    delete_on_close=False,
    visualizer=None,
)
assert isinstance(conversation, RemoteConversation)
assert conversation.max_budget_per_run == 0.05
conversation.send_message(
    "Call finish immediately with message PYTHON_BUDGET_OK. Do nothing else."
)
conversation.run()
response = workspace.client.get(f"/api/conversations/{conversation.id}")
response.raise_for_status()
info = response.json()
assert info["execution_status"] == "finished"
assert info["max_budget_per_run"] == 0.05
stats = conversation.conversation_stats.model_dump()
cost = sum(m["accumulated_cost"] for m in stats["usage_to_metrics"].values())
assert 0 < cost < 0.05
attached = RemoteConversation.attach(workspace, conversation.id, visualizer=None)
assert attached.max_budget_per_run == 0.05
forked = attached.fork(reset_metrics=False)
assert forked.max_budget_per_run == 0.05
assert forked.conversation_stats.model_dump() == stats
request = StartConversationRequest(
    agent=agent,
    workspace=LocalWorkspace(working_dir=working_dir),
    max_budget_per_run=0.02,
    autotitle=False,
)
created = RemoteConversation.create(workspace, request, visualizer=None)
assert created.max_budget_per_run == 0.02
same_id = request.model_copy(
    update={"conversation_id": created.id, "max_budget_per_run": 0.04}
)
repeated = RemoteConversation.create(workspace, same_id, visualizer=None)
assert repeated.id == created.id
assert repeated.max_budget_per_run == 0.02
summary = {
    "factory": str(conversation.id),
    "request": str(created.id),
    "fork": str(forked.id),
    "cap": conversation.max_budget_per_run,
    "cost": cost,
    "status": "finished",
    "reattach_cap": attached.max_budget_per_run,
    "idempotent_cap": repeated.max_budget_per_run,
}
for item in [repeated, created, forked, attached, conversation]:
    item.close()
workspace.client.close()
Path(".pr/live-review/python-summary.json").write_text(
    json.dumps(summary, indent=2) + "\n"
)
print("PYTHON_BUDGET_OK " + json.dumps(summary))
