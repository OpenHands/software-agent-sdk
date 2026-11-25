"""Retry behavior for the base workspace completion callback.

The server is a real local HTTP server. Exits go through ``with
LocalWorkspace``. These tests do not call OpenHands Cloud.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from openhands.sdk.workspace.base import BaseWorkspace
from openhands.sdk.workspace.local import LocalWorkspace
from tests.callback_http import callback_server
from tests.callback_retry_cases import RETRY_CASES, assert_case, begin_case


_SECRET = "callback-secret-token"


def _workspace(tmp_path, monkeypatch, url: str | None) -> LocalWorkspace:
    if url is None:
        monkeypatch.delenv("AUTOMATION_CALLBACK_URL", raising=False)
    else:
        monkeypatch.setenv("AUTOMATION_CALLBACK_URL", url)
    monkeypatch.setenv("AUTOMATION_CALLBACK_API_KEY", _SECRET)
    monkeypatch.setenv("AUTOMATION_RUN_ID", "run-42")
    workspace = LocalWorkspace(working_dir=tmp_path)
    workspace._conversation_id = "conv-123"
    workspace.register_cost(0.42)
    return workspace


@pytest.mark.parametrize("case", RETRY_CASES, ids=lambda case: case.name)
def test_retry_case(tmp_path, monkeypatch, caplog, case):
    clock = begin_case(monkeypatch, case)
    caplog.set_level(logging.INFO)
    with callback_server(
        list(case.actions), stall_seconds=case.stall_seconds
    ) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        with workspace:
            pass

    assert_case(case, server, caplog, clock, _SECRET)


def test_completed_payload_on_exit(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server([502, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
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
    assert any(
        "Completion callback sent (COMPLETED): 200" in record.getMessage()
        for record in caplog.records
    )


def test_failed_exit_retries_and_keeps_the_error(tmp_path, monkeypatch):
    exits = {"n": 0}
    original = BaseWorkspace.__exit__

    def _exit(self, exc_type, exc_val, exc_tb):
        exits["n"] += 1
        return original(self, exc_type, exc_val, exc_tb)

    with callback_server([502, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        with (
            patch.object(BaseWorkspace, "__exit__", _exit),
            pytest.raises(RuntimeError, match="script crashed"),
        ):
            with workspace:
                raise RuntimeError("script crashed")

    assert exits["n"] == 1
    assert server.bodies[0] == server.bodies[1]
    assert server.bodies[0]["status"] == "FAILED"
    assert server.bodies[0]["error"]["code"] == "RuntimeError"
    assert server.bodies[0]["error"]["detail"] == "script crashed"


def test_no_request_without_callback_url(tmp_path, monkeypatch):
    with callback_server([200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, None)
        with workspace:
            pass

    assert server.bodies == []


def test_header_preparation_failure_stays_best_effort(tmp_path, monkeypatch, caplog):
    def boom() -> dict[str, str]:
        raise RuntimeError("https://user:token@example.test/callback")

    monkeypatch.setattr(
        "openhands.sdk.workspace.base.observability_headers_from_env",
        boom,
    )
    exits = {"n": 0}
    original = BaseWorkspace.__exit__

    def _exit(self, exc_type, exc_val, exc_tb):
        exits["n"] += 1
        return original(self, exc_type, exc_val, exc_tb)

    caplog.set_level(logging.INFO)
    with callback_server([200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        with (
            patch.object(BaseWorkspace, "__exit__", _exit),
            pytest.raises(RuntimeError, match="body failed"),
        ):
            with workspace:
                raise RuntimeError("body failed")

    assert server.bodies == []
    assert exits["n"] == 1
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert warnings == ["Completion callback failed (FAILED): RuntimeError"]
    assert "example.test" not in caplog.text
    assert "user:token" not in caplog.text
