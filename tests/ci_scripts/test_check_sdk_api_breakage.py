"""Tests for SDK API breakage check script.

These tests verify the core policy logic without requiring griffe or network access.
The functions are duplicated here to avoid import issues with the .github/scripts path.
"""

from packaging import version as pkg_version


def _parse_version(v: str) -> pkg_version.Version:
    """Parse a version string using packaging (mirrors script implementation)."""
    return pkg_version.parse(v)


def _check_version_bump(prev: str, new_version: str, total_breaks: int) -> int:
    """Check version bump policy (mirrors script implementation).

    Policy: Breaking changes require at least a MINOR version bump.
    Returns 0 if policy satisfied, 1 if not.
    """
    if total_breaks == 0:
        return 0

    parsed_prev = _parse_version(prev)
    parsed_new = _parse_version(new_version)

    ok = (parsed_new.major > parsed_prev.major) or (
        parsed_new.major == parsed_prev.major and parsed_new.minor > parsed_prev.minor
    )

    return 0 if ok else 1


def test_parse_version_simple():
    v = _parse_version("1.2.3")
    assert v.major == 1
    assert v.minor == 2
    assert v.micro == 3


def test_parse_version_prerelease():
    v = _parse_version("1.2.3a1")
    assert v.major == 1
    assert v.minor == 2


def test_no_breaks_passes():
    """No breaking changes should always pass."""
    assert _check_version_bump("1.0.0", "1.0.1", total_breaks=0) == 0


def test_minor_bump_with_breaks_passes():
    """MINOR bump satisfies policy for breaking changes."""
    assert _check_version_bump("1.0.0", "1.1.0", total_breaks=1) == 0
    assert _check_version_bump("1.5.3", "1.6.0", total_breaks=5) == 0


def test_major_bump_with_breaks_passes():
    """MAJOR bump also satisfies policy for breaking changes."""
    assert _check_version_bump("1.0.0", "2.0.0", total_breaks=1) == 0
    assert _check_version_bump("1.5.3", "2.0.0", total_breaks=10) == 0


def test_patch_bump_with_breaks_fails():
    """PATCH bump should fail when there are breaking changes."""
    assert _check_version_bump("1.0.0", "1.0.1", total_breaks=1) == 1
    assert _check_version_bump("1.5.3", "1.5.4", total_breaks=1) == 1


def test_same_version_with_breaks_fails():
    """Same version should fail when there are breaking changes."""
    assert _check_version_bump("1.0.0", "1.0.0", total_breaks=1) == 1


def test_prerelease_versions():
    """Pre-release versions should work correctly."""
    # 1.1.0a1 has minor=1, so it satisfies minor bump from 1.0.0
    assert _check_version_bump("1.0.0", "1.1.0a1", total_breaks=1) == 0
    # 1.0.1a1 is still a patch bump
    assert _check_version_bump("1.0.0", "1.0.1a1", total_breaks=1) == 1
