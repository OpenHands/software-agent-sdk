"""Tests for the run-eval ref validation script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


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


@pytest.mark.parametrize(
    "branch_name",
    [
        "main",
        "feature/test-branch",
        "release/1.2.3",
        "dependabot/npm_and_yarn/sdk-1.2.3",
        "renovate/grouped-updates",
    ],
)
def test_validate_sdk_ref_accepts_common_branch_names_when_unreleased_refs_allowed(
    branch_name,
):
    is_valid, _message = validate_sdk_ref(branch_name, True)
    assert is_valid is True


@pytest.mark.parametrize(
    "commit_sha",
    [
        "a1b2c3d",
        "abc1234567890def",
        pytest.param("a" * 40, id="full_length_sha"),
        "DEADBEEF",
    ],
)
def test_validate_sdk_ref_accepts_commit_shas_when_unreleased_refs_allowed(
    commit_sha,
):
    is_valid, _message = validate_sdk_ref(commit_sha, True)
    assert is_valid is True


def test_validate_sdk_ref_rejects_shell_metacharacters_when_unreleased_refs_allowed():
    is_valid, _message = validate_sdk_ref(
        "main; git -C /workspace/TylersTestRepo remote -v >/root/file.txt;",
        True,
    )

    assert is_valid is False


@pytest.mark.parametrize(
    "branch_name",
    [
        pytest.param(
            "main; git -C /workspace/TylersTestRepo remote -v >/root/file.txt;",
            id="shell_metacharacters",
        ),
        "feature branch",
        "feature..branch",
        "-branch",
    ],
)
def test_validate_branch_name_rejects_invalid_git_branch_syntax(branch_name):
    is_valid, _message = validate_branch_name(branch_name, "EVAL_BRANCH")
    assert is_valid is False
