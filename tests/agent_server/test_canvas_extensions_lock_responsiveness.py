"""A Canvas request waiting on the installation metadata lock must not stall
the server.

``InstallationMetadata.open()`` takes a ``filelock.FileLock`` whose ``acquire``
polls with a blocking sleep (up to 30 s). The ``async def`` Canvas endpoints
must run the lock-taking calls off the event loop, as the install endpoint
already does, so ``/alive`` keeps answering while another process holds the
lock. Same check as ``test_profile_store_lock_responsiveness.py``, except that
these handlers stop the backend before taking the lock, so the test waits for
the lock attempt itself instead of a fixed delay.
"""

import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest
from filelock import FileLock

from openhands.agent_server.api import create_app
from openhands.agent_server.canvas_extensions.installed import (
    enable_canvas_extension,
    install_canvas_extension,
)
from openhands.agent_server.config import Config
from openhands.sdk.extensions.installation import InstallationMetadata

from .canvas_extensions.conftest import write_extension


# Upper bound for the held lock: if the handler blocks the event loop, nothing
# can release the lock from the test, so a timer frees it after this long.
_SAFETY_RELEASE_SECONDS = 5.0
_ALIVE_BUDGET_SECONDS = 2.0
_NAME = "demo-extension"


@pytest.mark.parametrize(
    ("method", "path", "json_body", "enabled_before"),
    [
        pytest.param(
            "PATCH",
            f"/api/canvas-extensions/installed/{_NAME}",
            {"enabled": False},
            True,
            id="disable",
        ),
        pytest.param(
            "PATCH",
            f"/api/canvas-extensions/installed/{_NAME}",
            {"enabled": True},
            False,
            id="enable",
        ),
        pytest.param(
            "DELETE",
            f"/api/canvas-extensions/installed/{_NAME}",
            None,
            False,
            id="uninstall",
        ),
        pytest.param(
            "DELETE",
            f"/api/canvas-extensions/installed/{_NAME}/backend/secret-grant",
            None,
            True,
            id="revoke-secret-grant",
        ),
    ],
)
async def test_alive_answers_while_canvas_request_waits_for_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    json_body: dict | None,
    enabled_before: bool,
) -> None:
    installed_dir = tmp_path / "canvas-installed"
    monkeypatch.setattr(
        "openhands.agent_server.canvas_extensions.installed."
        "get_installed_canvas_extensions_dir",
        lambda: installed_dir,
    )
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    install_canvas_extension(
        source=str(write_extension(tmp_path / "src" / _NAME, name=_NAME))
    )
    if enabled_before:
        assert enable_canvas_extension(_NAME)
    app = create_app(
        Config(static_files_path=None, session_api_keys=[], secret_key=None)
    )

    # Set from the thread that is about to wait on the lock. The time is taken
    # there too: if the handler blocks the event loop, the test's own clock
    # reading would only resume after the lock is released.
    lock_requested = threading.Event()
    lock_requested_at: list[float] = []
    acquire_lock = InstallationMetadata._acquire_lock

    def signalling_acquire_lock(installed_dir: Path):
        lock_requested_at.append(time.monotonic())
        lock_requested.set()
        return acquire_lock(installed_dir)

    monkeypatch.setattr(
        InstallationMetadata, "_acquire_lock", staticmethod(signalling_acquire_lock)
    )

    # A separate FileLock instance stands in for another process holding the
    # metadata lock. ``thread_local=False`` lets the safety timer release it.
    held = FileLock(installed_dir / ".metadata.lock", thread_local=False)
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
            assert await asyncio.to_thread(lock_requested.wait, 10)
            alive = await client.get("/alive", timeout=60)
            elapsed = time.monotonic() - lock_requested_at[0]

            assert alive.status_code == 200
            assert elapsed < _ALIVE_BUDGET_SECONDS, (
                f"/alive took {elapsed:.1f}s while a Canvas request waited "
                "for the metadata lock: the wait is blocking the event loop"
            )
            # The Canvas request was really still waiting on the lock.
            assert not waiting.done()

            safety.cancel()
            held.release()
            response = await asyncio.wait_for(waiting, timeout=30)
            assert response.status_code == 200, response.text
    finally:
        safety.cancel()
        if held.is_locked:
            held.release(force=True)
