"""Tests for SDK API breakage check script.

We import the production script via a file-based module load (rather than copying
functions) so tests remain coupled to real behavior.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import griffe


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
_find_deprecated_symbols = _prod._find_deprecated_symbols


def _write_sdk_init(tmp_path, root: str, all_names: list[str]):
    """Helper to create a minimal openhands.sdk package with __all__."""
    pkg = tmp_path / root / "openhands" / "sdk"
    pkg.mkdir(parents=True, exist_ok=True)
    (tmp_path / root / "openhands" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text(
        "__all__ = [\n" + "\n".join(f"    {name!r}," for name in all_names) + "\n]\n"
    )
    return pkg


def test_griffe_breakage_removed_attribute_requires_minor_bump(tmp_path):
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

    total_breaks, _undeprecated = _prod._compute_breakages(
        old_root, new_root, include=["openhands.sdk.llm.message.TextContent"]
    )
    assert total_breaks > 0

    assert _check_version_bump("1.11.3", "1.11.4", total_breaks=total_breaks) == 1
    assert _check_version_bump("1.11.3", "1.12.0", total_breaks=total_breaks) == 0


def test_griffe_removed_export_from_all_is_breaking(tmp_path):
    _write_sdk_init(tmp_path, "old", ["Foo", "Bar"])
    _write_sdk_init(tmp_path, "new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root, new_root, include=["openhands.sdk"]
    )
    assert total_breaks == 1
    # Bar was not deprecated before removal
    assert undeprecated == 1


def test_removal_of_deprecated_symbol_does_not_count_as_undeprecated(tmp_path):
    old_pkg = _write_sdk_init(tmp_path, "old", ["Foo", "Bar"])
    (old_pkg / "bar.py").write_text(
        "@deprecated(deprecated_in='1.0', removed_in='2.0')\nclass Bar:\n    pass\n"
    )
    _write_sdk_init(tmp_path, "new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root, new_root, include=["openhands.sdk"]
    )
    assert total_breaks == 1
    assert undeprecated == 0


def test_removal_with_warn_deprecated_is_not_undeprecated(tmp_path):
    old_pkg = _write_sdk_init(tmp_path, "old", ["Foo", "Bar"])
    (old_pkg / "bar.py").write_text(
        "class Bar:\n"
        "    @property\n"
        "    def value(self):\n"
        "        warn_deprecated('Bar.value', deprecated_in='1.0',"
        " removed_in='2.0')\n"
        "        return 42\n"
    )
    _write_sdk_init(tmp_path, "new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root, new_root, include=["openhands.sdk"]
    )
    assert total_breaks == 1
    assert undeprecated == 0


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


def test_find_deprecated_symbols_decorator(tmp_path):
    """@deprecated decorator on class/function is detected."""
    (tmp_path / "mod.py").write_text(
        "@deprecated(deprecated_in='1.0', removed_in='2.0')\n"
        "class Foo:\n"
        "    pass\n"
        "\n"
        "@deprecated(deprecated_in='1.0', removed_in='2.0')\n"
        "def bar():\n"
        "    pass\n"
        "\n"
        "class NotDeprecated:\n"
        "    pass\n"
    )
    result = _find_deprecated_symbols(tmp_path)
    assert result == {"Foo", "bar"}


def test_find_deprecated_symbols_warn_deprecated(tmp_path):
    """warn_deprecated() calls are detected; dotted names map to top-level."""
    (tmp_path / "mod.py").write_text(
        "warn_deprecated('Alpha', deprecated_in='1.0', removed_in='2.0')\n"
        "warn_deprecated('Beta.attr', deprecated_in='1.0', removed_in='2.0')\n"
    )
    result = _find_deprecated_symbols(tmp_path)
    assert result == {"Alpha", "Beta"}


def test_find_deprecated_symbols_ignores_syntax_errors(tmp_path):
    """Files with syntax errors are silently skipped."""
    (tmp_path / "bad.py").write_text("def broken(\n")
    (tmp_path / "good.py").write_text(
        "@deprecated(deprecated_in='1.0', removed_in='2.0')\ndef ok(): pass\n"
    )
    result = _find_deprecated_symbols(tmp_path)
    assert result == {"ok"}
