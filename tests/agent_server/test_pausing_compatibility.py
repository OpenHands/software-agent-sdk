"""Keep the existing wire status while clients adopt PAUSING."""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.conversation_service import WebhookSubscriber
from openhands.agent_server.dependencies import get_event_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.models import ConversationInfo, ConversationPage
from openhands.agent_server.sockets import _send_event
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.workspace import LocalWorkspace


@pytest.mark.parametrize("status", list(ConversationExecutionStatus))
def test_conversation_response_preserves_internal_status(tmp_path, status):
    info = ConversationInfo(
        id=uuid4(),
        agent=Agent(llm=LLM(model="test-model")),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        execution_status=status,
    )
    expected = "running" if status == ConversationExecutionStatus.PAUSING else status
    assert info.model_dump(mode="json")["execution_status"] == expected
    page = ConversationPage(items=[info])
    assert (
        json.loads(page.model_dump_json())["items"][0]["execution_status"] == expected
    )
    assert info.execution_status is status
    assert info.model_dump()["execution_status"] is status


def test_response_schema_keeps_the_existing_status_values():
    schema = ConversationInfo.model_json_schema(mode="serialization")
    status = schema["properties"]["execution_status"]
    if "$ref" in status:
        status = schema["$defs"][status["$ref"].rsplit("/", 1)[1]]
    assert set(status["enum"]) == {
        "idle",
        "running",
        "paused",
        "waiting_for_confirmation",
        "finished",
        "error",
        "stuck",
        "deleting",
    }


@pytest.mark.parametrize("full_state", [False, True])
@pytest.mark.parametrize(
    "transport", ["get", "batch", "search", "search_dict", "websocket", "webhook"]
)
@pytest.mark.asyncio
async def test_state_events_keep_wire_status_without_mutation(full_state, transport):
    value = (
        {"execution_status": "pausing", "title": "unchanged"}
        if full_state
        else "pausing"
    )
    event = ConversationStateUpdateEvent(
        key="full_state" if full_state else "execution_status", value=value
    )
    original = event.model_dump(mode="json")

    if transport == "websocket":
        socket = MagicMock()
        socket.send_json = AsyncMock()
        await _send_event(event, socket)
        payload = socket.send_json.call_args.args[0]
    elif transport == "webhook":
        payload = WebhookSubscriber._event_data(event)
    else:
        service = AsyncMock()
        service.get_event.return_value = event
        service.batch_get_events.return_value = [event, None]
        service.search_events.return_value = {
            "items": [original if transport == "search_dict" else event],
            "next_page_id": None,
        }
        app = FastAPI()
        app.include_router(event_router, prefix="/api")
        app.dependency_overrides[get_event_service] = lambda: service
        base = f"/api/conversations/{uuid4()}/events"
        with TestClient(app) as client:
            if transport == "get":
                response = client.get(f"{base}/{event.id}")
                payload = response.json()
            elif transport == "batch":
                response = client.request(
                    "GET", base, json=[str(event.id), str(uuid4())]
                )
                payload = response.json()[0]
                assert response.json()[1] is None
            else:
                response = client.get(f"{base}/search")
                payload = response.json()["items"][0]
            assert response.status_code == 200

    expected = (
        {"execution_status": "running", "title": "unchanged"}
        if full_state
        else "running"
    )
    assert payload["value"] == expected
    assert payload["id"] == original["id"]
    assert event.model_dump(mode="json") == original
    assert original["value"] == value
