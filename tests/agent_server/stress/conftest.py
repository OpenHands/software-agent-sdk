"""Shared fixtures for stress / scale tests.

Tests run **in-process** against the agent-server FastAPI app:
- A real ConversationService is constructed pointed at tmp_path/persist.
- A minimal FastAPI app is built with the routers needed for these suites.
- The `get_conversation_service` dependency is overridden to return our service.
- `httpx.AsyncClient(transport=ASGITransport(app))` shares the test event loop.

We bypass HTTP for the *creation* of conversations because TestLLM has private
attrs (`_scripted_responses`, `_call_count`) that don't survive Pydantic JSON
round-trips. Tests call `service.start_conversation(request)` directly with a
real Python object, then use the API for everything else.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openhands.agent_server import bash_router as bash_router_module
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.server_details_router import (
    mark_initialization_complete,
    server_details_router,
)
from tests.agent_server.stress.probe import ResourceProbe


@pytest_asyncio.fixture
async def conversation_service(tmp_path: Path) -> AsyncIterator[ConversationService]:
    """Real ConversationService with persistence under tmp_path/persist.

    Uses the service's own __aenter__/__aexit__ to set up and tear down the
    event_services dict and webhook subscribers. No global state leaks across
    tests because the path is unique per test.
    """
    persist_dir = tmp_path / "persist"
    persist_dir.mkdir(parents=True, exist_ok=True)
    service = ConversationService(conversations_dir=persist_dir)
    async with service:
        yield service


@pytest_asyncio.fixture
async def bash_service(tmp_path: Path) -> AsyncIterator[BashEventService]:
    """Per-test BashEventService scoped to tmp_path/bash_events."""
    bash_dir = tmp_path / "bash_events"
    bash_dir.mkdir(parents=True, exist_ok=True)
    service = BashEventService(bash_events_dir=bash_dir)
    async with service:
        yield service


@pytest.fixture
def app(
    conversation_service: ConversationService, bash_service: BashEventService
) -> FastAPI:
    """FastAPI app wired to the test ConversationService and bash service.

    Includes the routers the stress suites use today: conversation + event +
    server_details (for /health) + bash. Sockets are skipped here; suites
    that need websocket coverage assert against pub_sub internals (white-box)
    rather than performing real WS handshakes through ASGITransport.

    ``app.state.config`` is set so any code that reads it (e.g. middleware)
    finds something. ``mark_initialization_complete`` is called so /ready
    returns 200 in the responsiveness canary.
    """
    fastapi_app = FastAPI()
    fastapi_app.state.config = Config()
    fastapi_app.state.bash_event_service = bash_service
    fastapi_app.include_router(server_details_router)
    fastapi_app.include_router(conversation_router, prefix="/api")
    fastapi_app.include_router(event_router, prefix="/api")
    fastapi_app.include_router(bash_router_module.bash_router, prefix="/api")
    fastapi_app.dependency_overrides[get_conversation_service] = (
        lambda: conversation_service
    )
    mark_initialization_complete()
    return fastapi_app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://stress.test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def probe() -> AsyncIterator[ResourceProbe]:
    p = ResourceProbe()
    async with p:
        yield p
