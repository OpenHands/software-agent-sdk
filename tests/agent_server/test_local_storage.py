"""Deleting host-local conversations, against a real server and real git."""

import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import (
    config as config_module,
    conversation_service as service_module,
)
from openhands.agent_server.api import create_app


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


def worktrees(repo: Path) -> list[str]:
    return [
        line.removeprefix("worktree ")
        for line in git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """A local-mode server configured by environment, as the CLI does."""
    for name, value in {
        "OH_CONVERSATION_RUNTIME": "local",
        "OH_CONVERSATIONS_PATH": str(tmp_path / "conversations"),
        "OH_CONVERSATION_WORKTREE_ROOT": str(tmp_path / "worktrees"),
        "OH_PERSISTENCE_DIR": str(tmp_path / "persistence"),
        "OH_SECRET_KEY": "key",
    }.items():
        monkeypatch.setenv(name, value)
    # Both are process-wide and would keep another test's paths.
    monkeypatch.setattr(config_module, "_default_config", None)
    monkeypatch.setattr(service_module, "_conversation_service", None)
    with TestClient(create_app()) as client:
        yield client


def start(client: TestClient, repo: Path, worktree: bool = True) -> tuple[UUID, Path]:
    response = client.post(
        "/api/conversations",
        json={
            "agent": {"kind": "Agent", "llm": {"model": "test"}, "tools": []},
            "workspace": {"kind": "LocalWorkspace", "working_dir": str(repo)},
            "worktree": worktree,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return UUID(body["id"]), Path(body["workspace"]["working_dir"])


def delete(client: TestClient, conversation_id: UUID) -> None:
    response = client.delete(f"/api/conversations/{conversation_id}")
    assert response.status_code == 200, response.text


def test_delete_removes_the_worktree_and_keeps_the_branch(client, tmp_path):
    repo = make_repo(tmp_path / "repo")
    conversation_id, worktree = start(client, repo)
    assert worktree != repo and str(worktree) in worktrees(repo)
    (worktree / "agent.py").write_text("work")
    git(worktree, "add", "agent.py")
    git(worktree, "commit", "-qm", "agent work")
    (worktree / "node_modules" / "dep").mkdir(parents=True)

    delete(client, conversation_id)

    assert not worktree.exists()
    assert not (tmp_path / "worktrees" / str(conversation_id)).exists()
    assert worktrees(repo) == [str(repo.resolve())]
    branch = f"openhands/{conversation_id}"
    assert git(repo, "log", "-1", "--format=%s", branch) == "agent work"
    # The user's own checkout is untouched.
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "branch", "--show-current") == "main"


def test_uncommitted_work_goes_with_the_conversation(client, tmp_path):
    repo = make_repo(tmp_path / "repo")
    conversation_id, worktree = start(client, repo)
    (worktree / "draft.py").write_text("not committed")
    (worktree / "README.md").write_text("edited, not committed")

    delete(client, conversation_id)

    # Only what the agent committed survives, on its branch.
    branch = f"openhands/{conversation_id}"
    assert git(repo, "show", f"{branch}:README.md") == "hi"
    assert "draft.py" not in git(repo, "ls-tree", "--name-only", branch)
    assert not worktree.exists()


def test_delete_succeeds_when_the_source_repository_is_gone(client, tmp_path):
    repo = make_repo(tmp_path / "repo")
    conversation_id, worktree = start(client, repo)
    subprocess.run(["rm", "-rf", str(repo)], check=True)

    delete(client, conversation_id)

    assert not worktree.exists()
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404


def test_a_link_in_place_of_the_worktree_dir_is_not_followed(client, tmp_path):
    repo = make_repo(tmp_path / "repo")
    conversation_id, _ = start(client, repo)
    conversation_dir = tmp_path / "worktrees" / str(conversation_id)
    subprocess.run(["rm", "-rf", str(conversation_dir)], check=True)
    precious = tmp_path / "precious"
    make_repo(precious / "checkout")
    conversation_dir.symlink_to(precious, target_is_directory=True)

    delete(client, conversation_id)

    assert (precious / "checkout" / "README.md").read_text() == "hi"
    assert git(precious / "checkout", "status", "--porcelain") == ""


def test_deleting_one_conversation_leaves_other_worktrees_alone(client, tmp_path):
    repo = make_repo(tmp_path / "repo")
    users_own = tmp_path / "users-own"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(users_own))
    deleted, deleted_worktree = start(client, repo)
    kept, kept_worktree = start(client, repo)
    (kept_worktree / "wip.py").write_text("in progress")

    delete(client, deleted)

    assert not deleted_worktree.exists()
    assert (kept_worktree / "wip.py").read_text() == "in progress"
    assert sorted(worktrees(repo)) == sorted(
        str(p.resolve()) for p in (repo, users_own, kept_worktree)
    )
    assert client.get(f"/api/conversations/{kept}").status_code == 200


def test_a_conversation_without_a_worktree_leaves_the_repository_alone(
    client, tmp_path
):
    repo = make_repo(tmp_path / "repo")
    (repo / "local.txt").write_text("user's file")
    conversation_id, workspace = start(client, repo, worktree=False)
    assert workspace.resolve() == repo.resolve()

    delete(client, conversation_id)

    assert (repo / "local.txt").read_text() == "user's file"
    assert (repo / "README.md").read_text() == "hi"
    assert worktrees(repo) == [str(repo.resolve())]


def test_a_fork_keeps_the_shared_worktree_until_it_is_deleted(client, tmp_path):
    repo = make_repo(tmp_path / "repo")
    source, worktree = start(client, repo)
    response = client.post(f"/api/conversations/{source}/fork", json={})
    assert response.status_code == 201, response.text
    fork = UUID(response.json()["id"])
    assert Path(response.json()["workspace"]["working_dir"]) == worktree
    (worktree / "wip.py").write_text("in progress")

    delete(client, source)

    # The fork works in its source's worktree, so it survives the source.
    assert (worktree / "wip.py").read_text() == "in progress"
    assert str(worktree) in worktrees(repo)

    delete(client, fork)

    assert not worktree.exists()
    assert worktrees(repo) == [str(repo.resolve())]
    assert git(repo, "rev-parse", "--verify", f"openhands/{source}")
