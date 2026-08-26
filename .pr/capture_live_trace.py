"""Live capture of the A2A flow for .pr/live-trace.md."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.a2a_router import (
    a2a_agent_card_router,
    a2a_router,
    get_conversation_service,
)
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import ConversationInfo
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.workspace import LocalWorkspace

out: list[str] = []

def emit(title, detail):
    out.append(f"### {title}\n\n```{detail}\n")

app = FastAPI()
app.include_router(a2a_agent_card_router)
app.include_router(a2a_router, prefix="/api")
app.state.config = Config(static_files_path=None, session_api_keys=[], secret_key=None)
client = TestClient(app)

conv_id = uuid4()
info = ConversationInfo(
    id=conv_id,
    agent=Agent(
        llm=LLM(model="gpt-4o", api_key="k", usage_id="trace-llm"),
        tools=[Tool(name="TerminalTool")],
    ),
    workspace=LocalWorkspace(working_dir="/tmp/trace"),
    execution_status=ConversationExecutionStatus.IDLE,
    title="Trace Conversation",
    created_at=utc_now(),
    updated_at=utc_now(),
)

conv = MagicMock(spec=ConversationService)
ev = MagicMock(spec=EventService)
conv.start_conversation = AsyncMock(return_value=(info, True))
ev.get_state = AsyncMock(
    return_value=MagicMock(execution_status=ConversationExecutionStatus.IDLE)
)
ev.get_agent_final_response = AsyncMock(
    return_value="The capital of France is Paris."
)


async def subscribe(subscriber):
    # Replay a pre-run IDLE snapshot first — the exact race condition fixed
    # in this PR — then the real run lifecycle.
    await subscriber(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await subscriber(
        ConversationStateUpdateEvent(key="execution_status", value="running")
    )
    await subscriber(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    return uuid4()


ev.subscribe_to_events.side_effect = subscribe
ev.unsubscribe_from_events = AsyncMock()
conv.get_event_service.return_value = ev
app.dependency_overrides[get_conversation_service] = lambda: conv

import openhands.agent_server.a2a_router as a2a_router_module

a2a_router_module._resolve_agent_profile_id = lambda: str(uuid4())

# 1. Agent card
r = client.get("/.well-known/agent-card.json")
emit(
    "GET /.well-known/agent-card.json",
    f"HTTP {r.status_code} {r.headers.get('content-type')}\n"
    + json.dumps(r.json(), indent=2),
)

# 2. message/send
body = {
    "jsonrpc": "2.0",
    "id": "send-1",
    "method": "message/send",
    "params": {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": "What is the capital of France?"}],
        }
    },
}
r = client.post("/api/a2a", json=body)
print("SEND:", r.status_code, r.text[:600])
emit(
    "POST /api/a2a — message/send",
    "> "
    + json.dumps(body)
    + f"\n\n< HTTP {r.status_code}\n"
    + json.dumps(r.json(), indent=2),
)
assert "result" in r.json(), r.text
task_id = r.json()["result"]["id"]

# 3. tasks/get
body = {"jsonrpc": "2.0", "id": 42, "method": "tasks/get", "params": {"id": task_id}}
r = client.post("/api/a2a", json=body)
emit(
    "POST /api/a2a — tasks/get",
    "> "
    + json.dumps(body)
    + f"\n\n< HTTP {r.status_code}\n"
    + json.dumps(r.json(), indent=2),
)

# 4. message/stream (SSE frames)
body = {
    "jsonrpc": "2.0",
    "id": "stream-1",
    "method": "message/stream",
    "params": {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": "Stream me a reply"}],
            "taskId": task_id,
        }
    },
}
frames = []
with client.stream("POST", "/api/a2a", json=body) as r:
    ctype = r.headers.get("content-type")
    for line in r.iter_lines():
        if line.startswith("data: "):
            frames.append(json.loads(line[6:]))
emit(
    "POST /api/a2a — message/stream (SSE)",
    "> "
    + json.dumps(body)
    + f"\n\n< HTTP {r.status_code} {ctype}\n"
    + "\n".join(f"data: {json.dumps(f)}" for f in frames),
)

# 5. tasks/cancel
conv.interrupt_conversation = AsyncMock(return_value=True)
body = {
    "jsonrpc": "2.0",
    "id": "cancel-1",
    "method": "tasks/cancel",
    "params": {"id": task_id},
}
r = client.post("/api/a2a", json=body)
emit(
    "POST /api/a2a — tasks/cancel",
    "> "
    + json.dumps(body)
    + f"\n\n< HTTP {r.status_code}\n"
    + json.dumps(r.json(), indent=2),
)

# 6. error: task not found (id preserved)
body = {
    "jsonrpc": "2.0",
    "id": "err-task-1",
    "method": "tasks/get",
    "params": {"id": str(uuid4())},
}
r = client.post("/api/a2a", json=body)
emit(
    "POST /api/a2a — tasks/get (unknown task; id echoed)",
    "> "
    + json.dumps(body)
    + f"\n\n< HTTP {r.status_code}\n"
    + json.dumps(r.json(), indent=2),
)

# 7. error: invalid params (id preserved)
body = {"jsonrpc": "2.0", "id": "err-params-1", "method": "tasks/get"}
r = client.post("/api/a2a", json=body)
emit(
    "POST /api/a2a — tasks/get (missing params; id echoed)",
    "> "
    + json.dumps(body)
    + f"\n\n< HTTP {r.status_code}\n"
    + json.dumps(r.json(), indent=2),
)

# 8. error: parse error (id null per spec)
r = client.post(
    "/api/a2a",
    content=b"{not json",
    headers={"Content-Type": "application/json"},
)
emit(
    "POST /api/a2a — parse error (id null per JSON-RPC 2.0)",
    "> {not json\n\n< HTTP "
    + str(r.status_code)
    + "\n"
    + json.dumps(r.json(), indent=2),
)

# 9. method not found
body = {"jsonrpc": "2.0", "id": 99, "method": "tasks/resubmit"}
r = client.post("/api/a2a", json=body)
emit(
    "POST /api/a2a — method not found (id echoed)",
    "> "
    + json.dumps(body)
    + f"\n\n< HTTP {r.status_code}\n"
    + json.dumps(r.json(), indent=2),
)

# 10. disabled-by-default check via real create_app
from openhands.agent_server.api import create_app

cfg = Config(static_files_path=None, secret_key=None, a2a_enabled=False)
c2 = TestClient(create_app(cfg))
r1 = c2.post("/api/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get"})
r2 = c2.get("/.well-known/agent-card.json")
emit(
    "Default config (a2a_enabled=False) — routes not mounted",
    f"> POST /api/a2a tasks/get\n< HTTP {r1.status_code}\n"
    f"> GET /.well-known/agent-card.json\n< HTTP {r2.status_code}",
)

cfg_on = Config(static_files_path=None, secret_key=None, a2a_enabled=True)
c3 = TestClient(create_app(cfg_on))
r3 = c3.get("/.well-known/agent-card.json")
emit(
    "a2a_enabled=True — routes mounted",
    f"> GET /.well-known/agent-card.json\n< HTTP {r3.status_code}",
)

Path(".pr/live-trace-body.md").write_text("\n".join(out))
print("wrote", sum(1 for l in out if l.startswith("### ")), "sections")
