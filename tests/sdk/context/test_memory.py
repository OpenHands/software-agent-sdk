"""Unit tests for the two-tier persistent-memory loader (``context/memory.py``)."""

from pathlib import Path

import pytest

from openhands.sdk.context.memory import MEMORY_INDEX_RELPATH, load_memory


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the user memory tier (``~/.openhands/memory/``) at a temp home.

    USERPROFILE is what ``Path.home()`` reads on Windows, where HOME is a no-op.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _write_index(root: Path, text: str) -> None:
    index = root / MEMORY_INDEX_RELPATH
    index.parent.mkdir(parents=True)
    index.write_text(text)


def test_load_memory_returns_none_without_index_files(tmp_path: Path) -> None:
    assert load_memory(tmp_path / "workspace") is None


def test_load_memory_reads_project_index(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_index(workspace, "- run tests with `uv run pytest`\n")

    assert load_memory(workspace) == (
        "# Project memory (.openhands/memory/MEMORY.md)\n"
        "- run tests with `uv run pytest`"
    )


def test_load_memory_reads_user_index(isolated_home: Path, tmp_path: Path) -> None:
    _write_index(isolated_home, "- prefers uv over pip\n")

    assert load_memory(tmp_path / "workspace") == (
        "# User memory (~/.openhands/memory/MEMORY.md)\n- prefers uv over pip"
    )


def test_load_memory_orders_user_before_project(
    isolated_home: Path, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    _write_index(isolated_home, "user fact")
    _write_index(workspace, "project fact")

    text = load_memory(workspace)

    assert text is not None
    assert text.index("user fact") < text.index("project fact")


def test_load_memory_truncates_whole_lines_from_top_of_tier(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_index(workspace, "OLD\n" + ("x" * 40 + "\n") * 4 + "NEW")

    text = load_memory(workspace, char_budget=160)

    assert text is not None
    lines = text.splitlines()
    assert lines[0] == "# Project memory (.openhands/memory/MEMORY.md)"
    assert lines[1] == "[earlier memory truncated]"
    assert text.endswith("NEW")
    assert "OLD" not in text
    assert len(text) <= 160


def test_load_memory_truncation_keeps_both_tier_headers(
    isolated_home: Path, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    _write_index(isolated_home, "- prefers tabs")
    _write_index(workspace, "OLD-PROJECT\n" + ("y" * 40 + "\n") * 5 + "NEW-PROJECT")

    text = load_memory(workspace, char_budget=260)

    assert text is not None
    lines = text.splitlines()
    assert "# User memory (~/.openhands/memory/MEMORY.md)" in lines
    assert "# Project memory (.openhands/memory/MEMORY.md)" in lines
    # The short user tier fits its share, so its content survives untouched.
    assert "- prefers tabs" in lines
    assert text.endswith("NEW-PROJECT")
    assert "OLD-PROJECT" not in text
    assert lines.count("[earlier memory truncated]") == 1
    assert len(text) <= 260


def test_load_memory_truncation_drops_no_partial_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    original_lines = [f"memory fact number {i:02d} padded {'z' * i}" for i in range(12)]
    _write_index(workspace, "\n".join(original_lines))

    text = load_memory(workspace, char_budget=180)

    assert text is not None
    assert "[earlier memory truncated]" in text
    allowed = {
        "# Project memory (.openhands/memory/MEMORY.md)",
        "[earlier memory truncated]",
        *original_lines,
    }
    assert set(text.splitlines()) <= allowed
    assert len(text) <= 180


def test_load_memory_treats_empty_index_as_absent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_index(workspace, "   \n\n")

    assert load_memory(workspace) is None


def test_load_memory_treats_unreadable_index_as_absent(
    isolated_home: Path, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    # A directory where the index file should be makes read_text raise OSError.
    (workspace / MEMORY_INDEX_RELPATH).mkdir(parents=True)
    _write_index(isolated_home, "user fact")

    text = load_memory(workspace)

    assert text is not None
    assert "user fact" in text
    assert "Project memory" not in text
