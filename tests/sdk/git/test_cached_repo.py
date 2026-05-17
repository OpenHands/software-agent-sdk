"""Tests for git cached_repo helpers (clone, update, checkout, locking)."""

import subprocess
from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest

from openhands.sdk.git.cached_repo import (
    GitHelper,
    _checkout_ref,
    _clone_repository,
    _update_repository,
)
from openhands.sdk.git.exceptions import GitCommandError


# -- _clone_repository ---------------------------------------------------------


def test_clone_calls_git_helper(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    dest = tmp_path / "repo"

    _clone_repository("https://github.com/owner/repo.git", dest, None, mock_git)

    mock_git.clone.assert_called_once_with(
        "https://github.com/owner/repo.git", dest, depth=1, branch=None
    )


def test_clone_with_ref(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    dest = tmp_path / "repo"

    _clone_repository("https://github.com/owner/repo.git", dest, "v1.0.0", mock_git)

    mock_git.clone.assert_called_once_with(
        "https://github.com/owner/repo.git", dest, depth=1, branch="v1.0.0"
    )


def test_clone_removes_existing_directory(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "some_file.txt").write_text("test")

    _clone_repository("https://github.com/owner/repo.git", dest, None, mock_git)

    mock_git.clone.assert_called_once()


# -- _update_repository --------------------------------------------------------


def test_update_fetches_and_resets(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = "main"

    _update_repository(tmp_path, None, mock_git)

    mock_git.fetch.assert_called_once_with(tmp_path)
    mock_git.get_current_branch.assert_called_once_with(tmp_path)
    mock_git.reset_hard.assert_called_once_with(tmp_path, "origin/main")


def test_update_with_ref_checks_out(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = None

    _update_repository(tmp_path, "v1.0.0", mock_git)

    mock_git.fetch.assert_called_once_with(tmp_path)
    mock_git.checkout.assert_called_once_with(tmp_path, "v1.0.0")


def test_update_detached_head_recovers_to_default_branch(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = None
    mock_git.get_default_branch.return_value = "main"

    _update_repository(tmp_path, None, mock_git)

    mock_git.fetch.assert_called_once()
    mock_git.get_current_branch.assert_called_once()
    mock_git.get_default_branch.assert_called_once_with(tmp_path)
    mock_git.checkout.assert_called_once_with(tmp_path, "main")
    mock_git.reset_hard.assert_called_once_with(tmp_path, "origin/main")


def test_update_detached_head_no_default_branch_logs_warning(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = None
    mock_git.get_default_branch.return_value = None

    _update_repository(tmp_path, None, mock_git)

    mock_git.fetch.assert_called_once()
    mock_git.get_default_branch.assert_called_once()
    mock_git.checkout.assert_not_called()
    mock_git.reset_hard.assert_not_called()


def test_update_continues_on_fetch_error(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.fetch.side_effect = GitCommandError(
        "Network error", command=["git", "fetch"], exit_code=1
    )

    _update_repository(tmp_path, None, mock_git)

    mock_git.fetch.assert_called_once()
    mock_git.get_current_branch.assert_not_called()


def test_update_continues_on_checkout_error(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.checkout.side_effect = GitCommandError(
        "Invalid ref", command=["git", "checkout"], exit_code=1
    )

    _update_repository(tmp_path, "nonexistent", mock_git)


# -- _checkout_ref -------------------------------------------------------------


def test_checkout_branch_resets_to_origin(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = "main"

    _checkout_ref(tmp_path, "main", mock_git)

    mock_git.checkout.assert_called_once_with(tmp_path, "main")
    mock_git.get_current_branch.assert_called_once_with(tmp_path)
    mock_git.reset_hard.assert_called_once_with(tmp_path, "origin/main")


def test_checkout_tag_skips_reset(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = None

    _checkout_ref(tmp_path, "v1.0.0", mock_git)

    mock_git.checkout.assert_called_once_with(tmp_path, "v1.0.0")
    mock_git.reset_hard.assert_not_called()


def test_checkout_commit_skips_reset(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = None

    _checkout_ref(tmp_path, "abc123", mock_git)

    mock_git.checkout.assert_called_once_with(tmp_path, "abc123")
    mock_git.reset_hard.assert_not_called()


def test_checkout_branch_handles_reset_error(tmp_path: Path):
    mock_git = create_autospec(GitHelper)
    mock_git.get_current_branch.return_value = "main"
    mock_git.reset_hard.side_effect = GitCommandError(
        "Reset failed", command=["git", "reset"], exit_code=1
    )

    _checkout_ref(tmp_path, "main", mock_git)

    mock_git.checkout.assert_called_once()
    mock_git.reset_hard.assert_called_once()


# -- GitHelper error handling --------------------------------------------------


def test_git_clone_called_process_error(tmp_path: Path):
    git = GitHelper()
    dest = tmp_path / "repo"

    with pytest.raises(GitCommandError, match="git clone"):
        git.clone("https://invalid.example.com/nonexistent.git", dest, timeout=5)


def test_git_clone_timeout(tmp_path: Path):
    git = GitHelper()
    dest = tmp_path / "repo"

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
        with pytest.raises(GitCommandError, match="timed out"):
            git.clone("https://github.com/owner/repo.git", dest, timeout=1)


def test_git_fetch_with_ref_no_remote(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("content")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo, check=True)

    git = GitHelper()
    with pytest.raises(GitCommandError, match="git fetch"):
        git.fetch(repo, ref="main")


def test_git_fetch_called_process_error(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "not-a-repo"
    repo.mkdir()

    with pytest.raises(GitCommandError, match="git fetch"):
        git.fetch(repo)


def test_git_fetch_timeout(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
        with pytest.raises(GitCommandError, match="timed out"):
            git.fetch(repo, timeout=1)


def test_git_checkout_called_process_error(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)

    with pytest.raises(GitCommandError, match="git checkout"):
        git.checkout(repo, "nonexistent-ref")


def test_git_checkout_timeout(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
        with pytest.raises(GitCommandError, match="timed out"):
            git.checkout(repo, "main", timeout=1)


def test_git_reset_hard_called_process_error(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)

    with pytest.raises(GitCommandError, match="git reset"):
        git.reset_hard(repo, "nonexistent-ref")


def test_git_reset_hard_timeout(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
        with pytest.raises(GitCommandError, match="timed out"):
            git.reset_hard(repo, "HEAD", timeout=1)


def test_git_get_current_branch_error(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "not-a-repo"
    repo.mkdir()

    with pytest.raises(GitCommandError, match="git rev-parse"):
        git.get_current_branch(repo)


def test_git_get_current_branch_timeout(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=1)
        with pytest.raises(GitCommandError, match="timed out"):
            git.get_current_branch(repo, timeout=1)


# -- GitHelper.get_default_branch ---------------------------------------------


def test_get_default_branch_returns_main(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="refs/remotes/origin/main\n",
            stderr="",
        )
        result = git.get_default_branch(repo)

    assert result == "main"
    call_args = mock_run.call_args[0][0]
    assert call_args == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]


def test_get_default_branch_returns_master(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="refs/remotes/origin/master\n",
            stderr="",
        )
        result = git.get_default_branch(repo)

    assert result == "master"


def test_get_default_branch_returns_none_when_not_set(tmp_path: Path):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=1,
            stdout="",
            stderr=("fatal: ref refs/remotes/origin/HEAD is not a symbolic ref"),
        )
        result = git.get_default_branch(repo)

    assert result is None


def test_get_default_branch_returns_none_on_unexpected_format(
    tmp_path: Path,
):
    git = GitHelper()
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("openhands.sdk.git.utils.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="unexpected-format\n",
            stderr="",
        )
        result = git.get_default_branch(repo)

    assert result is None


# -- Cache locking -------------------------------------------------------------


def test_lock_file_created_during_clone(tmp_path: Path):
    from openhands.sdk.git.cached_repo import try_cached_clone_or_update

    cache_dir = tmp_path / "cache"
    repo_path = cache_dir / "test-repo"

    mock_git = create_autospec(GitHelper, instance=True)
    lock_existed_during_clone: list[bool] = []

    def mock_clone(url, dest, depth=None, branch=None, timeout=120):
        lock_path = repo_path.with_suffix(".lock")
        lock_existed_during_clone.append(lock_path.exists())

    mock_git.clone.side_effect = mock_clone

    try_cached_clone_or_update(
        url="https://github.com/test/repo.git",
        repo_path=repo_path,
        git_helper=mock_git,
    )

    assert lock_existed_during_clone[0] is True


def test_lock_timeout_returns_none(tmp_path: Path):
    from filelock import FileLock

    from openhands.sdk.git.cached_repo import try_cached_clone_or_update

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    repo_path = cache_dir / "test-repo"

    lock_path = repo_path.with_suffix(".lock")
    external_lock = FileLock(lock_path)
    external_lock.acquire()

    try:
        mock_git = create_autospec(GitHelper, instance=True)

        result = try_cached_clone_or_update(
            url="https://github.com/test/repo.git",
            repo_path=repo_path,
            git_helper=mock_git,
            lock_timeout=0.1,
        )

        assert result is None
        mock_git.clone.assert_not_called()
    finally:
        external_lock.release()


def test_lock_released_after_operation(tmp_path: Path):
    from filelock import FileLock

    from openhands.sdk.git.cached_repo import try_cached_clone_or_update

    cache_dir = tmp_path / "cache"
    repo_path = cache_dir / "test-repo"

    mock_git = create_autospec(GitHelper, instance=True)

    try_cached_clone_or_update(
        url="https://github.com/test/repo.git",
        repo_path=repo_path,
        git_helper=mock_git,
    )

    lock_path = repo_path.with_suffix(".lock")
    lock = FileLock(lock_path)
    lock.acquire(timeout=0)
    lock.release()


def test_lock_released_on_error(tmp_path: Path):
    from filelock import FileLock

    from openhands.sdk.git.cached_repo import try_cached_clone_or_update

    cache_dir = tmp_path / "cache"
    repo_path = cache_dir / "test-repo"

    mock_git = create_autospec(GitHelper, instance=True)
    mock_git.clone.side_effect = GitCommandError(
        "Clone failed", command=["git", "clone"], exit_code=1, stderr="error"
    )

    result = try_cached_clone_or_update(
        url="https://github.com/test/repo.git",
        repo_path=repo_path,
        git_helper=mock_git,
    )

    assert result is None

    lock_path = repo_path.with_suffix(".lock")
    lock = FileLock(lock_path)
    lock.acquire(timeout=0)
    lock.release()
