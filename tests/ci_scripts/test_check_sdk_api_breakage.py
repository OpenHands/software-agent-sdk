"""Tests for API breakage check script.

We import the production script via a file-based module load (rather than copying
functions) so tests remain coupled to real behavior.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import griffe


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / "check_sdk_api_breakage.py"
    name = "check_sdk_api_breakage"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register so @dataclass can resolve the module's __dict__
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
PackageConfig = _prod.PackageConfig
_parse_version = _prod._parse_version
_check_version_bump = _prod._check_version_bump
_find_deprecated_symbols = _prod._find_deprecated_symbols
_is_field_metadata_only_change = _prod._is_field_metadata_only_change

# Reusable test config matching the _write_pkg_init helper
_SDK_CFG = PackageConfig(
    package="openhands.sdk",
    distribution="openhands-sdk",
    source_dir="openhands-sdk",
)


def _write_pkg_init(
    tmp_path, root: str, all_names: list[str], module_parts: tuple[str, ...] = ()
):
    """Create a minimal package with ``__all__`` under *tmp_path/root*.

    *module_parts* defaults to ``("openhands", "sdk")``; pass a different
    tuple to create e.g. ``("openhands", "workspace")``.
    """
    parts = module_parts or ("openhands", "sdk")
    pkg = tmp_path / root / Path(*parts)
    pkg.mkdir(parents=True, exist_ok=True)
    # ensure parent __init__.py files exist
    for i in range(1, len(parts)):
        parent = tmp_path / root / Path(*parts[:i])
        init = parent / "__init__.py"
        if not init.exists():
            init.write_text("")
    (pkg / "__init__.py").write_text(
        "__all__ = [\n" + "\n".join(f"    {name!r}," for name in all_names) + "\n]\n"
    )
    return pkg


def test_griffe_breakage_removed_attribute_requires_minor_bump(tmp_path):
    old_pkg = _write_pkg_init(tmp_path, "old", ["TextContent"])
    new_pkg = _write_pkg_init(tmp_path, "new", ["TextContent"])

    old_init = old_pkg / "__init__.py"
    new_init = new_pkg / "__init__.py"

    old_init.write_text(
        old_init.read_text()
        + "\n\nclass TextContent:\n"
        + "    def __init__(self, text: str):\n"
        + "        self.text = text\n"
        + "        self.enable_truncation = True\n"
    )
    new_init.write_text(
        new_init.read_text()
        + "\n\nclass TextContent:\n"
        + "    def __init__(self, text: str):\n"
        + "        self.text = text\n"
    )

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, _undeprecated = _prod._compute_breakages(old_root, new_root, _SDK_CFG)
    assert total_breaks > 0

    assert _check_version_bump("1.11.3", "1.11.4", total_breaks=total_breaks) == 1
    assert _check_version_bump("1.11.3", "1.12.0", total_breaks=total_breaks) == 0


def test_griffe_removed_export_from_all_is_breaking(tmp_path):
    _write_pkg_init(tmp_path, "old", ["Foo", "Bar"])
    _write_pkg_init(tmp_path, "new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        _SDK_CFG,
    )
    assert total_breaks == 1
    # Bar was not deprecated before removal
    assert undeprecated == 1


def test_removal_of_deprecated_symbol_does_not_count_as_undeprecated(tmp_path):
    old_pkg = _write_pkg_init(tmp_path, "old", ["Foo", "Bar"])
    (old_pkg / "bar.py").write_text(
        "@deprecated(deprecated_in='1.0', removed_in='2.0')\nclass Bar:\n    pass\n"
    )
    _write_pkg_init(tmp_path, "new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        _SDK_CFG,
    )
    assert total_breaks == 1
    assert undeprecated == 0


def test_removal_with_warn_deprecated_is_not_undeprecated(tmp_path):
    old_pkg = _write_pkg_init(tmp_path, "old", ["Foo", "Bar"])
    (old_pkg / "bar.py").write_text(
        "class Bar:\n"
        "    @property\n"
        "    def value(self):\n"
        "        warn_deprecated('Bar.value', deprecated_in='1.0',"
        " removed_in='2.0')\n"
        "        return 42\n"
    )
    _write_pkg_init(tmp_path, "new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        _SDK_CFG,
    )
    assert total_breaks == 1
    assert undeprecated == 0


def test_removed_public_method_requires_deprecation(tmp_path):
    old_pkg = _write_pkg_init(tmp_path, "old", ["Foo"])
    new_pkg = _write_pkg_init(tmp_path, "new", ["Foo"])

    old_init = old_pkg / "__init__.py"
    new_init = new_pkg / "__init__.py"

    old_init.write_text(
        old_init.read_text()
        + "\n\nclass Foo:\n"
        + "    def bar(self) -> int:\n"
        + "        return 1\n"
    )
    new_init.write_text(new_init.read_text() + "\n\nclass Foo:\n    pass\n")

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        _SDK_CFG,
    )
    assert total_breaks > 0
    assert undeprecated == 1


def test_removed_public_method_with_deprecation_is_not_undeprecated(tmp_path):
    old_pkg = _write_pkg_init(tmp_path, "old", ["Foo"])
    new_pkg = _write_pkg_init(tmp_path, "new", ["Foo"])

    old_init = old_pkg / "__init__.py"
    new_init = new_pkg / "__init__.py"

    old_init.write_text(
        old_init.read_text()
        + "\n\nclass Foo:\n"
        + "    @deprecated(deprecated_in='1.0', removed_in='2.0')\n"
        + "    def bar(self) -> int:\n"
        + "        return 1\n"
    )
    new_init.write_text(new_init.read_text() + "\n\nclass Foo:\n    pass\n")

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        _SDK_CFG,
    )
    assert total_breaks > 0
    assert undeprecated == 0


def test_missing_all_in_previous_release_skips_breakage_check(tmp_path):
    """If previous release lacks __all__, skip instead of failing workflow."""
    old_pkg = tmp_path / "old" / "openhands" / "sdk"
    old_pkg.mkdir(parents=True)
    (tmp_path / "old" / "openhands" / "__init__.py").write_text("")
    (old_pkg / "__init__.py").write_text("# no __all__ in previous release\n")

    _write_pkg_init(tmp_path, "new", ["Foo"])

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(old_root, new_root, _SDK_CFG)
    assert total_breaks == 0
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
    assert result.top_level == {"Foo", "bar"}
    assert result.qualified == {"Foo", "bar"}


def test_find_deprecated_symbols_warn_deprecated(tmp_path):
    """warn_deprecated() calls are detected; dotted names map to top-level."""
    (tmp_path / "mod.py").write_text(
        "warn_deprecated('Alpha', deprecated_in='1.0', removed_in='2.0')\n"
        "warn_deprecated('Beta.attr', deprecated_in='1.0', removed_in='2.0')\n"
    )
    result = _find_deprecated_symbols(tmp_path)
    assert result.top_level == {"Alpha", "Beta"}
    assert result.qualified == {"Alpha", "Beta.attr"}


def test_find_deprecated_symbols_ignores_syntax_errors(tmp_path):
    """Files with syntax errors are silently skipped."""
    (tmp_path / "bad.py").write_text("def broken(\n")
    (tmp_path / "good.py").write_text(
        "@deprecated(deprecated_in='1.0', removed_in='2.0')\ndef ok(): pass\n"
    )
    result = _find_deprecated_symbols(tmp_path)
    assert result.top_level == {"ok"}
    assert result.qualified == {"ok"}


def test_workspace_removed_export_is_breaking(tmp_path):
    """Breakage detection works for non-SDK packages (openhands.workspace)."""
    ws_cfg = PackageConfig(
        package="openhands.workspace",
        distribution="openhands-workspace",
        source_dir="openhands-workspace",
    )
    _write_pkg_init(
        tmp_path, "old", ["Foo", "Bar"], module_parts=("openhands", "workspace")
    )
    _write_pkg_init(tmp_path, "new", ["Foo"], module_parts=("openhands", "workspace"))

    old_root = griffe.load("openhands.workspace", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.workspace", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        ws_cfg,
    )
    assert total_breaks == 1
    assert undeprecated == 1


def test_unresolved_alias_exports_do_not_crash_breakage_detection(tmp_path):
    """Unresolvable aliases should not abort checking other exports.

    This mirrors a real-world scenario for packages that re-export SDK symbols.
    """

    ws_cfg = PackageConfig(
        package="openhands.workspace",
        distribution="openhands-workspace",
        source_dir="openhands-workspace",
    )

    def _write_workspace(root: str, *, include_method: bool) -> None:
        pkg = tmp_path / root / "openhands" / "workspace"
        pkg.mkdir(parents=True)
        (tmp_path / root / "openhands" / "__init__.py").write_text("")

        content = (
            "from openhands.sdk.workspace import PlatformType\n\n"
            "__all__ = [\n"
            "    'PlatformType',\n"
            "    'Foo',\n"
            "]\n\n"
            "class Foo:\n"
        )
        if include_method:
            content += "    def bar(self) -> int:\n        return 1\n"
        else:
            content += "    pass\n"

        (pkg / "__init__.py").write_text(content)

    _write_workspace("old", include_method=True)
    _write_workspace("new", include_method=False)

    old_root = griffe.load("openhands.workspace", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.workspace", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        ws_cfg,
    )

    assert total_breaks >= 1
    assert undeprecated == 1


def test_is_field_metadata_only_change_description_only():
    """Changing only Field description is detected as metadata-only."""
    old = "Field(default=False, description='old description')"
    new = "Field(default=False, description='new description')"
    assert _is_field_metadata_only_change(old, new) is True


def test_is_field_metadata_only_change_title_and_description():
    """Changing title and description is detected as metadata-only."""
    old = "Field(default=False, title='old', description='old desc')"
    new = "Field(default=False, title='new', description='new desc')"
    assert _is_field_metadata_only_change(old, new) is True


def test_is_field_metadata_only_change_default_changed():
    """Changing Field default value is NOT metadata-only."""
    old = "Field(default=False, description='desc')"
    new = "Field(default=True, description='desc')"
    assert _is_field_metadata_only_change(old, new) is False


def test_is_field_metadata_only_change_not_field():
    """Non-Field values return False."""
    old = "SomeClass(value=1)"
    new = "SomeClass(value=2)"
    assert _is_field_metadata_only_change(old, new) is False


def test_is_field_metadata_only_change_long_description():
    """Long descriptions with URLs are handled correctly."""
    old = (
        "Field(default=False, description='Whether to automatically load "
        "skills from https://github.com/OpenHands/skills.')"
    )
    new = (
        "Field(default=False, description='Whether to automatically load "
        "skills from https://github.com/OpenHands/extensions.')"
    )
    assert _is_field_metadata_only_change(old, new) is True


def test_field_description_change_is_not_breaking(tmp_path):
    """Field description changes should not be counted as breaking changes."""
    old_pkg = _write_pkg_init(tmp_path, "old", ["Config"])
    new_pkg = _write_pkg_init(tmp_path, "new", ["Config"])

    old_init = old_pkg / "__init__.py"
    new_init = new_pkg / "__init__.py"

    old_init.write_text(
        old_init.read_text()
        + "\nfrom pydantic import BaseModel, Field\n\n"
        + "class Config(BaseModel):\n"
        + "    enabled: bool = Field(default=False, description='Old description')\n"
    )
    new_init.write_text(
        new_init.read_text()
        + "\nfrom pydantic import BaseModel, Field\n\n"
        + "class Config(BaseModel):\n"
        + "    enabled: bool = Field(default=False, description='New description')\n"
    )

    old_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "old")])
    new_root = griffe.load("openhands.sdk", search_paths=[str(tmp_path / "new")])

    total_breaks, undeprecated = _prod._compute_breakages(
        old_root,
        new_root,
        _SDK_CFG,
    )
    # Field description changes should NOT count as breaking
    assert total_breaks == 0
    assert undeprecated == 0
