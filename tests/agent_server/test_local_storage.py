import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.local_storage import LocalWorktreeStorage
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.storage import Reclaimer, reclaimer as reclaimer_module
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


def worktree_storage(svc: ConversationService) -> LocalWorktreeStorage:
    return LocalWorktreeStorage(
        svc, svc.conversation_worktree_root, svc.conversations_dir
    )


def add_worktree(svc: ConversationService, repo: Path) -> tuple[UUID, Path]:
    conversation_id = uuid4()
    worktree = svc.conversation_worktree_root / str(conversation_id) / repo.name
    worktree.parent.mkdir(parents=True)
    git(
        repo,
        "worktree",
        "add",
        "-q",
        "-b",
        f"openhands/{conversation_id}",
        str(worktree),
    )
    (worktree / "node_modules" / "left-pad").mkdir(parents=True)
    (worktree / "notes.md").write_text("agent work")
    return conversation_id, worktree


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


@pytest.fixture
def usage(monkeypatch) -> list[float]:
    readings: list[float] = []

    def read(_path: Path) -> float:
        return readings.pop(0) if len(readings) > 1 else readings[0]

    monkeypatch.setattr(reclaimer_module, "disk_usage", read)
    return readings


@pytest.mark.asyncio
async def test_disk_budget_sheds_dependencies_of_unloaded_worktrees(tmp_path, usage):
    repo = make_repo(tmp_path / "repo")
    svc = service(tmp_path)
    conversation_id, worktree = add_worktree(svc, repo)
    reclaimer = Reclaimer(worktree_storage(svc), disk_budget=0.8)
    usage.extend([0.95, 0.5])

    await reclaimer.run_pass()

    assert not (worktree / "node_modules").exists()
    assert (worktree / "notes.md").read_text() == "agent work"
    assert (worktree / "README.md").is_file()
    assert git(repo, "branch", "--list", f"openhands/{conversation_id}")


@pytest.mark.asyncio
async def test_loaded_conversation_keeps_its_worktree(tmp_path, usage):
    repo = make_repo(tmp_path / "repo")
    svc = service(tmp_path)
    conversation_id, worktree = add_worktree(svc, repo)
    assert svc._event_services is not None
    svc._event_services[conversation_id] = cast(EventService, AsyncMock())
    reclaimer = Reclaimer(worktree_storage(svc), disk_budget=0.8)
    usage.append(0.95)

    await reclaimer.run_pass()

    assert (worktree / "node_modules" / "left-pad").is_dir()


@pytest.mark.asyncio
async def test_missing_worktree_root_is_fine(tmp_path, usage):
    reclaimer = Reclaimer(
        worktree_storage(service(tmp_path)),
        disk_budget=0.8,
    )
    usage.append(0.95)

    await reclaimer.start()
    await reclaimer.run_pass()
    await reclaimer.shutdown()


@pytest.mark.asyncio
async def test_local_registry_runs_storage_maintenance(tmp_path, monkeypatch):
    monkeypatch.setattr(reclaimer_module, "MAINTENANCE_INTERVAL", 3600)
    registry = ConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("k"),
            conversation_storage_disk_budget=0.8,
        )
    )
    registry.configure_service(service(tmp_path))

    await registry.start()
    assert registry.worktree_reclaimer is not None
    assert registry.worktree_reclaimer._maintenance is not None
    await registry.shutdown()
    assert registry.worktree_reclaimer._maintenance is None
