import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def make_repo(repo: Path) -> Path:
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("node_modules/\n")
    (repo / "README.md").write_text("hi")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    return repo


def service(tmp_path: Path) -> ConversationService:
    svc = ConversationService(
        conversations_dir=tmp_path / "conversations",
        conversation_worktree_root=tmp_path / "conversation-worktrees",
    )
    svc._event_services = {}
    return svc


@pytest.mark.asyncio
async def test_delete_removes_worktree_and_keeps_branch(tmp_path):
    repo = make_repo(tmp_path / "repo")
    svc = service(tmp_path)
    conversation_id = uuid4()

    def factory(**kwargs):
        stored = kwargs["stored"]
        event_service = AsyncMock(spec=EventService)
        event_service.stored = stored
        event_service.conversation_dir = tmp_path / "conversations" / stored.id.hex
        event_service.get_state.return_value = ConversationState(
            id=stored.id,
            agent=cast(AgentBase, kwargs.get("agent")),
            workspace=stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored.confirmation_policy,
        )
        return event_service

    with patch(
        "openhands.agent_server.conversation_service.EventService",
        side_effect=factory,
    ):
        await svc.start_conversation(
            StartConversationRequest(
                conversation_id=conversation_id,
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="t"), tools=[]),
                workspace=LocalWorkspace(working_dir=repo),
                confirmation_policy=NeverConfirm(),
                worktree=True,
            )
        )
        worktree = svc.conversation_worktree_root / str(conversation_id) / repo.name
        (worktree / "agent.txt").write_text("work")
        git(worktree, "add", "agent.txt")
        git(worktree, "commit", "-qm", "agent work")
        assert await svc.delete_conversation(conversation_id)

    branch = f"openhands/{conversation_id}"
    assert not (svc.conversation_worktree_root / str(conversation_id)).exists()
    assert str(worktree) not in git(repo, "worktree", "list")
    # The agent's commit is still reachable from its branch.
    assert git(repo, "log", "-1", "--format=%s", branch) == "agent work"
