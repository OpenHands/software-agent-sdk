import asyncio
from contextlib import contextmanager
from uuid import UUID

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus as Status
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
async def family(tmp_path, monkeypatch):
    entered: dict[UUID, asyncio.Event] = {}
    release: dict[UUID, asyncio.Event] = {}
    waiting: dict[UUID, asyncio.Event] = {}
    outcomes: dict[UUID, Status] = {}
    provisional: dict[UUID, asyncio.Event] = {}
    original_scope = EventService.child_wait_scope

    @contextmanager
    def observed_scope(self):
        with original_scope(self) as signal:
            waiting[self.stored.id].set()
            yield signal

    async def step(self, conversation, **kwargs):
        async with conversation._released_state_lock_during_io():
            entered[conversation.id].set()
            await release[conversation.id].wait()
        conversation.state.execution_status = outcomes.get(
            conversation.id, Status.FINISHED
        )
        if conversation.id in provisional:
            entered[conversation.id].set()
            async with conversation._released_state_lock_during_io():
                await provisional[conversation.id].wait()

    monkeypatch.setattr(Agent, "astep", step)
    monkeypatch.setattr(EventService, "child_wait_scope", observed_scope)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with ConversationService(conversations_dir=tmp_path / "conversations") as svc:

        async def create(parent_id=None):
            info, _ = await svc.start_conversation(
                StartConversationRequest(
                    agent=Agent(llm=LLM(model="gpt-4o", usage_id="wait-test")),
                    workspace=LocalWorkspace(working_dir=str(workspace)),
                    parent_conversation_id=parent_id,
                )
            )
            entered[info.id] = asyncio.Event()
            release[info.id] = asyncio.Event()
            waiting[info.id] = asyncio.Event()
            event_service = await svc.get_event_service(info.id)
            assert event_service is not None
            return event_service

        parent = await create()
        children = [await create(parent.stored.id) for _ in range(3)]
        yield svc, parent, children, entered, release, waiting, outcomes, provisional
