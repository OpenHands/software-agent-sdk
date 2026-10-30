"""Edge-case and failure-path tests for GlobExecutor across its backends."""

import unicodedata
from pathlib import Path

import pytest

import openhands.tools.glob.impl as glob_impl
from openhands.tools.glob import GlobAction
from openhands.tools.glob.impl import GlobExecutor
from openhands.tools.utils import _check_ripgrep_available


@pytest.fixture
def make_executor(monkeypatch):
    """Build a GlobExecutor pinned to ripgrep or the Python fallback."""

    def _make(backend: str, working_dir: Path) -> GlobExecutor:
        use_ripgrep = backend == "ripgrep"
        if use_ripgrep and not _check_ripgrep_available():
            pytest.skip("ripgrep not available")
        monkeypatch.setattr(glob_impl, "_check_ripgrep_available", lambda: use_ripgrep)
        return GlobExecutor(working_dir=str(working_dir))

    return _make


def _names(observation) -> set[str]:
    return {Path(f).name for f in observation.files}


@pytest.mark.parametrize("backend", ["ripgrep", "python"])
def test_path_that_is_a_file_is_rejected(make_executor, tmp_path, backend):
    file_path = tmp_path / "a.py"
    file_path.write_text("")
    executor = make_executor(backend, tmp_path)

    observation = executor(GlobAction(pattern="*.py", path=str(file_path)))

    assert observation.is_error is True
    assert "not a valid directory" in observation.text


@pytest.mark.parametrize("backend", ["ripgrep", "python"])
def test_missing_absolute_pattern_dir_is_rejected(make_executor, tmp_path, backend):
    executor = make_executor(backend, tmp_path)

    observation = executor(GlobAction(pattern=str(tmp_path / "missing" / "*.py")))

    assert observation.is_error is True
    assert observation.files == []


@pytest.mark.parametrize("backend", ["ripgrep", "python"])
def test_unicode_file_names(make_executor, tmp_path, backend):
    (tmp_path / "café.py").write_text("")
    (tmp_path / "数据.py").write_text("")
    (tmp_path / "other.txt").write_text("")
    executor = make_executor(backend, tmp_path)

    observation = executor(GlobAction(pattern="*.py"))

    assert observation.is_error is False
    # macOS file systems may hand back decomposed (NFD) names.
    found = {unicodedata.normalize("NFC", name) for name in _names(observation)}
    assert found == {"café.py", "数据.py"}


@pytest.mark.parametrize(
    "backend",
    [
        "ripgrep",
        pytest.param(
            "python",
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: Python glob fallback has no brace expansion, so "
                "'*.{json,yaml}' matches nothing",
            ),
        ),
    ],
)
def test_brace_pattern_from_tool_description(make_executor, tmp_path, backend):
    # The tool description advertises "**/*.{json,yaml,yml,toml}".
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.yaml").write_text("")
    (tmp_path / "c.py").write_text("")
    executor = make_executor(backend, tmp_path)

    observation = executor(GlobAction(pattern="**/*.{json,yaml}"))

    assert _names(observation) == {"a.json", "b.yaml"}


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(
            "ripgrep",
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: ripgrep backend reports truncated=True when exactly "
                "100 files match",
            ),
        ),
        "python",
    ],
)
def test_exactly_max_matches_is_not_truncated(make_executor, tmp_path, backend):
    for i in range(100):
        (tmp_path / f"f{i}.txt").write_text("")
    executor = make_executor(backend, tmp_path)

    observation = executor(GlobAction(pattern="*.txt"))

    assert len(observation.files) == 100
    assert observation.truncated is False
    assert "truncated" not in observation.text
