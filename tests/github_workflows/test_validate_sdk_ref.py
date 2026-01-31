"""Tests for validate_sdk_ref.py GitHub Actions script."""

import sys
from pathlib import Path

import pytest


# Import the functions from validate_sdk_ref.py
run_eval_path = Path(__file__).parent.parent.parent / ".github" / "run-eval"
sys.path.insert(0, str(run_eval_path))
from validate_sdk_ref import (  # noqa: E402  # type: ignore[import-not-found]
    is_semantic_version,
    validate_sdk_ref,
)


class TestIsSemanticVersion:
    """Tests for is_semantic_version function."""

    @pytest.mark.parametrize(
        "version",
        [
            "1.0.0",
            "v1.0.0",
            "0.1.0",
            "v0.1.0",
            "10.20.30",
            "v10.20.30",
            "1.2.3",
            "v1.2.3",
            "0.0.1",
            "v0.0.1",
        ],
    )
    def test_valid_basic_versions(self, version):
        """Test that basic semantic versions are recognized."""
        assert is_semantic_version(version) is True

    @pytest.mark.parametrize(
        "version",
        [
            "1.0.0-alpha",
            "v1.0.0-alpha",
            "1.0.0-alpha.1",
            "v1.0.0-alpha.1",
            "1.0.0-beta",
            "v1.0.0-beta",
            "1.0.0-beta.2",
            "v1.0.0-beta.2",
            "1.0.0-rc.1",
            "v1.0.0-rc.1",
            "1.0.0-0.3.7",
            "1.0.0-x.7.z.92",
        ],
    )
    def test_valid_prerelease_versions(self, version):
        """Test that pre-release semantic versions are recognized."""
        assert is_semantic_version(version) is True

    @pytest.mark.parametrize(
        "version",
        [
            "1.0.0+build",
            "v1.0.0+build",
            "1.0.0+build.123",
            "v1.0.0+build.123",
            "1.0.0-alpha+build",
            "v1.0.0-alpha.1+build.456",
        ],
    )
    def test_valid_build_metadata_versions(self, version):
        """Test that versions with build metadata are recognized."""
        assert is_semantic_version(version) is True

    @pytest.mark.parametrize(
        "ref",
        [
            "main",
            "master",
            "develop",
            "feature/my-feature",
            "fix/bug-123",
            "release/1.0",
            "abc123def",
            "1.0",
            "v1.0",
            "1",
            "v1",
            "1.0.0.0",
            "v1.0.0.0",
            "",
            "latest",
            "HEAD",
        ],
    )
    def test_invalid_versions(self, ref):
        """Test that non-semantic versions are rejected."""
        assert is_semantic_version(ref) is False


class TestValidateSdkRef:
    """Tests for validate_sdk_ref function."""

    def test_valid_semver_without_override(self):
        """Test that valid semantic version passes without override."""
        is_valid, message = validate_sdk_ref("v1.0.0", allow_unreleased=False)
        assert is_valid is True
        assert "Valid semantic version" in message

    def test_invalid_ref_without_override(self):
        """Test that invalid ref fails without override."""
        is_valid, message = validate_sdk_ref("main", allow_unreleased=False)
        assert is_valid is False
        assert "not a valid semantic version" in message
        assert "Allow unreleased branches" in message

    def test_invalid_ref_with_override(self):
        """Test that invalid ref passes with override."""
        is_valid, message = validate_sdk_ref("main", allow_unreleased=True)
        assert is_valid is True
        assert "Allowing unreleased branch" in message

    def test_valid_semver_with_override(self):
        """Test that valid semantic version passes with override."""
        is_valid, message = validate_sdk_ref("v1.0.0", allow_unreleased=True)
        assert is_valid is True
        assert "Allowing unreleased branch" in message

    def test_branch_name_fails(self):
        """Test that branch names fail validation."""
        is_valid, _ = validate_sdk_ref("feature/new-feature", allow_unreleased=False)
        assert is_valid is False

    def test_commit_sha_fails(self):
        """Test that commit SHAs fail validation."""
        is_valid, _ = validate_sdk_ref("abc123def456", allow_unreleased=False)
        assert is_valid is False

    def test_prerelease_version_passes(self):
        """Test that pre-release versions pass validation."""
        is_valid, _ = validate_sdk_ref("v1.0.0-alpha.1", allow_unreleased=False)
        assert is_valid is True

    def test_version_with_build_metadata_passes(self):
        """Test that versions with build metadata pass validation."""
        is_valid, _ = validate_sdk_ref("v1.0.0+build.123", allow_unreleased=False)
        assert is_valid is True
