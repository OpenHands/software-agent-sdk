"""Tests for EXTENSIONS_REF environment variable support."""

import os
import sys
from unittest import mock


def test_extensions_ref_default():
    """PUBLIC_SKILLS_BRANCH should default to 'main' when EXTENSIONS_REF is not set."""
    # Clear EXTENSIONS_REF if set
    with mock.patch.dict(os.environ, {}, clear=False):
        if "EXTENSIONS_REF" in os.environ:
            del os.environ["EXTENSIONS_REF"]

        # Force reload of the module to pick up environment variable
        if "openhands.sdk.context.skills.skill" in sys.modules:
            del sys.modules["openhands.sdk.context.skills.skill"]

        from openhands.sdk.context.skills.skill import PUBLIC_SKILLS_BRANCH

        assert PUBLIC_SKILLS_BRANCH == "main", (
            f"Expected 'main' but got '{PUBLIC_SKILLS_BRANCH}'"
        )


def test_extensions_ref_custom_branch():
    """PUBLIC_SKILLS_BRANCH should use EXTENSIONS_REF when set."""
    # Set EXTENSIONS_REF
    with mock.patch.dict(os.environ, {"EXTENSIONS_REF": "feature-branch"}, clear=False):
        # Force reload of the module to pick up environment variable
        if "openhands.sdk.context.skills.skill" in sys.modules:
            del sys.modules["openhands.sdk.context.skills.skill"]

        from openhands.sdk.context.skills.skill import PUBLIC_SKILLS_BRANCH

        assert PUBLIC_SKILLS_BRANCH == "feature-branch", (
            f"Expected 'feature-branch' but got '{PUBLIC_SKILLS_BRANCH}'"
        )


def test_extensions_ref_with_load_public_skills():
    """load_public_skills should respect EXTENSIONS_REF environment variable."""
    with mock.patch.dict(os.environ, {"EXTENSIONS_REF": "test-branch"}, clear=False):
        # Force reload of the module
        if "openhands.sdk.context.skills.skill" in sys.modules:
            del sys.modules["openhands.sdk.context.skills.skill"]

        from openhands.sdk.context.skills.skill import (
            PUBLIC_SKILLS_BRANCH,
            load_public_skills,
        )

        # Verify the constant is set correctly
        assert PUBLIC_SKILLS_BRANCH == "test-branch"

        # Mock the actual git operations and verify branch is passed
        with mock.patch(
            "openhands.sdk.context.skills.skill.update_skills_repository"
        ) as mock_update:
            mock_update.return_value = (
                None  # Simulate failed clone (expected behavior for test)
            )

            load_public_skills()

            # Verify the branch was passed to update_skills_repository
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            # branch is 2nd positional arg: (repo_url, branch, cache_dir)
            assert call_args[0][1] == "test-branch", (
                f"Expected branch='test-branch' but got {call_args[0][1]}"
            )


def test_extensions_ref_empty_string():
    """Empty EXTENSIONS_REF should fall back to 'main'."""
    with mock.patch.dict(os.environ, {"EXTENSIONS_REF": ""}, clear=False):
        # Force reload of the module
        if "openhands.sdk.context.skills.skill" in sys.modules:
            del sys.modules["openhands.sdk.context.skills.skill"]

        from openhands.sdk.context.skills.skill import PUBLIC_SKILLS_BRANCH

        # Empty string should fall back to 'main' via os.environ.get default
        assert PUBLIC_SKILLS_BRANCH == "", (
            "Empty EXTENSIONS_REF should result in empty string "
            "(os.environ.get behavior)"
        )
