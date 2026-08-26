"""Tests for a2a_router.py (A2A server-mode support).

Builds the A2A routers the same way test_conversation_router.py builds the
conversation router: a bare FastAPI app with a mocked ConversationService /
EventService injected via dependency_overrides.

Enablement (mount-on-demand) behavior is covered separately at the bottom via
``openhands.agent_server.api.create_app``.
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
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
from openhands.sdk.workspace import LocalWorkspace


def _build_app(session_api_keys=None):
    app = FastAPI()
    app.include_router(a2a_agent_card_router)
    app.include_router(a2a_router, prefix="/api")
    app.state.config = Config(
        static_files_path=None,
        session_api_keys=session_api_keys or [],
        secret_key=None,
    )
    return app


@pytest.fixture
def client():
    app = _build_app()
    return TestClient(app)


@pytest.fixture
def authed_client():
    app = _build_app(session_api_keys=["secret-key"])
    return TestClient(app)


@pytest.fixture
def mock_conversation_service():
    return AsyncMock(spec=ConversationService)


@pytest.fixture
def mock_event_service():
    return AsyncMock(spec=EventService)


@pytest.fixture
def sample_conversation_id():
    return uuid4()


@pytest.fixture
def sample_conversation_info(sample_conversation_id):
    now = utc_now()
    return ConversationInfo(
        id=sample_conversation_id,
        agent=Agent(
            llm=LLM(
                model="gpt-4o",
                api_key="test-key",
                usage_id="test-llm",
            ),
            tools=[Tool(name="TerminalTool")],
        ),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        execution_status=ConversationExecutionStatus.IDLE,
        title="Test Conversation",
        created_at=now,
        updated_at=now,
    )


def _override(client, mock_conversation_service, mock_event_service):
    mock_conversation_service.get_event_service.return_value = mock_event_service
    client.app.dependency_overrides[get_conversation_service] = lambda: (
        mock_conversation_service
    )


def _jsonrpc_body(method, params=None, rpc_id: str | int | None = 1):
    payload: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if rpc_id is not None:
        payload["id"] = rpc_id
    return payload


def _send_params(text="Hello from A2A!", task_id=None):
    message = {"role": "user", "parts": [{"kind": "text", "text": text}]}
    if task_id:
        message["taskId"] = task_id
    return {"message": message}


# ---------------------------------------------------------------------------
# Agent card
# ---------------------------------------------------------------------------


class TestAgentCard:
    def test_agent_card_at_well_known_root(self, client):
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
        card = response.json()
        assert card["name"] == "OpenHands Agent Server"
        assert card["capabilities"] == {
            "streaming": True,
            "pushNotifications": False,
        }
        assert card["defaultInputModes"] == ["text/plain"]
        assert card["defaultOutputModes"] == ["text/plain"]
        assert card["skills"] == []
        assert card["provider"]["organization"] == "OpenHands"
        assert card["url"].endswith("/api/a2a")

    def test_agent_card_requires_no_auth(self, authed_client):
        # Discovery is unauthenticated even when session keys are configured.
        response = authed_client.get("/.well-known/agent-card.json")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_rejected_without_key(self, authed_client):
        response = authed_client.post("/api/a2a", json=_jsonrpc_body("tasks/get"))
        assert response.status_code == 401

    def test_bearer_token_accepted(self, authed_client, mock_conversation_service):
        mock_conversation_service.get_event_service.return_value = None
        authed_client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = authed_client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "tasks/get", params={"id": str(uuid4())}, rpc_id=1
                ),
                headers={"Authorization": "Bearer secret-key"},
            )
            assert response.status_code == 200
            assert response.json()["error"]["code"] == -32001
        finally:
            authed_client.app.dependency_overrides.clear()

    def test_session_header_accepted(self, authed_client, mock_conversation_service):
        mock_conversation_service.get_event_service.return_value = None
        authed_client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = authed_client.post(
                "/api/a2a",
                json=_jsonrpc_body("tasks/get", params={"id": str(uuid4())}),
                headers={"X-Session-API-Key": "secret-key"},
            )
            assert response.status_code == 200
        finally:
            authed_client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# JSON-RPC errors (every error path must echo the request id)
# ---------------------------------------------------------------------------


class TestJSONRPCErrors:
    def test_parse_error(self, client, mock_conversation_service):
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                content=b"{not json",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["jsonrpc"] == "2.0"
            assert body["error"]["code"] == -32700
            # JSON-RPC 2.0 spec: parse errors echo a null id.
            assert body["id"] is None
        finally:
            client.app.dependency_overrides.clear()

    def test_method_not_found(self, client, mock_conversation_service):
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a", json=_jsonrpc_body("tasks/resubmit", rpc_id=7)
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == 7
            assert body["error"]["code"] == -32601
            assert "tasks/resubmit" in body["error"]["message"]
        finally:
            client.app.dependency_overrides.clear()

    def test_invalid_params_tasks_get(self, client, mock_conversation_service):
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a", json=_jsonrpc_body("tasks/get", rpc_id="err-1")
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "err-1"
            assert body["error"]["code"] == -32602
        finally:
            client.app.dependency_overrides.clear()

    def test_invalid_params_bad_task_id(self, client, mock_conversation_service):
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "tasks/get", params={"id": "not-a-uuid"}, rpc_id="err-2"
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "err-2"
            assert body["error"]["code"] == -32602
        finally:
            client.app.dependency_overrides.clear()

    def test_invalid_params_message_send(self, client, mock_conversation_service):
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "message/send", params={"message": {}}, rpc_id="err-3"
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "err-3"
            assert body["error"]["code"] == -32602
        finally:
            client.app.dependency_overrides.clear()

    def test_invalid_params_message_stream(self, client, mock_conversation_service):
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "message/stream", params={"message": {}}, rpc_id="err-4"
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "err-4"
            assert body["error"]["code"] == -32602
        finally:
            client.app.dependency_overrides.clear()

    def test_task_not_found_preserves_id(self, client, mock_conversation_service):
        mock_conversation_service.get_event_service.return_value = None
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "tasks/get", params={"id": str(uuid4())}, rpc_id="nf-1"
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "nf-1"
            assert body["error"]["code"] == -32001
        finally:
            client.app.dependency_overrides.clear()

    def test_task_not_found_send_preserves_id(
        self, client, mock_conversation_service
    ):
        # message/send against a bogus taskId must echo the rpc id too.
        mock_conversation_service.get_event_service.return_value = None
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "message/send",
                    params=_send_params(task_id=str(uuid4())),
                    rpc_id="nf-2",
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "nf-2"
            assert body["error"]["code"] == -32001
        finally:
            client.app.dependency_overrides.clear()

    def test_no_profile_error_preserves_id(
        self, client, mock_conversation_service, mock_event_service, monkeypatch
    ):
        from openhands.agent_server import a2a_router

        monkeypatch.setattr(a2a_router, "_resolve_agent_profile_id", lambda: None)
        _override(client, mock_conversation_service, mock_event_service)
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "message/send", params=_send_params(), rpc_id="np-1"
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "np-1"
            assert body["error"]["code"] == -32603
        finally:
            client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# message/send
# ---------------------------------------------------------------------------


class TestMessageSend:
    def test_creates_conversation_and_sends_message(
        self,
        client,
        mock_conversation_service,
        mock_event_service,
        sample_conversation_info,
        monkeypatch,
    ):
        from openhands.agent_server import a2a_router

        monkeypatch.setattr(
            a2a_router, "_resolve_agent_profile_id", lambda: str(uuid4())
        )
        mock_conversation_service.start_conversation.return_value = (
            sample_conversation_info,
            True,
        )
        mock_event_service.get_state.return_value = MagicMock(
            execution_status=ConversationExecutionStatus.IDLE
        )
        mock_event_service.get_agent_final_response.return_value = "A2A reply"
        _override(client, mock_conversation_service, mock_event_service)

        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "message/send", params=_send_params("Hello!"), rpc_id="req-1"
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["jsonrpc"] == "2.0"
            assert body["id"] == "req-1"
            assert "error" not in body or body["error"] is None
            task = body["result"]
            assert task["id"] == str(sample_conversation_info.id)
            assert task["status"]["state"] == "completed"
            assert task["artifacts"][0]["parts"][0]["text"] == "A2A reply"

            mock_conversation_service.start_conversation.assert_called_once()
            request_arg = mock_conversation_service.start_conversation.call_args[0][0]
            assert request_arg.agent_profile_id is not None
            mock_event_service.send_message.assert_called_once()
            message_arg = mock_event_service.send_message.call_args[0][0]
            assert message_arg.role == "user"
            assert message_arg.content[0].text == "Hello!"
            assert mock_event_service.send_message.call_args[1]["run"] is True
        finally:
            client.app.dependency_overrides.clear()

    def test_reuses_existing_task(
        self,
        client,
        mock_conversation_service,
        mock_event_service,
        sample_conversation_id,
    ):
        mock_event_service.get_state.return_value = MagicMock(
            execution_status=ConversationExecutionStatus.RUNNING
        )
        mock_event_service.get_agent_final_response.return_value = ""
        _override(client, mock_conversation_service, mock_event_service)

        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "message/send",
                    params=_send_params(
                        "follow-up", task_id=str(sample_conversation_id)
                    ),
                ),
            )
            assert response.status_code == 200
            task = response.json()["result"]
            assert task["id"] == str(sample_conversation_id)
            assert task["status"]["state"] == "working"
            mock_conversation_service.start_conversation.assert_not_called()
            mock_event_service.send_message.assert_called_once()
        finally:
            client.app.dependency_overrides.clear()

    def test_no_profile_configured(
        self, client, mock_conversation_service, mock_event_service, monkeypatch
    ):
        from openhands.agent_server import a2a_router

        monkeypatch.setattr(a2a_router, "_resolve_agent_profile_id", lambda: None)
        _override(client, mock_conversation_service, mock_event_service)
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body("message/send", params=_send_params()),
            )
            assert response.status_code == 200
            assert response.json()["error"]["code"] == -32603
        finally:
            client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# tasks/get, tasks/cancel
# ---------------------------------------------------------------------------


class TestTasks:
    def test_tasks_get(
        self,
        client,
        mock_conversation_service,
        mock_event_service,
        sample_conversation_id,
    ):
        mock_event_service.get_state.return_value = MagicMock(
            execution_status=ConversationExecutionStatus.FINISHED
        )
        mock_event_service.get_agent_final_response.return_value = "final answer"
        _override(client, mock_conversation_service, mock_event_service)

        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "tasks/get", params={"id": str(sample_conversation_id)}, rpc_id=2
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == 2
            task = body["result"]
            assert task["id"] == str(sample_conversation_id)
            assert task["status"]["state"] == "completed"
            assert task["artifacts"][0]["parts"][0]["text"] == "final answer"
        finally:
            client.app.dependency_overrides.clear()

    def test_tasks_get_not_found(self, client, mock_conversation_service):
        mock_conversation_service.get_event_service.return_value = None
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body("tasks/get", params={"id": str(uuid4())}),
            )
            assert response.status_code == 200
            assert response.json()["error"]["code"] == -32001
        finally:
            client.app.dependency_overrides.clear()

    def test_tasks_cancel(
        self, client, mock_conversation_service, sample_conversation_id
    ):
        mock_conversation_service.interrupt_conversation.return_value = True
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "tasks/cancel",
                    params={"id": str(sample_conversation_id)},
                    rpc_id=3,
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == 3
            assert body["result"]["status"]["state"] == "canceled"
            mock_conversation_service.interrupt_conversation.assert_called_once_with(
                sample_conversation_id
            )
        finally:
            client.app.dependency_overrides.clear()

    def test_tasks_cancel_not_found(
        self, client, mock_conversation_service, sample_conversation_id
    ):
        mock_conversation_service.interrupt_conversation.return_value = False
        client.app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        try:
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body(
                    "tasks/cancel",
                    params={"id": str(sample_conversation_id)},
                    rpc_id="cnf-1",
                ),
            )
            assert response.status_code == 200
            body = response.json()
            assert body["id"] == "cnf-1"
            assert body["error"]["code"] == -32001
        finally:
            client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# message/stream
# ---------------------------------------------------------------------------


class TestMessageStream:
    def test_stream_returns_sse(
        self,
        client,
        mock_conversation_service,
        mock_event_service,
        sample_conversation_info,
        monkeypatch,
    ):
        from openhands.agent_server import a2a_router
        from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent

        monkeypatch.setattr(
            a2a_router, "_resolve_agent_profile_id", lambda: str(uuid4())
        )
        mock_conversation_service.start_conversation.return_value = (
            sample_conversation_info,
            True,
        )
        mock_event_service.get_state.return_value = MagicMock(
            execution_status=ConversationExecutionStatus.IDLE
        )
        mock_event_service.get_agent_final_response.return_value = "streamed reply"

        async def fake_subscribe(subscriber):
            await subscriber(
                ConversationStateUpdateEvent(key="execution_status", value="running")
            )
            await subscriber(
                ConversationStateUpdateEvent(key="execution_status", value="finished")
            )
            return uuid4()

        mock_event_service.subscribe_to_events.side_effect = fake_subscribe
        _override(client, mock_conversation_service, mock_event_service)

        try:
            with client.stream(
                "POST",
                "/api/a2a",
                json=_jsonrpc_body("message/stream", params=_send_params("Hi")),
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith(
                    "text/event-stream"
                )
                events = []
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
            assert events, "expected at least one SSE data event"
            # First event: the submitted task snapshot.
            assert events[0]["result"]["status"]["state"] == "submitted"
            states = [
                e["result"]["status"]["state"]
                for e in events
                if e["result"]["kind"] == "status-update"
            ]
            assert "working" in states
            terminal = [e for e in events if e["result"]["kind"] == "task"][-1]
            assert terminal["result"]["status"]["state"] == "completed"
            final_status = [
                e for e in events if e["result"].get("final") is True
            ]
            assert final_status, "expected a final=true status update"
            mock_event_service.send_message.assert_called_once()
        finally:
            client.app.dependency_overrides.clear()

    def test_stream_ignores_initial_idle_snapshot(
        self,
        client,
        mock_conversation_service,
        mock_event_service,
        sample_conversation_info,
        monkeypatch,
    ):
        """Regression test for the message/stream IDLE race.

        ``subscribe_to_events`` immediately replays the conversation's
        CURRENT status to a new subscriber. If that snapshot is terminal
        (e.g. IDLE before the run has started) the stream must NOT close on
        it; it must keep going and deliver the real terminal state and the
        final artifact.
        """
        from openhands.agent_server import a2a_router
        from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent

        monkeypatch.setattr(
            a2a_router, "_resolve_agent_profile_id", lambda: str(uuid4())
        )
        mock_conversation_service.start_conversation.return_value = (
            sample_conversation_info,
            True,
        )
        mock_event_service.get_state.return_value = MagicMock(
            execution_status=ConversationExecutionStatus.IDLE
        )
        mock_event_service.get_agent_final_response.return_value = "raced reply"

        async def fake_subscribe(subscriber):
            # Replay the pre-run IDLE snapshot FIRST — this used to close the
            # stream before the run started.
            await subscriber(
                ConversationStateUpdateEvent(key="execution_status", value="idle")
            )
            await subscriber(
                ConversationStateUpdateEvent(key="execution_status", value="running")
            )
            await subscriber(
                ConversationStateUpdateEvent(key="execution_status", value="idle")
            )
            return uuid4()

        mock_event_service.subscribe_to_events.side_effect = fake_subscribe
        _override(client, mock_conversation_service, mock_event_service)

        try:
            with client.stream(
                "POST",
                "/api/a2a",
                json=_jsonrpc_body(
                    "message/stream", params=_send_params("race"), rpc_id="race-1"
                ),
            ) as response:
                assert response.status_code == 200
                events = []
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
            states = [
                e["result"]["status"]["state"]
                for e in events
                if e["result"]["kind"] == "status-update"
            ]
            # The pre-run IDLE snapshot must not leak out as a completed /
            # final update, and must not terminate the stream early.
            assert states[0] != "completed"
            terminal_tasks = [e for e in events if e["result"]["kind"] == "task"]
            assert terminal_tasks, "stream never delivered the terminal Task"
            final = terminal_tasks[-1]["result"]
            assert final["status"]["state"] == "completed"
            assert final["artifacts"][0]["parts"][0]["text"] == "raced reply"
            final_updates = [e for e in events if e["result"].get("final") is True]
            assert len(final_updates) == 1, "exactly one final status update"
            assert final_updates[0]["id"] == "race-1"
            mock_event_service.send_message.assert_called_once()
        finally:
            client.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Enablement: mounted only when a2a_enabled AND a2a-sdk importable
# ---------------------------------------------------------------------------


class TestEnablement:
    def test_disabled_by_default_404(self, tmp_path):
        from openhands.agent_server.api import create_app

        config = Config(static_files_path=None, secret_key=None)
        assert config.a2a_enabled is False
        app = create_app(config)
        client = TestClient(app)
        assert client.post(
            "/api/a2a", json=_jsonrpc_body("tasks/get")
        ).status_code == 404
        assert client.get("/.well-known/agent-card.json").status_code == 404

    def test_enabled_mounts_routes(self, tmp_path):
        from unittest.mock import AsyncMock

        from openhands.agent_server.api import create_app
        from openhands.agent_server.dependencies import get_conversation_service

        mock_conversation_service = AsyncMock(spec=ConversationService)
        mock_conversation_service.get_event_service.return_value = None

        config = Config(
            static_files_path=None,
            secret_key=None,
            workspace_path=tmp_path,
            a2a_enabled=True,
        )
        app = create_app(config)
        app.dependency_overrides[get_conversation_service] = lambda: (
            mock_conversation_service
        )
        client = TestClient(app)
        try:
            # No auth keys configured: an unauthenticated tasks/get for a
            # random id reaches the JSON-RPC handler (200 + task-not-found).
            response = client.post(
                "/api/a2a",
                json=_jsonrpc_body("tasks/get", params={"id": str(uuid4())}),
            )
            assert response.status_code == 200
            assert response.json()["error"]["code"] == -32001
            card = client.get("/.well-known/agent-card.json")
            assert card.status_code == 200
            assert card.json()["name"] == "OpenHands Agent Server"
        finally:
            app.dependency_overrides.clear()

    def test_enabled_without_sdk_does_not_mount(self, tmp_path, monkeypatch):
        import builtins

        from openhands.agent_server import api as api_module

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "openhands.agent_server.a2a_router" or name.startswith("a2a"):
                raise ImportError("No module named 'a2a' (simulated)")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        config = Config(
            static_files_path=None,
            secret_key=None,
            a2a_enabled=True,
        )
        app = api_module.create_app(config)
        client = TestClient(app)
        assert client.post(
            "/api/a2a", json=_jsonrpc_body("tasks/get")
        ).status_code == 404
        assert client.get("/.well-known/agent-card.json").status_code == 404

    def test_env_var_enables(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OH_A2A_ENABLED", "true")
        from openhands.agent_server.api import create_app
        from openhands.agent_server.config import load_config

        loaded = load_config(tmp_path / "nonexistent.json")
        assert loaded.a2a_enabled is True
