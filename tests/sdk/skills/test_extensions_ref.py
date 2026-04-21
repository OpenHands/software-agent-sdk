"""Tests for EXTENSIONS_REF environment variable support.

These tests use subprocess to run each test in an isolated Python process,
avoiding module state pollution that would affect other tests.
"""

import subprocess
import sys


def _run_in_subprocess(test_code: str, env_extra: dict | None = None) -> None:
    """Run test code in a subprocess with the given environment variables."""
    import os

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        [sys.executable, "-c", test_code],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Subprocess test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_extensions_ref_default():
    """PUBLIC_SKILLS_BRANCH should default to 'main' when EXTENSIONS_REF is not set."""
    code = """
import os
if "EXTENSIONS_REF" in os.environ:
    del os.environ["EXTENSIONS_REF"]
from openhands.sdk.skills.skill import PUBLIC_SKILLS_BRANCH
assert PUBLIC_SKILLS_BRANCH == "main", (
    f"Expected 'main' but got '{PUBLIC_SKILLS_BRANCH}'"
)
"""
    _run_in_subprocess(code)


def test_extensions_ref_custom_branch():
    """PUBLIC_SKILLS_BRANCH should use EXTENSIONS_REF when set."""
    code = """
from openhands.sdk.skills.skill import PUBLIC_SKILLS_BRANCH
assert PUBLIC_SKILLS_BRANCH == "feature-branch", (
    f"Expected 'feature-branch' but got '{PUBLIC_SKILLS_BRANCH}'"
)
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": "feature-branch"})


def test_extensions_ref_with_load_public_skills():
    """load_public_skills should respect EXTENSIONS_REF environment variable."""
    code = """
from unittest import mock
from openhands.sdk.skills.skill import (
    PUBLIC_SKILLS_BRANCH,
    load_public_skills,
)
assert PUBLIC_SKILLS_BRANCH == "test-branch", (
    f"Expected 'test-branch' but got '{PUBLIC_SKILLS_BRANCH}'"
)
with mock.patch(
    "openhands.sdk.skills.skill.update_skills_repository"
) as mock_update:
    mock_update.return_value = None
    load_public_skills()
    mock_update.assert_called_once()
    call_args = mock_update.call_args
    # branch is 2nd positional arg: (repo_url, branch, cache_dir)
    assert call_args[0][1] == "test-branch", (
        f"Expected branch='test-branch' but got {call_args[0][1]}"
    )
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": "test-branch"})


def test_extensions_ref_empty_string():
    """Empty EXTENSIONS_REF should fall back to 'main'."""
    code = """
from openhands.sdk.skills.skill import PUBLIC_SKILLS_BRANCH
# Empty string returns empty string per os.environ.get behavior
assert PUBLIC_SKILLS_BRANCH == "", (
    f"Expected '' but got '{PUBLIC_SKILLS_BRANCH}'"
)
"""
    _run_in_subprocess(code, {"EXTENSIONS_REF": ""})
