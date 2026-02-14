"""Tests for SDK API breakage check script.

We import the production script via a file-based module load (rather than copying
functions) so tests remain coupled to real behavior.

These tests cover the SemVer policy helper functions and do not require griffe or
network access.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / "check_sdk_api_breakage.py"
    spec = importlib.util.spec_from_file_location("check_sdk_api_breakage", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
_parse_version = _prod._parse_version
_check_version_bump = _prod._check_version_bump


def test_griffe_breakage_removed_attribute_requires_minor_bump(tmp_path):
    griffe = __import__("griffe")

    old_pkg = tmp_path / "old" / "openhands" / "sdk" / "llm"
    new_pkg = tmp_path / "new" / "openhands" / "sdk" / "llm"
    old_pkg.mkdir(parents=True)
    new_pkg.mkdir(parents=True)

    (old_pkg / "message.py").write_text(
        """
class TextContent:
    def __init__(self, text: str):
        self.text = text
        self.enable_truncation = True
""".lstrip()
    )
    (new_pkg / "message.py").write_text(
        """
class TextContent:
    def __init__(self, text: str):
        self.text = text
""".lstrip()
    )

    old_root = griffe.load(
        "openhands.sdk.llm.message", search_paths=[str(tmp_path / "old")]
    )
    new_root = griffe.load(
        "openhands.sdk.llm.message", search_paths=[str(tmp_path / "new")]
    )

    total_breaks = _prod._compute_breakages(
        old_root, new_root, include=["openhands.sdk.llm.message.TextContent"]
    )
    assert total_breaks > 0

    assert _check_version_bump("1.11.3", "1.11.4", total_breaks=total_breaks) == 1
    assert _check_version_bump("1.11.3", "1.12.0", total_breaks=total_breaks) == 0


def test_griffe_removed_export_from_all_is_breaking(tmp_path):
    griffe = __import__("griffe")

    def write_init(root: str, all_names: list[str]):
        pkg = tmp_path / root / "openhands" / "sdk"
        pkg.mkdir(parents=True)
        (tmp_path / root / "openhands" / "__init__.py").write_text("")
        (pkg / "__init__.py").write_text(
            "__all__ = [\n"
            + "\n".join(f"    {name!r}," for name in all_names)
            + "\n]\n"
        )

    write_init("old", ["Foo", "Bar"])
    write_init("new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks = _prod._compute_breakages(
        old_root, new_root, include=["openhands.sdk"]
    )
    assert total_breaks == 1


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
