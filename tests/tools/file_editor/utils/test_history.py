"""Tests for file history management."""

import tempfile
from pathlib import Path

from openhands.tools.file_editor.utils.history import FileHistoryManager


def test_default_history_limit():
    """Test that default history limit is 5 entries."""
    with tempfile.NamedTemporaryFile() as temp_file:
        path = Path(temp_file.name)
        manager = FileHistoryManager()

        for i in range(6):
            manager.add_history(path, f"content{i}")

        assert manager.get_all_history(path) == [
            "content1",
            "content2",
            "content3",
            "content4",
            "content5",
        ]


def test_history_persists_across_manager_instances():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "test.txt"
        path.write_text("initial")

        manager1 = FileHistoryManager(history_dir=Path(temp_dir))
        manager1.add_history(path, "content1")
        manager1.add_history(path, "content2")

        manager2 = FileHistoryManager(history_dir=Path(temp_dir))
        manager2.add_history(path, "content3")

        assert manager2.get_all_history(path) == [
            "content1",
            "content2",
            "content3",
        ]


def test_clear_history_removes_entries():
    with tempfile.NamedTemporaryFile() as temp_file:
        path = Path(temp_file.name)
        manager = FileHistoryManager()
        manager.add_history(path, "content1")
        manager.add_history(path, "content2")

        manager.clear_history(path)

        assert manager.get_all_history(path) == []
        manager.add_history(path, "new_content")
        assert manager.get_all_history(path) == ["new_content"]


def test_pop_last_history_removes_entry():
    """Test that pop_last_history removes the latest entry."""
    with tempfile.NamedTemporaryFile() as temp_file:
        path = Path(temp_file.name)
        manager = FileHistoryManager()
        manager.add_history(path, "content1")
        manager.add_history(path, "content2")
        manager.add_history(path, "content3")

        assert manager.pop_last_history(path) == "content3"
        assert manager.get_all_history(path) == ["content1", "content2"]
        assert manager.pop_last_history(path) == "content2"
        assert manager.pop_last_history(path) == "content1"
        assert manager.pop_last_history(path) is None
