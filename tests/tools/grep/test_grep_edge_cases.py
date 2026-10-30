"""Edge-case and failure-path tests for GrepExecutor across its backends."""

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
def test_search_path_that_is_a_file_is_rejected(make_executor, tmp_path, backend):
    file_path = tmp_path / "a.txt"
    file_path.write_text("needle\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="needle", path=str(file_path)))

    assert observation.is_error is True
    assert "not a valid directory" in observation.text


@pytest.mark.parametrize("backend", ["ripgrep", "grep", "python"])
def test_unicode_pattern_matches_unicode_content(make_executor, tmp_path, backend):
    (tmp_path / "uni.txt").write_text("le café 😀\n", encoding="utf-8")
    (tmp_path / "plain.txt").write_text("cafe\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="café"))

    assert observation.is_error is False
    assert _names(observation) == {"uni.txt"}


@pytest.mark.parametrize("backend", ["ripgrep", "grep", "python"])
def test_pattern_with_shell_metacharacters_is_not_interpreted(
    make_executor, tmp_path, backend
):
    marker = tmp_path / "pwned"
    (tmp_path / "a.txt").write_text("x\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern=f"x; touch {marker} $(touch {marker})"))

    assert observation.is_error is False
    assert observation.matches == []
    assert not marker.exists()


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(
            backend,
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: brace include like '*.{ts,tsx}' is filtered with "
                "fnmatch, which has no brace expansion",
            ),
        )
        for backend in ("ripgrep", "grep", "python")
    ],
)
def test_brace_include_pattern_from_tool_description(make_executor, tmp_path, backend):
    # The tool description advertises include="*.{ts,tsx}".
    (tmp_path / "a.ts").write_text("needle\n")
    (tmp_path / "b.tsx").write_text("needle\n")
    (tmp_path / "c.py").write_text("needle\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="needle", include="*.{ts,tsx}"))

    assert _names(observation) == {"a.ts", "b.tsx"}


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(
            "ripgrep",
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: ripgrep backend passes a leading-dash pattern as a "
                "CLI flag",
            ),
        ),
        "grep",
        "python",
    ],
)
def test_pattern_starting_with_dash(make_executor, tmp_path, backend):
    (tmp_path / "flags.txt").write_text("run with -foo enabled\n")
    (tmp_path / "other.txt").write_text("nothing here\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="-foo"))

    assert observation.is_error is False
    assert _names(observation) == {"flags.txt"}


@pytest.mark.xfail(
    strict=True,
    reason="BUG: ripgrep backend treats '--files' pattern as a flag and lists "
    "every file",
)
def test_ripgrep_pattern_cannot_inject_flags(make_executor, tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    (tmp_path / "b.txt").write_text("beta\n")
    executor = make_executor("ripgrep", tmp_path)

    observation = executor(GrepAction(pattern="--files"))

    assert observation.matches == []


@pytest.mark.xfail(
    strict=True,
    reason="BUG: ripgrep regex errors (exit 2) are reported as 'No files found'",
)
def test_ripgrep_unsupported_regex_is_not_silently_empty(make_executor, tmp_path):
    # Python's re accepts look-ahead, so the pre-check passes, but ripgrep's
    # default engine rejects it. The result must not be a silent "no match".
    (tmp_path / "a.txt").write_text("foobar\n")
    executor = make_executor("ripgrep", tmp_path)

    observation = executor(GrepAction(pattern="foo(?=bar)"))

    assert observation.is_error or _names(observation) == {"a.txt"}


@pytest.mark.parametrize(
    "backend",
    [
        "ripgrep",
        pytest.param(
            "grep",
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: system grep backend runs basic regex (no -E), so "
                "'|' alternation is literal",
            ),
        ),
        "python",
    ],
)
def test_alternation_pattern(make_executor, tmp_path, backend):
    (tmp_path / "a.txt").write_text("needle\n")
    (tmp_path / "b.txt").write_text("haystack\n")
    (tmp_path / "c.txt").write_text("other\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="needle|haystack"))

    assert _names(observation) == {"a.txt", "b.txt"}


@pytest.mark.parametrize("backend", ["ripgrep", "grep", "python"])
def test_exactly_max_matches_is_not_truncated(make_executor, tmp_path, backend):
    for i in range(100):
        (tmp_path / f"f{i}.txt").write_text("needle\n")
    executor = make_executor(backend, tmp_path)

    observation = executor(GrepAction(pattern="needle"))

    assert len(observation.matches) == 100
    assert observation.truncated is False
