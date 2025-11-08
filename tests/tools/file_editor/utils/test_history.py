"""Tests for file history management."""

import tempfile
from pathlib import Path

from openhands.tools.file_editor.utils.history import (
    FileHistoryManager,
)


def test_default_history_limit():
    """Test that default history limit is 5 entries."""
    with tempfile.NamedTemporaryFile() as temp_file:
        path = Path(temp_file.name)
        manager = FileHistoryManager()

        # Add 6 entries - this should trigger removal of the first entry
        for i in range(6):
            manager.add_history(path, f"content{i}")

        # Get the metadata
        metadata = manager.get_metadata(path)
        assert len(metadata["entries"]) == 5  # Should only keep last 5 entries
        # First entry should be content1, last should be content5
        assert manager.get_all_history(path)[0].startswith("content1")
        assert manager.get_all_history(path)[-1].startswith("content5")


def test_history_keys_are_unique():
    """Test that history keys remain unique even after removing old entries."""
    with tempfile.NamedTemporaryFile() as temp_file:
        path = Path(temp_file.name)
        manager = FileHistoryManager(max_history_per_file=2)

        # Add 3 entries - this should trigger removal of the first entry
        manager.add_history(path, "content1")
        manager.add_history(path, "content2")
        manager.add_history(path, "content3")

        # Get the metadata
        metadata = manager.get_metadata(path)
        assert len(metadata["entries"]) == 2  # Should only keep last 2 entries

        # Keys should be unique and sequential
        keys = metadata["entries"]
        assert len(set(keys)) == len(keys)  # All keys should be unique
        assert sorted(keys) == keys  # Keys should be sequential

        # Add another entry
        manager.add_history(path, "content4")
        new_metadata = manager.get_metadata(path)
        new_keys = new_metadata["entries"]

        # New key should be greater than all previous keys
        assert min(new_keys) > min(keys)
        assert len(set(new_keys)) == len(new_keys)  # All keys should still be unique


def test_history_counter_persists():
    """Test that history counter persists across manager instances."""
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "test.txt"
        path.write_text("initial")

        # First manager instance
        manager1 = FileHistoryManager(history_dir=Path(temp_dir))
        manager1.add_history(path, "content1")
        manager1.add_history(path, "content2")

        # Second manager instance using same directory
        manager2 = FileHistoryManager(history_dir=Path(temp_dir))
        manager2.add_history(path, "content3")

        # Get metadata
        metadata = manager2.get_metadata(path)
        keys = metadata["entries"]

        # Keys should be sequential even across instances
        assert len(set(keys)) == len(keys)  # All keys should be unique
        assert sorted(keys) == keys  # Keys should be sequential


def test_clear_history_resets_counter():
    """Test that clearing history resets the counter."""
    with tempfile.NamedTemporaryFile() as temp_file:
        path = Path(temp_file.name)
        manager = FileHistoryManager()

        # Add some entries
        manager.add_history(path, "content1")
        manager.add_history(path, "content2")

        # Clear history
        manager.clear_history(path)

        # Counter should be reset
        metadata = manager.get_metadata(path)
        assert metadata["counter"] == 0

        # Adding new entries should start from 0
        manager.add_history(path, "new_content")
        metadata = manager.get_metadata(path)
        assert len(metadata["entries"]) == 1
        assert metadata["entries"][0] == 0  # First key should be 0


def test_pop_last_history_removes_entry():
    """Test that pop_last_history removes the latest entry."""
    with tempfile.NamedTemporaryFile() as temp_file:
        path = Path(temp_file.name)
        manager = FileHistoryManager()

        # Add some entries
        manager.add_history(path, "content1")
        manager.add_history(path, "content2")
        manager.add_history(path, "content3")

        # Pop the last history entry
        last_entry = manager.pop_last_history(path)
        assert last_entry == "content3"

        # Check that the entry has been removed
        metadata = manager.get_metadata(path)
        assert len(metadata["entries"]) == 2

        # Pop the last history entry again
        last_entry = manager.pop_last_history(path)
        assert last_entry == "content2"

        # Check that the entry has been removed
        metadata = manager.get_metadata(path)
        assert len(metadata["entries"]) == 1

        # Pop the last history entry one more time
        last_entry = manager.pop_last_history(path)
        assert last_entry == "content1"

        # Check that all entries have been removed
        metadata = manager.get_metadata(path)
        assert len(metadata["entries"]) == 0

        # Try to pop last history when there are no entries
        last_entry = manager.pop_last_history(path)
        assert last_entry is None
