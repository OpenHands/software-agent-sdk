"""Tests for the run-eval ref validation script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "run-eval" / "validate_sdk_ref.py"
    name = "validate_sdk_ref"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
validate_branch_name = _prod.validate_branch_name
validate_sdk_ref = _prod.validate_sdk_ref


def test_validate_sdk_ref_accepts_common_branch_names_when_unreleased_refs_allowed():
    for branch_name in (
        "main",
        "feature/test-branch",
        "release/1.2.3",
        "dependabot/npm_and_yarn/sdk-1.2.3",
        "renovate/grouped-updates",
    ):
        is_valid, _message = validate_sdk_ref(branch_name, True)
        assert is_valid is True


def test_validate_sdk_ref_accepts_commit_shas_when_unreleased_refs_allowed():
    for commit_sha in (
        "a1b2c3d",
        "abc1234567890def",
        "a" * 40,
        "DEADBEEF",
    ):
        is_valid, _message = validate_sdk_ref(commit_sha, True)
        assert is_valid is True


def test_validate_sdk_ref_rejects_shell_metacharacters_when_unreleased_refs_allowed():
    is_valid, _message = validate_sdk_ref(
        "main; git -C /workspace/TylersTestRepo remote -v >/root/file.txt;",
        True,
    )

    assert is_valid is False


def test_validate_branch_name_rejects_invalid_git_branch_syntax():
    for branch_name in (
        "main; git -C /workspace/TylersTestRepo remote -v >/root/file.txt;",
        "feature branch",
        "feature..branch",
        "-branch",
    ):
        is_valid, _message = validate_branch_name(branch_name, "EVAL_BRANCH")
        assert is_valid is False
