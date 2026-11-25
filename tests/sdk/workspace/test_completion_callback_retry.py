"""Retry behavior for BaseWorkspace._send_completion_callback.

The server is a real local HTTP server. These tests do not call OpenHands Cloud.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import patch

import pytest

from openhands.sdk.workspace.base import BaseWorkspace
from openhands.sdk.workspace.completion_callback import (
    COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS,
)
from openhands.sdk.workspace.local import LocalWorkspace
from tests.callback_http import callback_server


_SECRET = "callback-secret-token"
_CALLBACK = "openhands.sdk.workspace.completion_callback"


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


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


def test_retries_502_then_200_with_the_same_payload(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server([502, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 2
    assert server.bodies[0] == server.bodies[1]
    assert server.bodies[0]["status"] == "COMPLETED"
    assert server.bodies[0]["run_id"] == "run-42"
    assert server.bodies[0]["conversation_id"] == "conv-123"
    assert server.bodies[0]["cost"] == 0.42
    assert server.authorization == [f"Bearer {_SECRET}", f"Bearer {_SECRET}"]
    assert _warnings(caplog) == []
    assert _SECRET not in caplog.text
    assert any(
        "Completion callback sent (COMPLETED): 200" in record.getMessage()
        for record in caplog.records
    )


def test_retries_failed_payload_after_502(tmp_path, monkeypatch):
    with callback_server([502, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        exc = RuntimeError("script crashed")
        workspace._send_completion_callback(RuntimeError, exc)

    assert server.bodies[0] == server.bodies[1]
    assert server.bodies[0]["status"] == "FAILED"
    assert server.bodies[0]["error"]["code"] == "RuntimeError"
    assert server.bodies[0]["error"]["detail"] == "script crashed"


def test_retries_429_then_5xx_then_stops_on_2xx(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server([429, 503, 201]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 3
    assert server.bodies[0] == server.bodies[1] == server.bodies[2]
    assert _warnings(caplog) == []
    assert any(
        "Completion callback sent (COMPLETED): 201" in record.getMessage()
        for record in caplog.records
    )


def test_first_2xx_is_a_single_post(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server([204]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 1
    assert any(
        "Completion callback sent (COMPLETED): 204" in record.getMessage()
        for record in caplog.records
    )


def test_409_stops_without_a_failure_warning(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server([409]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 1
    assert _warnings(caplog) == []
    assert _SECRET not in caplog.text
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "already terminal" in message and "409" in message for message in messages
    )
    assert all("sent" not in message for message in messages)


def test_409_after_a_retry_stops_without_a_failure_warning(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    with callback_server([502, 409]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 2
    assert server.bodies[0] == server.bodies[1]
    assert _warnings(caplog) == []


@pytest.mark.parametrize("status_code", [401, 403, 404, 422])
def test_other_4xx_is_not_retried(tmp_path, monkeypatch, caplog, status_code: int):
    caplog.set_level(logging.INFO)
    with callback_server([status_code, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 1
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert str(status_code) in warnings[0]
    assert "sent" not in warnings[0]
    assert _SECRET not in caplog.text


def test_transport_error_then_200(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server(["drop", 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 2
    assert server.bodies[0] == server.bodies[1]
    assert _warnings(caplog) == []


def test_read_timeout_then_200(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(f"{_CALLBACK}.COMPLETION_CALLBACK_TIMEOUT_SECONDS", 0.2)
    caplog.set_level(logging.INFO)
    with callback_server(["stall", 200], stall_seconds=1.0) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 2
    assert server.bodies[0] == server.bodies[1]
    assert _warnings(caplog) == []


def test_persistent_transport_error_stops_without_raising(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(f"{_CALLBACK}.COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS", 0.01)
    caplog.set_level(logging.INFO)
    with callback_server(["drop", "drop", "drop", "drop", 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 4
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "sent" not in warnings[0]
    assert _SECRET not in caplog.text


def test_persistent_5xx_stops_at_the_attempt_cap(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(f"{_CALLBACK}.COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS", 0.01)
    caplog.set_level(logging.INFO)
    with callback_server([502, 502, 502, 502, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 4
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "502" in warnings[0]
    assert "sent" not in warnings[0]


def test_retry_after_beyond_the_budget_stops(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    with callback_server([(429, {"Retry-After": "120"}), 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        started = time.monotonic()
        workspace._send_completion_callback(None, None)
        elapsed = time.monotonic() - started

    assert len(server.bodies) == 1
    assert elapsed < 2
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "429" in warnings[0]
    assert "sent" not in warnings[0]


def test_retry_after_zero_retries_without_the_backoff(tmp_path, monkeypatch):
    with callback_server([(503, {"Retry-After": "0"}), 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 2
    assert server.times[1] - server.times[0] < COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS


def test_past_retry_after_date_retries_immediately(tmp_path, monkeypatch):
    past = "Wed, 21 Oct 2015 07:28:00 GMT"
    with callback_server([(429, {"Retry-After": past}), 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 2
    assert server.times[1] - server.times[0] < COMPLETION_CALLBACK_BACKOFF_BASE_SECONDS


def test_future_retry_after_date_stops_inside_the_budget(tmp_path, monkeypatch):
    future = "Wed, 21 Oct 2099 07:28:00 GMT"
    with callback_server([(429, {"Retry-After": future}), 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        started = time.monotonic()
        workspace._send_completion_callback(None, None)
        elapsed = time.monotonic() - started

    assert len(server.bodies) == 1
    assert elapsed < 2


def test_budget_stops_before_the_attempt_cap(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(f"{_CALLBACK}.COMPLETION_CALLBACK_BUDGET_SECONDS", 0.05)
    caplog.set_level(logging.WARNING)
    with callback_server([502, 502, 502, 502]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        workspace._send_completion_callback(None, None)

    assert len(server.bodies) == 1
    assert _warnings(caplog)


def test_no_request_without_callback_url(tmp_path, monkeypatch):
    with callback_server([200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, None)
        workspace._send_completion_callback(None, None)

    assert server.bodies == []


def test_exit_does_not_raise_when_the_callback_retries(tmp_path, monkeypatch):
    with callback_server([502, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        with workspace:
            pass

    assert len(server.bodies) == 2


def test_callback_does_not_mask_a_later_exit_error(tmp_path, monkeypatch):
    with callback_server([422]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        with (
            patch.object(
                BaseWorkspace,
                "__exit__",
                side_effect=RuntimeError("cleanup failed"),
            ),
            pytest.raises(RuntimeError, match="cleanup failed"),
        ):
            workspace.__exit__(None, None, None)

    assert len(server.bodies) == 1
