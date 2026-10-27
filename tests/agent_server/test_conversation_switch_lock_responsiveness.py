"""A conversation switching its LLM must not stall the server on a store lock.

``switch_profile`` loads the named profile from the conversation's
``LLMProfileStore`` and ``switch_llm`` resolves a referenced provider
connection through the server's profile store. Both stores take a
``filelock.FileLock`` whose ``acquire`` polls with a blocking sleep (up to
30 s), and both routes are ``async def`` handlers, so the wait must run off
the event loop — like the profile-store routes (issue #5632) — or ``/alive``
stops answering while another process holds the lock.
"""

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from filelock import FileLock
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.persistence import reset_stores
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import LocalConversation


# Upper bound for the held lock: if the handler blocks the event loop, nothing
# can release the lock from the test, so a timer frees it after this long.
_SAFETY_RELEASE_SECONDS = 5.0
_ALIVE_BUDGET_SECONDS = 2.0


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    reset_stores()
    yield create_app(
        Config(static_files_path=None, session_api_keys=[], secret_key=None)
    )
    reset_stores()


@pytest.fixture
def conversation(tmp_path: Path) -> LocalConversation:
    # An explicit profile_store_dir keeps the conversation's store lock inside
    # the test's persistence dir regardless of import-time defaults.
    return LocalConversation(
        agent=Agent(
            llm=LLM(
                model="gpt-4o",
                api_key=SecretStr("test-key"),
                usage_id="test-llm",
            ),
            tools=[],
        ),
        workspace=str(tmp_path / "ws"),
        persistence_dir=tmp_path / "conversations",
        profile_store_dir=tmp_path / "profiles",
    )


@pytest.fixture
def stub_service(app, conversation: LocalConversation) -> ConversationService:
    service = AsyncMock(spec=ConversationService)
    event_service = AsyncMock(spec=EventService)
    event_service.get_conversation.return_value = conversation
    service.get_event_service.return_value = event_service
    app.dependency_overrides[get_conversation_service] = lambda: service
    return service


async def _assert_alive_stays_responsive(
    client: httpx.AsyncClient, waiting: "asyncio.Task[httpx.Response]"
) -> float:
    started = time.monotonic()
    # Let the store request start and block on the lock.
    await asyncio.sleep(0.3)
    alive = await client.get("/alive", timeout=60)
    elapsed = time.monotonic() - started

    assert alive.status_code == 200
    assert elapsed < _ALIVE_BUDGET_SECONDS, (
        f"/alive took {elapsed:.1f}s while a switch request waited "
        "for the store lock: the wait is blocking the event loop"
    )
    # The switch request was really still waiting on the lock.
    assert not waiting.done()
    return elapsed


async def test_alive_answers_while_switch_profile_waits_for_lock(
    tmp_path: Path,
    app,
    stub_service,
) -> None:
    """``switch_profile`` waits for the conversation's profile-store lock off
    the event loop."""
    lock_path = tmp_path / "profiles" / ".profiles.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = FileLock(lock_path, thread_local=False)
    held.acquire()
    safety = threading.Timer(_SAFETY_RELEASE_SECONDS, held.release)
    safety.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            waiting = asyncio.create_task(
                client.post(
                    f"/api/conversations/{uuid4()}/switch_profile",
                    json={"profile_name": "qa-busy"},
                    timeout=60,
                )
            )
            await _assert_alive_stays_responsive(client, waiting)

            safety.cancel()
            held.release()
            response = await asyncio.wait_for(waiting, timeout=30)
            # The profile never existed; the handler maps that to 404.
            assert response.status_code == 404, response.text
    finally:
        safety.cancel()
        if held.is_locked:
            held.release(force=True)


async def test_alive_answers_while_switch_llm_waits_for_lock(
    tmp_path: Path,
    app,
    stub_service,
) -> None:
    """``switch_llm`` resolves a referenced provider connection off the event
    loop, so a held provider-connections lock does not stall ``/alive``."""
    lock_path = tmp_path / "provider-connections" / ".provider-connections.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = FileLock(lock_path, thread_local=False)
    held.acquire()
    safety = threading.Timer(_SAFETY_RELEASE_SECONDS, held.release)
    safety.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            waiting = asyncio.create_task(
                client.post(
                    f"/api/conversations/{uuid4()}/switch_llm",
                    # No inline api_key, so the missing referenced connection
                    # must surface as ProviderConnectionNotFound (422).
                    json={
                        "llm": {
                            "model": "gpt-4o",
                            "provider_connection_id": "qa-missing",
                        }
                    },
                    timeout=60,
                )
            )
            await _assert_alive_stays_responsive(client, waiting)

            safety.cancel()
            held.release()
            response = await asyncio.wait_for(waiting, timeout=30)
            assert response.status_code == 422, response.text
    finally:
        safety.cancel()
        if held.is_locked:
            held.release(force=True)
