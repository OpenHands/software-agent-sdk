"""Tests for LocalFileStore path traversal security."""

import os
import tempfile
import threading

import pytest

from openhands.sdk.io.local import LocalFileStore


def test_path_traversal_attacks_blocked():
    """Test that various path traversal attacks are properly blocked."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = os.path.join(temp_dir, "filestore_root")
        store = LocalFileStore(root_dir)

        # Create a sensitive file outside the root
        sensitive_file = os.path.join(temp_dir, "sensitive.txt")
        with open(sensitive_file, "w") as f:
            f.write("SENSITIVE DATA")

        # Test various path traversal attack vectors
        attack_vectors = [
            "../sensitive.txt",
            "../../sensitive.txt",
            "../../../etc/passwd",
            "subdir/../../../sensitive.txt",
            "..\\sensitive.txt",  # Windows-style
            "subdir/../../sensitive.txt",
            "./../sensitive.txt",
            "a/../../../sensitive.txt",
        ]

        for attack_path in attack_vectors:
            with pytest.raises(ValueError, match="path escapes filestore root"):
                store.get_full_path(attack_path)


def test_legitimate_paths_allowed():
    """Test that legitimate paths within the root are allowed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = os.path.join(temp_dir, "filestore_root")
        store = LocalFileStore(root_dir)

        legitimate_paths = [
            "file.txt",
            "subdir/file.txt",
            "deep/nested/path/file.txt",
            "file_with_dots.txt",
            ".hidden_file",
            "subdir/.hidden",
        ]

        for legit_path in legitimate_paths:
            full_path = store.get_full_path(legit_path)
            # Verify the path is within the root
            assert full_path.startswith(root_dir)
            assert os.path.commonpath([root_dir, full_path]) == root_dir


def test_edge_cases():
    """Test edge cases like empty paths and root paths."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = os.path.join(temp_dir, "filestore_root")
        store = LocalFileStore(root_dir)

        # Test empty path
        full_path = store.get_full_path("")
        assert full_path == root_dir

        # Test root path
        full_path = store.get_full_path("/")
        assert full_path == root_dir

        # Test current directory
        full_path = store.get_full_path(".")
        assert full_path == root_dir


def test_root_normalization():
    """Test that the root path is properly normalized during initialization."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Test with tilde expansion
        if os.path.expanduser("~") != "~":
            store = LocalFileStore("~/test_root")
            assert not store.root.startswith("~")

        # Test with relative path
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            store = LocalFileStore("./relative_root")
            assert os.path.isabs(store.root)

            # Prevent test error in some mac environments
            if store.root.startswith("/private/") and not temp_dir.startswith(
                "/private/"
            ):
                temp_dir = f"/private{temp_dir}"

            assert store.root.startswith(temp_dir)
        finally:
            os.chdir(original_cwd)


def test_file_operations_with_security():
    """Test that file operations work correctly with the security fix."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = os.path.join(temp_dir, "filestore_root")
        store = LocalFileStore(root_dir)

        # Test writing and reading a legitimate file
        test_content = "Hello, World!"
        store.write("test.txt", test_content)
        assert store.read("test.txt") == test_content

        # Test that we can't write outside the root
        with pytest.raises(ValueError, match="path escapes filestore root"):
            store.write("../outside.txt", "malicious content")

        # Test that we can't read outside the root
        with pytest.raises(ValueError, match="path escapes filestore root"):
            store.read("../outside.txt")


def test_windows_style_traversal_in_nested_path_blocked(tmp_path):
    store = LocalFileStore(str(tmp_path / "root"))

    with pytest.raises(ValueError, match="path escapes filestore root"):
        store.get_full_path("sub\\..\\..\\outside.txt")


def test_sibling_directory_sharing_root_prefix_blocked(tmp_path):
    """``root2`` shares ``root``'s string prefix but is outside it."""
    store = LocalFileStore(str(tmp_path / "root"))

    with pytest.raises(ValueError, match="path escapes filestore root"):
        store.write("../root2/file.txt", "x")
    assert not (tmp_path / "root2").exists()


def test_list_and_exists_on_missing_path(tmp_path):
    store = LocalFileStore(str(tmp_path / "root"))

    assert store.list("missing") == []
    assert store.exists("missing") is False


def test_list_file_returns_the_file_itself(tmp_path):
    store = LocalFileStore(str(tmp_path / "root"))
    store.write("dir/file.txt", "x")

    assert store.list("dir/file.txt") == ["dir/file.txt"]
    assert store.list("dir") == ["dir/file.txt"]


def test_delete_missing_path_is_a_no_op(tmp_path):
    store = LocalFileStore(str(tmp_path / "root"))

    store.delete("missing/file.txt")

    assert store.exists("") is True


def test_lock_times_out_while_another_thread_holds_it(tmp_path):
    store = LocalFileStore(str(tmp_path / "root"))
    held = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with store.lock("state.lock"):
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    try:
        assert held.wait(5)
        with pytest.raises(TimeoutError, match="state.lock"):
            with store.lock("state.lock", timeout=0.05):
                pass
    finally:
        release.set()
        holder.join(5)

    with store.lock("state.lock", timeout=1):
        pass
