import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server import conversation_service as service_module
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import (
    ConversationService,
    _create_conversation_worktree,
    _get_worktree_start_point,
    _plan_conversation_worktree,
    _remove_conversation_worktree,
)
from openhands.agent_server.runtime_settings import (
    RuntimeSettings,
    load_runtime_settings,
)
from openhands.sdk.git.utils import run_git_command
from openhands.sdk.workspace import LocalWorkspace


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# test repo\n")
    run_git_command(["git", "init", "-b", "main"], repo_dir)
    run_git_command(["git", "add", "README.md"], repo_dir)
    run_git_command(
        [
            "git",
            "-c",
            "user.name=OpenHands Test",
            "-c",
            "user.email=openhands@example.com",
            "commit",
            "-m",
            "init",
        ],
        repo_dir,
    )


def _worktree(
    tmp_path: Path,
    repo: Path,
    base: Literal["default_branch", "head"] = "default_branch",
):
    conversation_id = uuid4()
    root = tmp_path / "worktrees"
    plan = _plan_conversation_worktree(
        LocalWorkspace(working_dir=repo), conversation_id, root
    )
    assert plan is not None
    _create_conversation_worktree(plan, base)
    return root, conversation_id, plan


def test_load_reads_the_misc_runtime_namespace(monkeypatch):
    store = SimpleNamespace(
        load=lambda: SimpleNamespace(
            misc_settings={"runtime": {"docker_memory": "8g", "unknown": 1}}
        )
    )
    monkeypatch.setattr(
        "openhands.agent_server.persistence.get_settings_store", lambda: store
    )
    assert load_runtime_settings().docker_memory == "8g"


def test_unset_values_fall_back_to_config():
    config = Config(
        secret_key=SecretStr("k"),
        conversation_storage_retention_days=7,
        conversation_idle_ttl_seconds=600,
    )
    defaults = RuntimeSettings()
    assert defaults.docker_storage_policy(config) == (None, 7)
    assert defaults.docker_idle_ttl_seconds(config) == 600
    overridden = RuntimeSettings(docker_retention_days=2, docker_idle_stop_minutes=5)
    assert overridden.docker_storage_policy(config) == (None, 2)
    assert overridden.docker_idle_ttl_seconds(config) == 300


def test_head_base_skips_the_default_branch_policy(tmp_path):
    _init_git_repo(tmp_path / "repo")
    assert _get_worktree_start_point(tmp_path / "repo", "head") == "HEAD"


@pytest.mark.parametrize("delete_branch", [False, True])
def test_remove_worktree_prunes_git_and_optionally_the_branch(tmp_path, delete_branch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    root, conversation_id, plan = _worktree(tmp_path, repo)

    assert _remove_conversation_worktree(
        root, conversation_id, delete_branch=delete_branch
    )

    assert not (root / str(conversation_id)).exists()
    assert str(plan.worktree_root) not in run_git_command(
        ["git", "worktree", "list"], repo
    )
    has_branch = bool(run_git_command(["git", "branch", "--list", plan.branch], repo))
    assert has_branch is not delete_branch


@pytest.mark.asyncio
async def test_retention_removes_only_worktrees_past_the_period(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    root, old_id, _ = _worktree(tmp_path, repo)
    _, fresh_id, _ = _worktree(tmp_path, repo)
    two_days_ago = time.time() - 2 * 86400
    os.utime(root / str(old_id), (two_days_ago, two_days_ago))
    monkeypatch.setattr(
        service_module,
        "load_runtime_settings",
        lambda: RuntimeSettings(worktree_retention_days=1),
    )
    # Conversations already deleted: activity falls back to the directory mtime.
    fake = SimpleNamespace(
        conversation_worktree_root=root, get_conversation=AsyncMock(return_value=None)
    )

    removed = await ConversationService.enforce_worktree_retention(fake)  # type: ignore[arg-type]

    assert removed == 1
    assert not (root / str(old_id)).exists()
    assert (root / str(fresh_id)).exists()
