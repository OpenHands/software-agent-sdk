"""Retry behavior for the cloud workspace completion callback.

Exits go through ``with OpenHandsCloudWorkspace`` against a local HTTP server.
These tests do not call OpenHands Cloud. Retry-After parsing lives with the
base sender so it is not repeated here.
"""

from __future__ import annotations

import logging

import pytest

from openhands.workspace import OpenHandsCloudWorkspace
from tests.callback_http import callback_server
from tests.callback_retry_cases import NETWORK, assert_network


_SECRET = "callback-secret-token"


def _workspace(monkeypatch, url: str | None) -> OpenHandsCloudWorkspace:
    if url is None:
        monkeypatch.delenv("AUTOMATION_CALLBACK_URL", raising=False)
    else:
        monkeypatch.setenv("AUTOMATION_CALLBACK_URL", url)
    monkeypatch.setenv("AUTOMATION_RUN_ID", "run-42")
    workspace = OpenHandsCloudWorkspace(
        local_agent_server_mode=True,
        cloud_api_url="https://cloud.example.com",
        cloud_api_key=_SECRET,
    )
    workspace.register_conversation("conv-123")
    workspace.register_cost(0.42)
    return workspace


def _closed(workspace: OpenHandsCloudWorkspace) -> None:
    client = workspace._client
    assert client is not None
    assert client.is_closed


@pytest.mark.parametrize("actions,attempts,warning", NETWORK)
def test_network(monkeypatch, caplog, actions, attempts, warning):
    caplog.set_level(logging.INFO)
    with callback_server(list(actions)) as server:
        workspace = _workspace(monkeypatch, server.url)
        caplog.clear()
        with workspace:
            pass
    assert_network(server, caplog, actions, attempts, warning, _SECRET)


def test_completed_payload_on_exit(monkeypatch, caplog):
    """502 then 200 through a real exit. The before/after script uses this shape."""
    caplog.set_level(logging.INFO)
    with callback_server([502, 200]) as server:
        workspace = _workspace(monkeypatch, server.url)
        caplog.clear()
        with workspace:
            pass

    body = server.bodies[0]
    assert server.bodies == [body, body]
    assert body == {
        "status": "COMPLETED",
        "run_id": "run-42",
        "conversation_id": "conv-123",
        "cost": 0.42,
    }
    assert server.authorization == [f"Bearer {_SECRET}", f"Bearer {_SECRET}"]
    assert any(
        "Completion callback sent (COMPLETED): 200" in record.getMessage()
        for record in caplog.records
    )


def test_failed_exit_keeps_the_error_and_cleans_up(monkeypatch):
    with callback_server([200]) as server:
        workspace = _workspace(monkeypatch, server.url)
        workspace.client
        with pytest.raises(RuntimeError, match="script crashed"):
            with workspace:
                raise RuntimeError("script crashed")

    _closed(workspace)
    assert server.bodies == [
        {
            "status": "FAILED",
            "run_id": "run-42",
            "conversation_id": "conv-123",
            "cost": 0.42,
            "error": "script crashed",
        }
    ]


def test_no_request_without_callback_url(monkeypatch):
    with callback_server([200]) as server:
        workspace = _workspace(monkeypatch, None)
        workspace.client
        with workspace:
            pass
    assert server.bodies == []
    _closed(workspace)


def test_header_preparation_failure_stays_best_effort(monkeypatch, caplog):
    class _Key:
        def __format__(self, spec: str) -> str:
            raise RuntimeError("Bearer leaked-token https://user:pw@host")

    caplog.set_level(logging.INFO)
    with callback_server([200]) as server:
        workspace = _workspace(monkeypatch, server.url)
        workspace.client
        object.__setattr__(workspace, "cloud_api_key", _Key())
        caplog.clear()
        with pytest.raises(RuntimeError, match="body failed"):
            with workspace:
                raise RuntimeError("body failed")

    assert server.bodies == []
    _closed(workspace)
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert warnings == ["Completion callback failed (FAILED): RuntimeError"]
    assert "leaked-token" not in caplog.text
    assert _SECRET not in caplog.text
