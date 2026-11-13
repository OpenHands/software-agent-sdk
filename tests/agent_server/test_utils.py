import os
from pathlib import Path

import pytest

from openhands.agent_server.utils import safe_rmtree


needs_permission_checks = pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="root ignores directory permissions",
)


def _restore_permissions(path: Path) -> None:
    os.chmod(path, 0o700)
    if path.is_dir() and not path.is_symlink():
        for child in path.iterdir():
            _restore_permissions(child)


@needs_permission_checks
def test_safe_rmtree_leaves_a_symlink_target_untouched(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    mode_before = outside.stat().st_mode
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    try:
        removed = safe_rmtree(link)
        assert removed is not link.is_symlink()
        assert outside.stat().st_mode == mode_before
    finally:
        _restore_permissions(outside)
    assert (outside / "precious").read_text() == "keep"


@needs_permission_checks
def test_safe_rmtree_result_matches_whether_tree_is_gone(tmp_path):
    target = tmp_path / "tree"
    locked = target / "locked"
    locked.mkdir(parents=True)
    (locked / "file.txt").write_text("x")
    locked.chmod(0o500)
    try:
        removed = safe_rmtree(target)
        assert removed is not target.exists()
    finally:
        if target.exists():
            _restore_permissions(target)


@needs_permission_checks
def test_safe_rmtree_on_a_file_reports_failure_or_removes_it(tmp_path):
    target = tmp_path / "plain.txt"
    target.write_text("x")
    try:
        removed = safe_rmtree(target)
        assert removed is not target.exists()
    finally:
        if target.exists():
            _restore_permissions(target)
