"""Retry behavior for the cloud workspace completion callback.

The server is a real local HTTP server. Exits go through ``with
OpenHandsCloudWorkspace``. These tests do not call OpenHands Cloud.
"""

from __future__ import annotations

import logging

import pytest

from openhands.workspace import OpenHandsCloudWorkspace
from tests.callback_http import callback_server
from tests.callback_retry_cases import RETRY_CASES, assert_case, begin_case


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


@pytest.mark.parametrize("case", RETRY_CASES, ids=lambda case: case.name)
def test_retry_case(monkeypatch, caplog, case):
    clock = begin_case(monkeypatch, case)
    caplog.set_level(logging.INFO)
    with callback_server(
        list(case.actions), stall_seconds=case.stall_seconds
    ) as server:
        workspace = _workspace(monkeypatch, server.url)
        caplog.clear()
        with workspace:
            pass

    assert_case(case, server, caplog, clock, _SECRET)


def _closed_after_cleanup(workspace: OpenHandsCloudWorkspace) -> None:
    client = workspace._client
    assert client is not None
    assert client.is_closed


def test_completed_payload_on_exit(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server([502, 200]) as server:
        workspace = _workspace(monkeypatch, server.url)
        workspace.client
        caplog.clear()
        with workspace:
            pass

    body = server.bodies[0]
    assert server.bodies[0] == server.bodies[1]
    assert body["status"] == "COMPLETED"
    assert body["run_id"] == "run-42"
    assert body["conversation_id"] == "conv-123"
    assert body["cost"] == 0.42
    assert "error" not in body
    _closed_after_cleanup(workspace)
    assert any(
        "Completion callback sent (COMPLETED): 200" in record.getMessage()
        for record in caplog.records
    )


def test_failed_exit_retries_and_keeps_the_error(monkeypatch):
    with callback_server([502, 200]) as server:
        workspace = _workspace(monkeypatch, server.url)
        workspace.client
        with pytest.raises(RuntimeError, match="script crashed"):
            with workspace:
                raise RuntimeError("script crashed")

    _closed_after_cleanup(workspace)
    assert server.bodies[0] == server.bodies[1]
    assert server.bodies[0]["status"] == "FAILED"
    assert server.bodies[0]["error"] == "script crashed"


def test_no_request_without_callback_url(monkeypatch):
    with callback_server([200]) as server:
        workspace = _workspace(monkeypatch, None)
        workspace.client
        with workspace:
            pass

    assert server.bodies == []
    _closed_after_cleanup(workspace)


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
    _closed_after_cleanup(workspace)
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert warnings == ["Completion callback failed (FAILED): RuntimeError"]
    assert "leaked-token" not in caplog.text
    assert "user:pw" not in caplog.text
    assert _SECRET not in caplog.text
