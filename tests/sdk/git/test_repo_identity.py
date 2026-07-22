"""Tests for the observability repo-identity probe."""

import subprocess
from pathlib import Path

import pytest

from openhands.sdk.git.utils import (
    _repo_slug_and_provider,
    resolve_git_repo_root,
    resolve_repo_identity,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/repo.git", ("owner/repo", "github")),
        ("https://github.com/owner/repo", ("owner/repo", "github")),
        (
            "HTTPS://user:SECRET@github.com/owner/repo.git",
            ("owner/repo", "github"),
        ),
        (
            "https://github.com/owner/repo.git?access_token=SECRET&depth=1",
            ("owner/repo", "github"),
        ),
        ("git@github.com:owner/repo.git", ("owner/repo", "github")),
        ("ssh://git@bitbucket.org/team/repo.git", ("team/repo", "bitbucket")),
        (
            "https://dev.azure.com/org/project/_git/repo",
            ("org/project/repo", "azure_devops"),
        ),
        (
            "git@ssh.dev.azure.com:v3/org/project/repo",
            ("org/project/repo", "azure_devops"),
        ),
        (
            "https://org.visualstudio.com/project/_git/repo",
            ("org/project/repo", "azure_devops"),
        ),
        (
            "https://bitbucket.example.com/scm/proj/repo.git",
            ("PROJ/repo", "bitbucket_data_center"),
        ),
        ("https://codeberg.org/owner/repo.git", ("owner/repo", "forgejo")),
        ("https://github.com/scm/repo.git", ("scm/repo", "github")),
        # GitLab subgroups keep the full path, not just the last two segments.
        ("https://gitlab.com/group/sub/proj.git", ("group/sub/proj", "gitlab")),
        # Self-hosted host still maps to the provider by name.
        ("https://gitlab.example.com/a/b", ("a/b", "gitlab")),
        # Unknown host: slug parsed, provider unknown.
        ("https://git.example.com/a/b.git", ("a/b", None)),
        ("file:///workspace/private/repo.git", (None, None)),
        # Not enough path segments -> no slug.
        ("https://github.com/onlyone", (None, "github")),
    ],
)
def test_repo_slug_and_provider(url, expected):
    assert _repo_slug_and_provider(url) == expected


def _init_repo(path: Path, remote: str | None = None, commit: bool = False) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    if remote:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", remote], check=True
        )
    if commit:
        (path / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)


def test_resolve_git_repo_root_at_base(tmp_path):
    _init_repo(tmp_path)
    assert resolve_git_repo_root(tmp_path) == tmp_path


def test_resolve_git_repo_root_in_subdir(tmp_path):
    """The clone-later flow puts the repo one level below the workspace base."""
    sub = tmp_path / "myrepo"
    sub.mkdir()
    _init_repo(sub)
    assert resolve_git_repo_root(tmp_path) == sub


def test_resolve_git_repo_root_from_repo_subdirectory(tmp_path):
    repo = tmp_path / "repo"
    workspace = repo / "packages" / "sdk"
    workspace.mkdir(parents=True)
    _init_repo(repo)

    assert resolve_git_repo_root(workspace) == repo


def test_resolve_git_repo_root_ambiguous_returns_none(tmp_path):
    for name in ("a", "b"):
        d = tmp_path / name
        d.mkdir()
        _init_repo(d)
    assert resolve_git_repo_root(tmp_path) is None


def test_resolve_git_repo_root_none_when_absent(tmp_path):
    assert resolve_git_repo_root(tmp_path) is None


def test_resolve_repo_identity_full(tmp_path):
    sub = tmp_path / "repo"
    sub.mkdir()
    _init_repo(sub, remote="https://github.com/OpenHands/OpenHands.git", commit=True)
    identity = resolve_repo_identity(tmp_path)
    assert identity["repo"] == "OpenHands/OpenHands"
    assert identity["git_provider"] == "github"
    assert identity["branch"]  # some branch name
    assert len(identity["commit"]) == 40


def test_resolve_repo_identity_does_not_expose_remote_credentials(tmp_path):
    _init_repo(
        tmp_path,
        remote=(
            "HTTPS://user:SECRET@github.com/OpenHands/OpenHands.git"
            "?access_token=OTHER_SECRET"
        ),
        commit=True,
    )

    identity = resolve_repo_identity(tmp_path)

    assert identity["repo"] == "OpenHands/OpenHands"
    assert "SECRET" not in str(identity)


def test_resolve_repo_identity_requires_origin(tmp_path):
    """A local-only repo (no origin) is ignored so scratch git never pollutes."""
    _init_repo(tmp_path, remote=None, commit=True)
    assert resolve_repo_identity(tmp_path) == {}


def test_resolve_repo_identity_no_repo(tmp_path):
    assert resolve_repo_identity(tmp_path) == {}


def test_resolve_repo_identity_unborn_head_omits_commit(tmp_path):
    """A cloned repo with no commit yet yields repo but no commit field."""
    _init_repo(tmp_path, remote="https://github.com/o/r.git", commit=False)
    identity = resolve_repo_identity(tmp_path)
    assert identity["repo"] == "o/r"
    assert "commit" not in identity
