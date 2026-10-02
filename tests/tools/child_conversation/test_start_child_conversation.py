import json
from uuid import UUID, uuid4

import httpx
import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.child_conversation import (
    StartChildConversationAction,
    StartChildConversationExecutor,
    StartChildConversationTool,
)


def _conv_state(tmp_path) -> ConversationState:
    return ConversationState(
        id=uuid4(),
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )


def _launch_payload(parent_id: UUID, child_id: UUID, **overrides) -> dict:
    payload = {
        "conversation_id": str(child_id),
        "parent_conversation_id": str(parent_id),
        "status": "idle",
        "title": "Docs",
        "url": None,
        "workspace": "/workspace",
        "isolation": "shared",
    }
    payload.update(overrides)
    return payload


def _executor(
    parent_id: UUID, handler, launch_url: str | None = None
) -> StartChildConversationExecutor:
    return StartChildConversationExecutor(
        parent_conversation_id=parent_id,
        launch_url=launch_url,
        transport=httpx.MockTransport(handler),
    )


def _recording_handler(seen: dict, payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=payload)

    return handler


def test_tool_name_is_start_child_conversation():
    assert StartChildConversationTool.name == "start_child_conversation"


def test_tool_binds_executor_to_parent_conversation(tmp_path):
    conv_state = _conv_state(tmp_path)

    (tool,) = StartChildConversationTool.create(
        conv_state, launch_url="https://cloud.example/children"
    )

    executor = tool.executor
    assert isinstance(executor, StartChildConversationExecutor)
    assert executor.parent_conversation_id == conv_state.id
    assert executor.launch_url == "https://cloud.example/children"


def test_tool_rejects_unknown_parameters(tmp_path):
    conv_state = _conv_state(tmp_path)

    with pytest.raises(ValueError, match="does not accept parameters"):
        StartChildConversationTool.create(conv_state, target="cloud")


def test_executor_posts_launch_request_to_own_agent_server(monkeypatch):
    monkeypatch.setenv("OH_INTERNAL_SERVER_URL", "http://agent-server:9000/")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "session-secret")
    parent_id, child_id = uuid4(), uuid4()
    seen: dict = {}
    executor = _executor(
        parent_id, _recording_handler(seen, _launch_payload(parent_id, child_id))
    )

    executor(StartChildConversationAction(task="Write the docs", title="Docs"))

    assert seen["url"] == (
        f"http://agent-server:9000/api/conversations/{parent_id}/children"
    )
    assert seen["headers"]["x-session-api-key"] == "session-secret"
    assert seen["body"] == {
        "task": "Write the docs",
        "title": "Docs",
        "isolation": "worktree",
    }


def test_executor_maps_launch_response_into_observation():
    parent_id, child_id = uuid4(), uuid4()
    payload = _launch_payload(
        parent_id, child_id, url=f"https://cloud.example/conversations/{child_id}"
    )
    executor = _executor(parent_id, lambda request: httpx.Response(201, json=payload))

    observation = executor(StartChildConversationAction(task="Write the docs"))

    assert observation.is_error is False
    assert observation.conversation_id == str(child_id)
    assert observation.parent_conversation_id == str(parent_id)
    assert observation.status == "idle"
    assert observation.title == "Docs"
    assert observation.url == f"https://cloud.example/conversations/{child_id}"
    assert observation.workspace == "/workspace"
    assert observation.isolation == "shared"
    assert str(child_id) in observation.text


def test_executor_uses_launch_url_override():
    parent_id, child_id = uuid4(), uuid4()
    launch_url = (
        f"https://cloud.example/api/v1/webhooks/conversations/{parent_id}/children"
    )
    seen: dict = {}
    executor = _executor(
        parent_id,
        _recording_handler(seen, _launch_payload(parent_id, child_id)),
        launch_url=launch_url,
    )

    executor(StartChildConversationAction(task="Write the docs"))

    assert seen["url"] == launch_url


def test_executor_omits_session_key_header_when_unset(monkeypatch):
    monkeypatch.delenv("OH_SESSION_API_KEYS_0", raising=False)
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    parent_id, child_id = uuid4(), uuid4()
    seen: dict = {}
    executor = _executor(
        parent_id, _recording_handler(seen, _launch_payload(parent_id, child_id))
    )

    executor(StartChildConversationAction(task="Write the docs"))

    assert "x-session-api-key" not in seen["headers"]


def test_executor_reports_http_error_detail():
    executor = _executor(
        uuid4(),
        lambda request: httpx.Response(
            404, json={"detail": "Parent conversation not found"}
        ),
    )

    observation = executor(StartChildConversationAction(task="anything"))

    assert observation.is_error is True
    assert "404" in observation.text
    assert "Parent conversation not found" in observation.text
    assert observation.conversation_id is None


def test_executor_reports_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    executor = _executor(uuid4(), handler)

    observation = executor(StartChildConversationAction(task="anything"))

    assert observation.is_error is True
    assert "timed out" in observation.text
