"""Tests for conversation tags in the API layer."""

import asyncio
import json
import threading
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConversationInfo,
    StoredConversation,
    UpdateConversationRequest,
)
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def mock_conversation_service():
    return AsyncMock(spec=ConversationService)


@pytest.fixture
def mock_event_service():
    return AsyncMock(spec=EventService)


@pytest.fixture
def sample_conversation_info():
    now = utc_now()
    return ConversationInfo(
        id=uuid4(),
        agent=Agent(
            llm=LLM(
                model="gpt-4o",
                api_key=SecretStr("test-key"),
                usage_id="test-llm",
            ),
            tools=[Tool(name="TerminalTool")],
        ),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        execution_status=ConversationExecutionStatus.IDLE,
        title="Test Conversation",
        tags={"env": "test", "team": "backend"},
        created_at=now,
        updated_at=now,
    )


def test_start_conversation_with_tags(
    client, mock_conversation_service, sample_conversation_info
):
    """Tags are forwarded to the service when starting a conversation."""
    mock_conversation_service.start_conversation.return_value = (
        sample_conversation_info,
        True,
    )
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [{"name": "TerminalTool"}],
            },
            "workspace": {"working_dir": "/tmp/test"},
            "tags": {"env": "prod", "team": "infra"},
        }
        response = client.post("/api/conversations", json=request_data)

        assert response.status_code == 201
        call_args = mock_conversation_service.start_conversation.call_args
        request_arg = call_args[0][0]
        assert request_arg.tags == {"env": "prod", "team": "infra"}
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_without_tags(
    client, mock_conversation_service, sample_conversation_info
):
    """Starting without tags defaults to empty dict."""
    mock_conversation_service.start_conversation.return_value = (
        sample_conversation_info,
        True,
    )
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [{"name": "TerminalTool"}],
            },
            "workspace": {"working_dir": "/tmp/test"},
        }
        response = client.post("/api/conversations", json=request_data)

        assert response.status_code == 201
        call_args = mock_conversation_service.start_conversation.call_args
        request_arg = call_args[0][0]
        assert request_arg.tags == {}
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_invalid_tag_key(client, mock_conversation_service):
    """Invalid tag keys are rejected with 422."""
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [{"name": "TerminalTool"}],
            },
            "workspace": {"working_dir": "/tmp/test"},
            "tags": {"INVALID-KEY": "value"},
        }
        response = client.post("/api/conversations", json=request_data)
        assert response.status_code == 422
    finally:
        client.app.dependency_overrides.clear()


def test_update_conversation_tags(client, mock_conversation_service):
    """PATCH endpoint updates tags."""
    mock_conversation_service.update_conversation.return_value = True
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.patch(
            f"/api/conversations/{conversation_id}",
            json={"tags": {"env": "staging"}},
        )

        assert response.status_code == 200
        assert response.json() == {"success": True}
        call_args = mock_conversation_service.update_conversation.call_args
        request_arg = call_args[0][1]
        assert isinstance(request_arg, UpdateConversationRequest)
        assert request_arg.tags == {"env": "staging"}
        assert request_arg.title is None
    finally:
        client.app.dependency_overrides.clear()


def test_update_conversation_title_and_tags(client, mock_conversation_service):
    """PATCH endpoint can update both title and tags."""
    mock_conversation_service.update_conversation.return_value = True
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.patch(
            f"/api/conversations/{conversation_id}",
            json={"title": "New Title", "tags": {"env": "prod"}},
        )

        assert response.status_code == 200
        call_args = mock_conversation_service.update_conversation.call_args
        request_arg = call_args[0][1]
        assert request_arg.title == "New Title"
        assert request_arg.tags == {"env": "prod"}
    finally:
        client.app.dependency_overrides.clear()


def test_get_conversation_includes_tags(
    client, mock_conversation_service, sample_conversation_info
):
    """GET endpoint returns tags in response."""
    mock_conversation_service.get_conversation.return_value = sample_conversation_info
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(f"/api/conversations/{sample_conversation_info.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["tags"] == {"env": "test", "team": "backend"}
    finally:
        client.app.dependency_overrides.clear()


def test_set_conversation_tag(client, mock_conversation_service):
    """PUT /tags/{key} sets a single tag without touching others."""
    mock_conversation_service.set_conversation_tag = AsyncMock(return_value=True)
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.put(
            f"/api/conversations/{conversation_id}/tags/env",
            json={"value": "production"},
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
        mock_conversation_service.set_conversation_tag.assert_awaited_once_with(
            conversation_id, "env", "production"
        )
    finally:
        client.app.dependency_overrides.clear()


def test_set_conversation_tag_missing_conversation(client, mock_conversation_service):
    """PUT /tags/{key} returns 404 when the conversation does not exist."""
    mock_conversation_service.set_conversation_tag = AsyncMock(return_value=False)
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.put(
            f"/api/conversations/{conversation_id}/tags/env",
            json={"value": "production"},
        )
        assert response.status_code == 404
    finally:
        client.app.dependency_overrides.clear()


def test_set_conversation_tag_invalid_key(client, mock_conversation_service):
    """PUT /tags/{key} returns 422 for an invalid tag key."""
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.put(
            f"/api/conversations/{conversation_id}/tags/INVALID-KEY",
            json={"value": "value"},
        )
        assert response.status_code == 422
    finally:
        client.app.dependency_overrides.clear()


def test_delete_conversation_tag(client, mock_conversation_service):
    """DELETE /tags/{key} removes a single tag."""
    mock_conversation_service.delete_conversation_tag = AsyncMock(return_value=True)
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.delete(
            f"/api/conversations/{conversation_id}/tags/env",
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
        mock_conversation_service.delete_conversation_tag.assert_awaited_once_with(
            conversation_id, "env"
        )
    finally:
        client.app.dependency_overrides.clear()


def test_delete_conversation_tag_invalid_key(client, mock_conversation_service):
    """DELETE /tags/{key} returns 422 for an invalid tag key."""
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.delete(
            f"/api/conversations/{conversation_id}/tags/INVALID-KEY",
        )
        assert response.status_code == 422
    finally:
        client.app.dependency_overrides.clear()


def test_delete_conversation_tag_missing_conversation(
    client, mock_conversation_service
):
    """DELETE /tags/{key} returns 404 when the conversation does not exist."""
    mock_conversation_service.delete_conversation_tag = AsyncMock(return_value=False)
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.delete(
            f"/api/conversations/{conversation_id}/tags/env",
        )
        assert response.status_code == 404
        assert "Conversation not found" in response.json()["detail"]
    finally:
        client.app.dependency_overrides.clear()


def test_delete_conversation_tag_missing_key(client, mock_conversation_service):
    """DELETE /tags/{key} returns 404 when the key is not present."""
    mock_conversation_service.delete_conversation_tag = AsyncMock(return_value=None)
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.delete(
            f"/api/conversations/{conversation_id}/tags/nosuchkey",
        )
        assert response.status_code == 404
        assert "Tag key not found" in response.json()["detail"]
    finally:
        client.app.dependency_overrides.clear()


def test_set_tag_does_not_affect_other_tags(client, mock_conversation_service):
    """PUT /tags/{key} overwrites only the specified key."""
    mock_conversation_service.set_conversation_tag = AsyncMock(return_value=True)
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    conversation_id = uuid4()
    try:
        response = client.put(
            f"/api/conversations/{conversation_id}/tags/repo",
            json={"value": "myorg/myrepo"},
        )
        assert response.status_code == 200
        mock_conversation_service.set_conversation_tag.assert_awaited_once_with(
            conversation_id, "repo", "myorg/myrepo"
        )
        # The service is responsible for leaving other keys untouched;
        # here we verify only one key was passed (not the full tag map).
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_event_service_start_forwards_tags_to_local_conversation(tmp_path):
    """EventService.start() must pass stored tags to LocalConversation.

    Regression test for https://github.com/OpenHands/software-agent-sdk/issues/2821:
    tags sent via POST /api/conversations were persisted in StoredConversation but
    not forwarded to the LocalConversation constructor, so state.tags was always {}.
    """
    tags = {"source": "pipeline", "symbol": "gold"}
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        confirmation_policy=NeverConfirm(),
        tags=tags,
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )

    event_service = EventService(
        stored=stored,
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        conversations_dir=tmp_path / "conversations",
    )

    with patch(
        "openhands.agent_server.event_service.LocalConversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_state = MagicMock()
        mock_state.execution_status = ConversationExecutionStatus.IDLE
        mock_state.events = []
        mock_agent = MagicMock()
        mock_agent.get_all_llms.return_value = []
        mock_conv._state = mock_state
        mock_conv.state = mock_state
        mock_conv.agent = mock_agent
        mock_conv._on_event = MagicMock()
        MockConversation.return_value = mock_conv

        await event_service.start()

        # Verify LocalConversation was called with the correct tags
        MockConversation.assert_called_once()
        call_kwargs = MockConversation.call_args.kwargs
        assert call_kwargs["tags"] == tags


@pytest.mark.asyncio
async def test_event_service_start_forwards_observability_span_name(tmp_path):
    """EventService.start() must pass stored child span names to LocalConversation."""
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        confirmation_policy=NeverConfirm(),
        observability_span_name="pr_review_evaluation",
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )

    event_service = EventService(
        stored=stored,
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        conversations_dir=tmp_path / "conversations",
    )

    with patch(
        "openhands.agent_server.event_service.LocalConversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_state = MagicMock()
        mock_state.execution_status = ConversationExecutionStatus.IDLE
        mock_state.events = []
        mock_agent = MagicMock()
        mock_agent.get_all_llms.return_value = []
        mock_conv._state = mock_state
        mock_conv.state = mock_state
        mock_conv.agent = mock_agent
        mock_conv._on_event = MagicMock()
        MockConversation.return_value = mock_conv

        await event_service.start()

        MockConversation.assert_called_once()
        call_kwargs = MockConversation.call_args.kwargs
        assert call_kwargs["observability_span_name"] == "pr_review_evaluation"


@pytest.fixture
async def tag_service(tmp_path):
    """A ConversationService holding one conversation backed by a real state."""
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(tmp_path / "workspace")),
        confirmation_policy=NeverConfirm(),
        tags={"env": "test"},
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    conversations_dir = tmp_path / "conversations"
    conv_dir = conversations_dir / stored.id.hex
    conv_dir.mkdir(parents=True)
    (conv_dir / "meta.json").write_text(stored.model_dump_json())

    # Build an EventService that bypasses LocalConversation startup.
    event_service = EventService(
        stored=stored,
        conversations_dir=conversations_dir,
        agent=None,
    )
    state = ConversationState.create(
        id=stored.id,
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=stored.workspace,
        persistence_dir=str(conv_dir),
        tags=dict(stored.tags),
    )
    event_service._conversation = MagicMock()
    event_service._conversation._state = state

    with patch(
        "openhands.agent_server.conversation_service"
        "._compose_webhook_conversation_info_sync",
        return_value=MagicMock(),
    ):
        async with ConversationService(conversations_dir=conversations_dir) as service:
            assert service._event_services is not None
            service._event_services[stored.id] = event_service
            yield service, event_service, state, conv_dir


def _persisted_tags(conv_dir) -> tuple[dict[str, str], dict[str, str]]:
    meta = json.loads((conv_dir / "meta.json").read_text())
    base_state = json.loads((conv_dir / "base_state.json").read_text())
    return meta.get("tags", {}), base_state.get("tags", {})


@pytest.mark.asyncio
async def test_set_delete_tag_persists_to_disk(tag_service):
    """set/delete_conversation_tag keep state and meta.json in sync on disk."""
    service, event_service, state, conv_dir = tag_service
    original_updated_at = event_service.stored.updated_at

    assert await service.set_conversation_tag(state.id, "team", "backend") is True
    assert state.tags == {"env": "test", "team": "backend"}
    assert _persisted_tags(conv_dir) == (state.tags, state.tags)

    assert await service.delete_conversation_tag(state.id, "env") is True
    assert state.tags == {"team": "backend"}
    assert _persisted_tags(conv_dir) == (state.tags, state.tags)

    assert await service.delete_conversation_tag(state.id, "env") is None
    assert event_service.stored.updated_at == original_updated_at


@pytest.mark.asyncio
async def test_concurrent_tag_sets_keep_every_key(tag_service, monkeypatch):
    """A tag write that waits on the state lock must not drop a key set meanwhile."""
    service, event_service, state, conv_dir = tag_service
    first_waiting = threading.Event()
    release_first = threading.Event()
    original_enter = ConversationState.__enter__
    calls = 0

    def gated_enter(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_waiting.set()
            release_first.wait(timeout=5)
        return original_enter(self)

    monkeypatch.setattr(ConversationState, "__enter__", gated_enter)

    first = asyncio.create_task(service.set_conversation_tag(state.id, "a", "1"))
    await asyncio.to_thread(first_waiting.wait, 5)
    assert await service.set_conversation_tag(state.id, "b", "2") is True
    release_first.set()
    assert await first is True

    expected = {"env": "test", "a": "1", "b": "2"}
    assert state.tags == expected
    assert event_service.stored.tags == expected
    assert _persisted_tags(conv_dir) == (expected, expected)
