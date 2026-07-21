"""Unit tests for the two-tier persistent-memory loader (``context/memory.py``)."""

from pathlib import Path

import pytest

from openhands.sdk.context.memory import MEMORY_INDEX_RELPATH, load_memory


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the user memory tier (``~/.openhands/memory/``) at a temp home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
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


def test_load_memory_truncates_from_top_keeping_recent_tail(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write_index(workspace, "OLD\n" + "x" * 100 + "\nNEW")

    text = load_memory(workspace, char_budget=20)

    assert text is not None
    assert text.startswith("[earlier memory truncated]")
    assert text.endswith("NEW")
    assert "OLD" not in text


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
