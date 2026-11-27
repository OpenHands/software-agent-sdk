"""Retry behavior for the base workspace completion callback.

Exits go through ``with LocalWorkspace`` against a local HTTP server.
Retry-After parsing is checked once here, not again through the cloud sender.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from openhands.sdk.workspace import completion_callback
from openhands.sdk.workspace.base import BaseWorkspace
from openhands.sdk.workspace.completion_callback import post_completion_callback
from openhands.sdk.workspace.local import LocalWorkspace
from tests.callback_http import callback_server
from tests.callback_retry_cases import NETWORK, assert_network


_SECRET = "callback-secret-token"


class _Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wall_time(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.slept.append(delay)
        self.now += delay


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


@pytest.mark.parametrize("actions,attempts,warning", NETWORK)
def test_network(tmp_path, monkeypatch, caplog, actions, attempts, warning):
    caplog.set_level(logging.INFO)
    with callback_server(list(actions)) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        caplog.clear()
        with workspace:
            pass
    assert_network(server, caplog, actions, attempts, warning, _SECRET)


def test_completed_payload_on_exit(tmp_path, monkeypatch, caplog):
    """502 then 200 through a real exit. The before/after script uses this shape."""
    caplog.set_level(logging.INFO)
    with callback_server([502, 200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
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


def test_failed_exit_keeps_the_error_and_continues(tmp_path, monkeypatch):
    exits = {"n": 0}
    original = BaseWorkspace.__exit__

    def _exit(self, exc_type, exc_val, exc_tb):
        exits["n"] += 1
        return original(self, exc_type, exc_val, exc_tb)

    with callback_server([200]) as server:
        workspace = _workspace(tmp_path, monkeypatch, server.url)
        with (
            patch.object(BaseWorkspace, "__exit__", _exit),
            pytest.raises(RuntimeError, match="script crashed"),
        ):
            with workspace:
                raise RuntimeError("script crashed")

    assert exits["n"] == 1
    assert len(server.bodies) == 1
    body = server.bodies[0]
    assert body["status"] == "FAILED"
    assert body["run_id"] == "run-42"
    assert body["conversation_id"] == "conv-123"
    assert body["cost"] == 0.42
    assert body["error"]["source"] == "environment"
    assert body["error"]["code"] == "RuntimeError"
    assert body["error"]["detail"] == "script crashed"


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
        "openhands.sdk.workspace.base.observability_headers_from_env", boom
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


@pytest.mark.parametrize(
    ("header", "attempts", "pause", "warning"),
    [
        ("1", 2, "exact", None),
        ("0", 2, "none", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 2, "none", None),
        ("120", 1, "none", "429"),
        ("Wed, 21 Oct 2099 07:28:00 GMT", 1, "none", "429"),
        ("NaN", 2, "backoff", None),
        ("Infinity", 2, "backoff", None),
        ("1e2", 2, "backoff", None),
        ("1.5", 2, "backoff", None),
        ("+5", 2, "backoff", None),
        ("Wed, 21 Oct 10000 07:28:00 GMT", 2, "backoff", None),
        ("9" * 400, 1, "none", "429"),
    ],
)
def test_retry_after(monkeypatch, caplog, header, attempts, pause, warning):
    clock = _Clock()
    monkeypatch.setattr(completion_callback, "monotonic", clock.monotonic)
    monkeypatch.setattr(completion_callback, "sleep", clock.sleep)
    monkeypatch.setattr(completion_callback, "wall_time", clock.wall_time)
    caplog.set_level(logging.INFO)
    with callback_server([(429, {"Retry-After": header}), 200]) as server:
        post_completion_callback(
            url=server.url,
            payload={"status": "COMPLETED"},
            headers={"Authorization": f"Bearer {_SECRET}"},
            status="COMPLETED",
            logger=logging.getLogger("openhands.sdk.workspace.base"),
        )

    assert len(server.bodies) == attempts
    failures = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Completion callback failed")
    ]
    if warning is None:
        assert failures == []
    else:
        assert failures == [f"Completion callback failed (COMPLETED): {warning}"]
    if pause == "none":
        assert clock.slept == []
    elif pause == "exact":
        assert clock.slept == [1.0]
    else:
        assert len(clock.slept) == 1
        assert 0.25 <= clock.slept[0] <= 0.3
    assert _SECRET not in "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("openhands.")
    )
