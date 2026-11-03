"""Edge-case behaviour of GrepExecutor across its backends."""

from pathlib import Path

import pytest

import openhands.tools.grep.impl as grep_impl
from openhands.tools.grep import GrepAction
from openhands.tools.grep.impl import GrepExecutor
from openhands.tools.utils import _check_grep_available, _check_ripgrep_available


_BACKEND_AVAILABLE = {
    "ripgrep": _check_ripgrep_available(),
    "grep": _check_grep_available(),
    "python": True,
}


@pytest.fixture
def make_executor(monkeypatch):
    """Build a GrepExecutor pinned to one backend, skipping if it is missing."""

    def _make(backend: str, working_dir: Path) -> GrepExecutor:
        if not _BACKEND_AVAILABLE[backend]:
            pytest.skip(f"{backend} backend not available")
        monkeypatch.setattr(
            grep_impl, "_check_ripgrep_available", lambda: backend == "ripgrep"
        )
        monkeypatch.setattr(
            grep_impl, "_check_grep_available", lambda: backend == "grep"
        )
        return GrepExecutor(working_dir=str(working_dir))

    return _make


def _names(observation) -> set[str]:
    return {Path(match).name for match in observation.matches}


@pytest.mark.parametrize("backend", ["ripgrep", "grep", "python"])
def test_alternation_pattern(make_executor, tmp_path, backend):
    (tmp_path / "a.txt").write_text("needle\n")
    (tmp_path / "b.txt").write_text("haystack\n")
    (tmp_path / "c.txt").write_text("other\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="needle|haystack"))

    assert _names(observation) == {"a.txt", "b.txt"}


@pytest.mark.parametrize("backend", ["ripgrep", "grep", "python"])
def test_pattern_starting_with_dash(make_executor, tmp_path, backend):
    (tmp_path / "flags.txt").write_text("run with -foo enabled\n")
    (tmp_path / "other.txt").write_text("nothing here\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="-foo"))

    assert observation.is_error is False
    assert _names(observation) == {"flags.txt"}


def test_ripgrep_pattern_cannot_inject_flags(make_executor, tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    (tmp_path / "b.txt").write_text("beta\n")
    executor = make_executor("ripgrep", tmp_path)

    observation = executor(GrepAction(pattern="--files"))

    assert observation.matches == []


@pytest.mark.parametrize("backend", ["ripgrep", "grep", "python"])
def test_brace_include_pattern(make_executor, tmp_path, backend):
    # The tool description advertises include="*.{ts,tsx}".
    (tmp_path / "a.ts").write_text("needle\n")
    (tmp_path / "b.tsx").write_text("needle\n")
    (tmp_path / "c.py").write_text("needle\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="needle", include="*.{ts,tsx}"))

    assert _names(observation) == {"a.ts", "b.tsx"}
