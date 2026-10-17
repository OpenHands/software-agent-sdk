"""Opt-in coverage through the real Docker conversation API, without an LLM call.

Set OPENHANDS_DOCKER_WORKTREE_TEST_IMAGE to an already-built Agent Server image
containing the checkout under test. Docker must be available to the test user.
"""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


def test_docker_worktrees_remain_host_resolvable_after_runtime_lifecycle(
    tmp_path, monkeypatch
):
    image = os.environ.get("OPENHANDS_DOCKER_WORKTREE_TEST_IMAGE")
    if not image:
        pytest.skip("Set OPENHANDS_DOCKER_WORKTREE_TEST_IMAGE to run real Docker")
    subprocess.run(
        ["docker", "image", "inspect", image], check=True, capture_output=True
    )
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*args, cwd=repository):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()

    git("init", "-q", "-b", "main")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    app = create_app(
        Config(
            conversation_runtime="docker",
            conversation_image=image,
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspace",
            secret_key=SecretStr("test-outer-key"),
        )
    )
    first, second = uuid4(), uuid4()

    with TestClient(app) as client:
        registry = app.state.conversation_registry
        try:
            for cid in (first, second):
                response = client.post(
                    "/api/conversations",
                    json={
                        "conversation_id": str(cid),
                        "worktree": True,
                        "agent": {
                            "kind": "Agent",
                            "llm": {"model": "gpt-4o", "api_key": "test-key"},
                            "tools": [{"name": "TerminalTool"}],
                        },
                        "workspace": {
                            "kind": "LocalWorkspace",
                            "working_dir": str(repository),
                        },
                    },
                )
                assert response.status_code == 201, response.text
            first_root = (
                registry.provisioning.runtime_dir(first)
                / "worktrees"
                / str(first)
                / repository.name
            )
            host_view = git("worktree", "list", "--porcelain")
            print("HOST_AFTER_CREATE", host_view)
            assert f"worktree {first_root}" in host_view
            assert "prunable" not in host_view
            gitdir = Path(
                (first_root / ".git").read_text().strip().removeprefix("gitdir: ")
            )
            assert gitdir.is_dir()
            assert Path((gitdir / "gitdir").read_text().strip()) == first_root / ".git"

            response = client.post(
                f"/api/conversations/{first}/bash/execute_bash_command",
                json={
                    "command": "git switch -c live-worktree-branch",
                    "cwd": str(first_root),
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["exit_code"] == 0, response.text
            assert (
                git("branch", "--show-current", cwd=first_root)
                == "live-worktree-branch"
            )
            print("BRANCH_SWITCH", response.status_code)

            response = client.delete(f"/api/conversations/{first}/runtime")
            assert response.status_code == 204, response.text
            response = client.post(f"/api/conversations/{first}/runtime/reprovision")
            assert response.status_code == 200, response.text
            assert (
                git("branch", "--show-current", cwd=first_root)
                == "live-worktree-branch"
            )
            print("REPROVISION", response.status_code)
            response = client.delete(f"/api/conversations/{second}")
            assert response.status_code == 200, response.text
            git("status", "--porcelain", cwd=first_root)
            host_view = git("worktree", "list", "--porcelain")
            print("HOST_AFTER_SIBLING_DELETE", host_view)
            assert f"worktree {first_root}" in host_view
            assert "prunable" not in host_view
            response = client.delete(f"/api/conversations/{first}")
            assert response.status_code == 200, response.text
            assert not first_root.exists()
            assert not registry.provisioning.manifest_path(first).exists()
            assert not registry.provisioning.runtime_dir(first).exists()
            assert f"worktree {first_root}" not in git(
                "worktree", "list", "--porcelain"
            )
            print("DELETE_CLEANUP", response.status_code)
        finally:
            # Only conversations owned by this fixture are released, even on failure.
            for cid in (second, first):
                client.delete(f"/api/conversations/{cid}/runtime")
                client.delete(f"/api/conversations/{cid}")
