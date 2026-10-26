import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    StartChildConversationRequest,
    StartConversationRequest,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.workspace import LocalWorkspace


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
def test_creation_rejects_invalid_budget(budget, tmp_path):
    with pytest.raises(ValidationError, match="max_budget_per_run"):
        StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(tmp_path)),
            max_budget_per_run=budget,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget,current_budget", [(None, None), (1.25, 2.5), (1.25, None)]
)
async def test_budget_survives_reuse_eviction_fork_and_restart(
    tmp_path, budget, current_budget
):
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        max_budget_per_run=budget,
    )
    async with ConversationService(conversations_dir=conversations_dir) as service:
        info, _ = await service.start_conversation(request)
        assert info.max_budget_per_run == budget
        original = await service.get_event_service(info.id)
        assert original is not None
        assert original.get_conversation().max_budget_per_run == budget
        await asyncio.to_thread(
            setattr, original.get_conversation(), "max_budget_per_run", current_budget
        )
        reused, is_new = await service.start_conversation(
            request.model_copy(
                update={"conversation_id": info.id, "max_budget_per_run": 99}
            )
        )
        assert not is_new
        assert reused.max_budget_per_run == current_budget

        fork = await service.fork_conversation(info.id)
        assert fork is not None
        assert fork.max_budget_per_run == current_budget
        fork_service = await service.get_event_service(fork.id)
        assert fork_service is not None
        assert fork_service.get_conversation().max_budget_per_run == current_budget

        await service._evict_idle_conversations(0)
        assert not original.is_open()
        cold = await service.get_conversation(info.id)
        assert cold is not None
        assert cold.max_budget_per_run == current_budget
        rehydrated = await service.get_event_service(info.id)
        assert rehydrated is not None
        assert rehydrated.get_conversation().max_budget_per_run == current_budget

    for conversation_id in (info.id, fork.id):
        meta_path = conversations_dir / conversation_id.hex / "meta.json"
        meta = json.loads(meta_path.read_text())
        assert "max_budget_per_run" not in meta
        state_path = meta_path.with_name("base_state.json")
        state = json.loads(state_path.read_text())
        assert state.get("max_budget_per_run") == current_budget

    async with ConversationService(conversations_dir=conversations_dir) as restarted:
        for conversation_id in (info.id, fork.id):
            reloaded = await restarted.get_conversation(conversation_id)
            assert reloaded is not None
            assert reloaded.max_budget_per_run == current_budget
            event_service = await restarted.get_event_service(conversation_id)
            assert event_service is not None
            assert event_service.get_conversation().max_budget_per_run == current_budget


@pytest.mark.asyncio
async def test_child_inherits_current_parent_budget(tmp_path):
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        max_budget_per_run=1.25,
    )
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent, _ = await service.start_conversation(request)
        parent_service = await service.get_event_service(parent.id)
        assert parent_service is not None
        await asyncio.to_thread(
            setattr, parent_service.get_conversation(), "max_budget_per_run", 2.5
        )
        with patch.object(EventService, "run", new_callable=AsyncMock):
            child = await service.start_child_conversation(
                parent.id,
                StartChildConversationRequest(task="Child task", isolation="shared"),
            )
        assert child is not None
        info = await service.get_conversation(child.conversation_id)
        assert info is not None
        assert info.max_budget_per_run == 2.5
        child_dir = tmp_path / "conversations" / child.conversation_id.hex
        assert (
            json.loads((child_dir / "base_state.json").read_text())[
                "max_budget_per_run"
            ]
            == 2.5
        )
        assert "max_budget_per_run" not in json.loads(
            (child_dir / "meta.json").read_text()
        )
