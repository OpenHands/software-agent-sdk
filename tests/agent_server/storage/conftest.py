from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk import LLM, Agent
from openhands.sdk.io.storage_safety import StorageSafetyConfig
from openhands.sdk.workspace import LocalWorkspace


@pytest_asyncio.fixture
async def storage_service(tmp_path) -> AsyncIterator[EventService]:
    service = EventService(
        stored=StoredConversation(
            id=uuid4(),
            workspace=LocalWorkspace(working_dir=str(tmp_path / "workspace")),
            autotitle=False,
        ),
        agent=Agent(llm=LLM(model="test-model"), tools=[]),
        conversations_dir=tmp_path / "conversations",
        storage_safety=StorageSafetyConfig(),
        lease_ttl_seconds=0,
    )
    await service.start()
    try:
        yield service
    finally:
        await service.close()
