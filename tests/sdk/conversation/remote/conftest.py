import json
import uuid
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.workspace import RemoteWorkspace


@pytest.fixture
def remote_transport_workspace() -> Iterator[
    tuple[RemoteWorkspace, Agent, list[httpx.Request]]
]:
    agent = Agent(llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test-key")), tools=[])
    records: dict[str, dict] = {}
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if request.method == "POST" and (
            path == "/api/conversations" or path.endswith("/fork")
        ):
            payload = json.loads(request.content)
            cid = payload.get("id") or str(uuid.uuid4())
            records[cid] = {
                "id": cid,
                "agent": agent.model_dump(mode="json"),
                "max_iterations": 500,
                "execution_status": "idle",
                "tags": {},
            }
            return httpx.Response(200, json=records[cid])
        if path.endswith("/events/search"):
            return httpx.Response(200, json={"items": [], "next_page_id": None})
        if request.method == "GET":
            cid = path.rsplit("/", 1)[-1]
            if cid in records:
                return httpx.Response(200, json=records[cid])
        return httpx.Response(404, json={"detail": "Not found"})

    workspace = RemoteWorkspace(
        host="http://event-transport.test", working_dir="/workspace", api_key="ws-key"
    )
    with httpx.Client(
        base_url=workspace.host,
        transport=httpx.MockTransport(handle),
    ) as client:
        workspace._client = client
        yield workspace, agent, requests
    workspace._client = None
