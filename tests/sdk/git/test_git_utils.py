"""Tests for git utils base-ref resolution (``get_valid_ref``)."""

import subprocess
import tempfile
from pathlib import Path

from openhands.sdk.git.utils import GIT_EMPTY_TREE_HASH, get_valid_ref


def run_bash_command(command: str, cwd: str) -> subprocess.CompletedProcess:
    """Run a bash command in the specified directory."""
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_get_valid_ref_purposes_diverge_for_committed_no_remote_repo():
    """The display purpose resolves HEAD for a committed repo without a
    remote (so the Diff view gets git-status semantics), while the default
    export purpose keeps resolving the empty tree — workspace-export patches
    must stay reconstructable from a fresh clone, so the display policy must
    not leak into it."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange — a repo with a commit and no remote.
        run_bash_command("git init -b main", temp_dir)
        run_bash_command("git config user.name 'Test User'", temp_dir)
        run_bash_command("git config user.email 'test@example.com'", temp_dir)
        (Path(temp_dir) / "a.txt").write_text("a")
        run_bash_command("git add .", temp_dir)
        run_bash_command("git commit -m 'base'", temp_dir)
        head_sha = run_bash_command("git rev-parse HEAD", temp_dir).stdout.strip()

        # Act
        export_ref = get_valid_ref(temp_dir)
        display_ref = get_valid_ref(temp_dir, purpose="display")

        # Assert
        assert export_ref == GIT_EMPTY_TREE_HASH
        assert display_ref == head_sha
