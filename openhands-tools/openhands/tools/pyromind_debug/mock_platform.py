"""Stand-in for the real Pyromind debug/试跑 platform API.

The real integration submits the current ``workflow.py`` to an external,
asynchronous debug API together with a callback URL. Some time later
(observed to be ~30s-2min for this workload) the platform calls back to
``POST /api/pyromind/debug/callback`` with the run's outcome, and that
endpoint resolves the same :class:`DebugResultBroker` used here. Swapping
this mock for the real client only changes how ``submit`` is implemented;
the broker/callback contract does not change.

This mock deliberately goes over a real HTTP hop -- after the configured
delay, its background timer thread does an actual ``POST`` to
``{callback_base_url}/api/pyromind/debug/callback`` -- rather than touching
:class:`DebugResultBroker` in-process. That endpoint is what an external
platform would call in production, so exercising it for real here is what
gives us confidence the webhook wiring (route, request/response schema,
auth-exemption) actually works end to end, not just the broker in isolation.
"""

from __future__ import annotations

import os
import threading
from typing import Protocol

import httpx

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

DEFAULT_DELAY_SECONDS = float(os.environ.get("PYROMIND_DEBUG_MOCK_DELAY_SECONDS", "30"))
# How many of the first attempts the mock reports as failed before passing.
DEFAULT_FAIL_ATTEMPTS = int(os.environ.get("PYROMIND_DEBUG_MOCK_FAIL_ATTEMPTS", "2"))
# Base URL of this very agent-server process, used to call its own debug
# webhook -- mirrors how an external platform would be configured with a
# callback base URL. Must match the host/port the server is actually bound
# to (see openhands.agent_server.__main__'s --host/--port).
DEFAULT_CALLBACK_BASE_URL = os.environ.get(
    "PYROMIND_DEBUG_CALLBACK_BASE_URL", "http://127.0.0.1:8000"
)
_CALLBACK_PATH = "/api/pyromind/debug/callback"
_CALLBACK_HTTP_TIMEOUT_SECONDS = 10.0

_MOCK_ERROR_TEMPLATE = (
    "Traceback (most recent call last):\n"
    '  File "workflow.py", line {line}, in main\n'
    "    {snippet}\n"
    "{error_type}: {message}"
)

_MOCK_ERRORS = [
    {
        "line": 12,
        "snippet": "node = TrainNode(dataset=dataset)",
        "error_type": "KeyError",
        "message": "'dataset_path' is required but was not provided",
    },
    {
        "line": 27,
        "snippet": "output = node.run(batch_size=batch_size)",
        "error_type": "ValueError",
        "message": "batch_size must be a positive integer, got -1",
    },
]


class DebugPlatformClient(Protocol):
    """Interface a real Pyromind debug-platform client must implement."""

    def submit(self, task_id: str, workflow_source: str, attempt: int) -> None:
        """Submit a debug run. Must eventually cause a broker resolution for
        ``task_id`` (either via this mock's local timer, or via a real
        platform calling the ``/api/pyromind/debug/callback`` webhook)."""
        ...


class MockDebugPlatform:
    """Simulates the platform: fails the first N attempts, then passes.

    Unlike a real platform, this runs in the same process as the server it
    calls back into -- but the call itself is a genuine HTTP request over
    loopback, not a shortcut into :class:`DebugResultBroker`.
    """

    def __init__(
        self,
        delay_seconds: float | None = None,
        fail_attempts: int | None = None,
        callback_base_url: str | None = None,
    ) -> None:
        self._delay_seconds = (
            DEFAULT_DELAY_SECONDS if delay_seconds is None else delay_seconds
        )
        self._fail_attempts = (
            DEFAULT_FAIL_ATTEMPTS if fail_attempts is None else fail_attempts
        )
        self._callback_base_url = (
            DEFAULT_CALLBACK_BASE_URL
            if callback_base_url is None
            else callback_base_url
        )

    def submit(self, task_id: str, workflow_source: str, attempt: int) -> None:
        del workflow_source  # unused by the mock; a real client would upload it
        timer = threading.Timer(
            self._delay_seconds, self._call_back, args=(task_id, attempt)
        )
        timer.daemon = True
        timer.start()

    def _call_back(self, task_id: str, attempt: int) -> None:
        if attempt <= self._fail_attempts:
            error = _MOCK_ERRORS[(attempt - 1) % len(_MOCK_ERRORS)]
            payload = {
                "task_id": task_id,
                "status": "failed",
                "error_log": _MOCK_ERROR_TEMPLATE.format(**error),
            }
        else:
            payload = {"task_id": task_id, "status": "passed", "error_log": None}

        url = f"{self._callback_base_url}{_CALLBACK_PATH}"
        try:
            response = httpx.post(
                url, json=payload, timeout=_CALLBACK_HTTP_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            logger.exception(
                "Mock debug platform failed to POST callback for task %s to %s",
                task_id,
                url,
            )
            return

        if response.status_code == 404:
            logger.warning(
                "Mock debug platform's callback for task %s was rejected as "
                "unknown/already-resolved (the wait may have already timed "
                "out): %s",
                task_id,
                response.text,
            )
        elif response.is_error:
            logger.error(
                "Mock debug platform's callback for task %s failed with %s: %s",
                task_id,
                response.status_code,
                response.text,
            )
