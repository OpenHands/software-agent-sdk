"""Transport fixtures for remote subagent execution."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from openhands.sdk import Agent
from openhands.sdk.conversation import LocalConversation, RemoteConversation
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.tool.builtins.finish import FinishAction, FinishTool
from openhands.sdk.workspace import RemoteWorkspace
from openhands.tools.task.workspace import SubagentWorkspace, SubagentWorkspaceReference


@pytest.fixture
def remote_subagents(monkeypatch, mock_llm, tmp_path):
    """Run the real remote proxy against isolated in-memory agent servers."""
    servers = {}
    workspaces = []
    lifecycle = []
    resources = {}
    workspace_ids = {}
    resolutions = []
    options = SimpleNamespace(
        confirm=False,
        fail_create=False,
        fail_policy=False,
        fail_events=False,
        acknowledge_budget=True,
    )

    def factory(child_id, agent_type):
        host = f"https://child-{len(workspaces)}.example"
        server = {
            "conversations": {},
            "creates": [],
            "rejections": [],
            "messages": [],
        }
        servers[host] = server

        def request(request):
            path = request.url.path
            payload = json.loads(request.content) if request.content else {}
            if request.method == "POST" and path == "/api/conversations":
                if options.fail_create:
                    return httpx.Response(400, json={"detail": "creation failed"})
                cid = payload.get("conversation_id", str(uuid.uuid4()))
                server["creates"].append(payload)
                server["conversations"][cid] = {
                    "id": cid,
                    "execution_status": "idle",
                    "stats": {},
                    "events": [],
                    "runs": 0,
                    **payload,
                }
                return httpx.Response(
                    200,
                    json={
                        "id": cid,
                        "max_budget_per_run": (
                            payload.get("max_budget_per_run")
                            if options.acknowledge_budget
                            else None
                        ),
                    },
                )
            parts = path.split("/")
            cid = parts[3]
            state = server["conversations"].get(cid)
            if state is None:
                return httpx.Response(404, json={"detail": "conversation is gone"})
            if request.method == "DELETE":
                lifecycle.append((host, "delete"))
                del server["conversations"][cid]
            elif path.endswith("/events/search"):
                if options.fail_events:
                    return httpx.Response(400, json={"detail": "event sync failed"})
                return httpx.Response(200, json={"items": state["events"]})
            elif path.endswith("/confirmation_policy"):
                if options.fail_policy:
                    return httpx.Response(400, json={"detail": "policy failed"})
                state["confirmation_policy"] = payload["policy"]
            elif path.endswith("/events/respond_to_confirmation"):
                server["rejections"].append(payload)
            elif path.endswith("/events") and request.method == "POST":
                server["messages"].append(payload)
            elif path.endswith("/run"):
                state["runs"] += 1
                if options.confirm and state["runs"] == 1:
                    action = FinishAction(message="Pending result")
                    event = ActionEvent(
                        thought=[],
                        action=action,
                        tool_name=FinishTool.name,
                        tool_call_id="finish-call",
                        tool_call=MessageToolCall(
                            origin="completion",
                            id="finish-call",
                            name=FinishTool.name,
                            arguments=action.model_dump_json(),
                        ),
                        llm_response_id="response",
                    )
                    state["execution_status"] = "waiting_for_confirmation"
                else:
                    event = MessageEvent(
                        source="agent",
                        llm_message=Message(
                            role="assistant",
                            content=[TextContent(text=f"Result {child_id}")],
                        ),
                    )
                    state["execution_status"] = "finished"
                state["events"].append(event.model_dump(mode="json"))
            elif request.method == "GET":
                return httpx.Response(200, json=state)
            return httpx.Response(200, json={})

        workspace = RemoteWorkspace(host=host, working_dir=f"/work/{child_id}")
        workspace._client = httpx.Client(
            base_url=host, transport=httpx.MockTransport(request)
        )
        identity = str(uuid.uuid4())
        workspace_ids[host] = identity
        resources[identity] = (host, request)
        workspaces.append(workspace)
        return workspace

    def persistent_factory(child_id, agent_type):
        workspace = factory(child_id, agent_type)
        return SubagentWorkspace(
            workspace=workspace,
            reference=SubagentWorkspaceReference(
                provider="test-provider",
                workspace_id=workspace_ids[workspace.host],
                working_dir=workspace.working_dir,
            ),
        )

    def resolver(reference):
        resolutions.append(reference)
        resource = resources.get(reference.workspace_id)
        if resource is None:
            return None
        host, request = resource
        workspace = RemoteWorkspace(
            host=host,
            working_dir=reference.working_dir,
            api_key="fresh-credential-from-provider",
        )
        workspace._client = httpx.Client(
            base_url=workspace.host, transport=httpx.MockTransport(request)
        )
        workspaces.append(workspace)
        return workspace

    def enter(workspace):
        lifecycle.append((workspace.host, "enter"))
        return workspace

    def leave(workspace, *args):
        lifecycle.append((workspace.host, "exit"))

    def wait(conversation, *args, **kwargs):
        conversation.state.refresh_from_server()
        conversation.state.events.reconcile()

    monkeypatch.setattr(RemoteWorkspace, "__enter__", enter)
    monkeypatch.setattr(RemoteWorkspace, "__exit__", leave)
    monkeypatch.setattr(RemoteConversation, "_wait_for_run_completion", wait)
    monkeypatch.setattr(
        "openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient",
        Mock(),
    )
    _reset_registry_for_tests()
    register_agent("remote-worker", lambda llm: Agent(llm=llm, tools=[]), "Worker")
    parent = LocalConversation(
        agent=Agent(llm=mock_llm, tools=[]),
        workspace=str(tmp_path),
        visualizer=None,
        persistence_dir=tmp_path / "parent",
        profile_store_dir=tmp_path / "profiles",
    )
    yield SimpleNamespace(
        factory=factory,
        persistent_factory=persistent_factory,
        resolver=resolver,
        resolutions=resolutions,
        resources=resources,
        servers=servers,
        workspaces=workspaces,
        lifecycle=lifecycle,
        options=options,
        parent=parent,
    )
    parent.close()
    for workspace in workspaces:
        workspace.reset_client()
    _reset_registry_for_tests()
