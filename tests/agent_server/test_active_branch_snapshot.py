from uuid import uuid4

import pytest

from openhands.agent_server.event_router import get_active_branch
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk.errors import Conflict
from openhands.sdk.workspace import LocalWorkspace


async def test_persisted_only_branch_read_is_explicitly_unavailable(tmp_path):
    stored = StoredConversation(
        id=uuid4(), workspace=LocalWorkspace(working_dir=str(tmp_path))
    )
    service = EventService.for_persisted_events(stored, tmp_path)
    with pytest.raises(Conflict, match="attached conversation runtime"):
        await get_active_branch(limit=None, event_service=service)
    assert service._conversation is None
