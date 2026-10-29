"""A request waiting on a profile/connection store lock must not stall the server.

The four stores below take a ``filelock.FileLock`` whose ``acquire`` polls with
a blocking sleep (up to 30 s). Their route handlers must run that wait off the
event loop, so ``/alive`` keeps answering while another process holds the lock.
Regression test for issue #5632.
"""

import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest
from filelock import FileLock

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores


# Upper bound for the held lock: if the handler blocks the event loop, nothing
# can release the lock from the test, so a timer frees it after this long.
_SAFETY_RELEASE_SECONDS = 5.0
_ALIVE_BUDGET_SECONDS = 2.0


@pytest.mark.parametrize(
    ("lock_file", "method", "path", "json_body", "expected_status"),
    [
        pytest.param(
            "profiles/.profiles.lock",
            "POST",
            "/api/profiles/qa-busy",
            {"llm": {"model": "openai/qa-busy"}},
            201,
            id="llm-profiles",
        ),
        pytest.param(
            "agent-profiles/.agent-profiles.lock",
            "GET",
            "/api/agent-profiles/qa-busy",
            None,
            404,
            id="agent-profiles",
        ),
        pytest.param(
            "agent-profiles/.agent-profiles.lock",
            "POST",
            "/api/agent-profiles/qa-busy/materialize",
            None,
            404,
            id="agent-profiles-materialize",
        ),
        pytest.param(
            "meta-profiles/.meta-profiles.lock",
            "POST",
            "/api/meta-profiles/qa-busy",
            {
                "classifier_model": "qa-classifier",
                "classes": [{"description": "UI", "model": "qa-model"}],
            },
            201,
            id="meta-profiles",
        ),
        pytest.param(
            "provider-connections/.provider-connections.lock",
            "GET",
            "/api/llm/provider-connections",
            None,
            200,
            id="provider-connections",
        ),
    ],
)
async def test_alive_answers_while_store_request_waits_for_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lock_file: str,
    method: str,
    path: str,
    json_body: dict | None,
    expected_status: int,
) -> None:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    reset_stores()
    app = create_app(
        Config(static_files_path=None, session_api_keys=[], secret_key=None)
    )

    lock_path = tmp_path / lock_file
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # A separate FileLock instance stands in for another process holding the
    # store lock. ``thread_local=False`` lets the safety timer release it.
    held = FileLock(lock_path, thread_local=False)
    held.acquire()
    safety = threading.Timer(_SAFETY_RELEASE_SECONDS, held.release)
    safety.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            waiting = asyncio.create_task(
                client.request(method, path, json=json_body, timeout=60)
            )
            started = time.monotonic()
            # Let the store request start and block on the lock.
            await asyncio.sleep(0.3)
            alive = await client.get("/alive", timeout=60)
            elapsed = time.monotonic() - started

            assert alive.status_code == 200
            assert elapsed < _ALIVE_BUDGET_SECONDS, (
                f"/alive took {elapsed:.1f}s while a store request waited "
                "for the lock: the wait is blocking the event loop"
            )
            # The store request was really still waiting on the lock.
            assert not waiting.done()

            safety.cancel()
            held.release()
            response = await asyncio.wait_for(waiting, timeout=30)
            assert response.status_code == expected_status, response.text
    finally:
        safety.cancel()
        if held.is_locked:
            held.release(force=True)
        reset_stores()
