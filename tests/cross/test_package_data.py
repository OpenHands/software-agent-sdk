"""Runtime data files must be declared as package data.

``openhands-tools`` ships helper assets next to the code that loads them (the
browser recording JS, the subagent prompts). Those only reach an installed
wheel if a ``[tool.setuptools.package-data]`` glob covers them, and nothing
source checkout reveals an omission: an editable install reads them straight
from the working tree and passes either way. The gap only appears once the
wheel is built, which is how it reached a release (issue #4443).
"""

import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_PROJECT = REPO_ROOT / "openhands-tools"
TOOLS_PACKAGE = TOOLS_PROJECT / "openhands" / "tools"

# Contributor docs that sit next to the code and are never opened at runtime.
DEV_ONLY_FILENAMES = frozenset({"AGENTS.md", "README.md"})


def _package_data_patterns() -> dict[str, list[str]]:
    pyproject = tomllib.loads(
        (TOOLS_PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return pyproject["tool"]["setuptools"]["package-data"]


def _is_declared(path: Path) -> bool:
    """Whether any package-data glob ships ``path``.

    A dotted key names a directory relative to the project root, which need not
    be an importable package: ``openhands.tools.preset.subagents`` has no
    ``__init__.py`` and still ships. ``*`` applies to every directory on the way
    down, so its patterns are matched against each one. Patterns are expanded
    with ``Path.glob`` because that is what setuptools itself does, so ``*``
    stops at a directory boundary and only ``**`` descends.
    """
    for key, patterns in _package_data_patterns().items():
        if key == "*":
            bases = [
                parent
                for parent in path.parents
                if parent.is_relative_to(TOOLS_PROJECT)
            ]
        else:
            base = TOOLS_PROJECT.joinpath(*key.split("."))
            if not path.is_relative_to(base):
                continue
            bases = [base]

        for base in bases:
            if any(path in base.glob(pattern) for pattern in patterns):
                return True
    return False


def _runtime_data_files() -> list[Path]:
    return sorted(
        path
        for path in TOOLS_PACKAGE.rglob("*")
        if path.is_file()
        and path.suffix != ".py"
        and "__pycache__" not in path.parts
        and path.name not in DEV_ONLY_FILENAMES
    )


def test_runtime_data_files_are_declared_as_package_data():
    undeclared = [
        path.relative_to(TOOLS_PROJECT).as_posix()
        for path in _runtime_data_files()
        if not _is_declared(path)
    ]

    assert not undeclared, (
        "these files are read at runtime but no package-data glob ships them, "
        "so they are missing from the built wheel: " + ", ".join(undeclared)
    )


def test_browser_recording_js_helpers_are_declared():
    """The helpers ``browser_use/recording.py`` loads by name."""
    helpers = sorted((TOOLS_PACKAGE / "browser_use" / "js").glob("*.js"))

    assert helpers, "expected browser_use/js to hold the recording helpers"
    for helper in helpers:
        assert _is_declared(helper), helper.name


def test_a_nested_file_is_not_declared_by_a_single_star_pattern(tmp_path, monkeypatch):
    """``js/*.js`` covers the directory it names, not the ones below it."""
    project = tmp_path / "openhands-tools"
    js_dir = project / "openhands" / "tools" / "browser_use" / "js"
    (js_dir / "nested").mkdir(parents=True)
    (js_dir / "recording.js").touch()
    (js_dir / "nested" / "recording.js").touch()
    (project / "pyproject.toml").write_text(
        '[tool.setuptools.package-data]\n"openhands.tools.browser_use" = ["js/*.js"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "TOOLS_PROJECT", project)

    assert _is_declared(js_dir / "recording.js")
    assert not _is_declared(js_dir / "nested" / "recording.js")
