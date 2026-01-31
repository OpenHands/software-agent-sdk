import os
import tempfile

import pytest

from openhands.tools.file_editor.utils.file_cache import FileCache


@pytest.fixture
def file_cache():
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = FileCache(temp_dir)
        yield cache
        cache.clear()


def test_init(file_cache):
    assert isinstance(file_cache, FileCache)
    assert file_cache.directory.exists()
    assert file_cache.directory.is_dir()


def test_set_and_get(file_cache):
    file_cache.set("test_key", "test_value")
    assert file_cache.get("test_key") == "test_value"


def test_get_nonexistent_key(file_cache):
    assert file_cache.get("nonexistent_key") is None
    assert file_cache.get("nonexistent_key", "default") == "default"


def test_set_nested_key(file_cache):
    file_cache.set("folder/nested/key", "nested_value")
    assert file_cache.get("folder/nested/key") == "nested_value"


def test_set_overwrite(file_cache):
    file_cache.set("test_key", "initial_value")
    file_cache.set("test_key", "new_value")
    assert file_cache.get("test_key") == "new_value"


def test_delete(file_cache):
    file_cache.set("test_key", "test_value")
    file_cache.delete("test_key")
    assert file_cache.get("test_key") is None


def test_delete_nonexistent_key(file_cache):
    file_cache.delete("nonexistent_key")  # Should not raise an exception


def test_delete_nested_key(file_cache):
    file_cache.set("folder/nested/key", "nested_value")
    file_cache.delete("folder/nested/key")
    assert file_cache.get("folder/nested/key") is None


def test_clear(file_cache):
    file_cache.set("key1", "value1")
    file_cache.set("key2", "value2")
    file_cache.set("folder/key3", "value3")
    file_cache.clear()
    assert len(file_cache) == 0
    assert file_cache.get("key1") is None
    assert file_cache.get("key2") is None
    assert file_cache.get("folder/key3") is None


def test_contains(file_cache):
    file_cache.set("test_key", "test_value")
    assert "test_key" in file_cache
    assert "nonexistent_key" not in file_cache


def test_len(file_cache):
    assert len(file_cache) == 0
    file_cache.set("key1", "value1")
    file_cache.set("key2", "value2")
    assert len(file_cache) == 2
    file_cache.set("folder/key3", "value3")
    assert len(file_cache) == 3


def test_iter(file_cache):
    file_cache.set("key1", "value1")
    file_cache.set("key2", "value2")
    file_cache.set("folder/key3", "value3")
    keys = set(file_cache)
    assert keys == {"key1", "key2", "folder/key3"}


@pytest.mark.skipif(
    os.environ.get("CI", "false").lower() == "true",
    reason="Skip large value test on CI since it will break due to memory limits",
)
def test_large_value(file_cache):
    large_value = "x" * 1024 * 1024  # 1 MB string
    file_cache.set("large_key", large_value)
    assert file_cache.get("large_key") == large_value


def test_many_items(file_cache):
    for i in range(1000):
        file_cache.set(f"key_{i}", f"value_{i}")

    assert len(file_cache) == 1000
    for i in range(1000):
        assert file_cache.get(f"key_{i}") == f"value_{i}"


def test_nested_structure(file_cache):
    file_cache.set("folder1/file1", "content1")
    file_cache.set("folder1/file2", "content2")
    file_cache.set("folder2/subfolder/file3", "content3")

    assert file_cache.get("folder1/file1") == "content1"
    assert file_cache.get("folder1/file2") == "content2"
    assert file_cache.get("folder2/subfolder/file3") == "content3"
    assert len(file_cache) == 3


def test_clear_nested_structure(file_cache):
    file_cache.set("folder1/file1", "content1")
    file_cache.set("folder1/file2", "content2")
    file_cache.set("folder2/subfolder/file3", "content3")
    file_cache.clear()

    assert len(file_cache) == 0
    assert list(file_cache) == []
    assert not any(file_cache.directory.iterdir())


def test_delete_removes_empty_directories(file_cache):
    file_cache.set("folder1/subfolder/file1", "content1")
    file_cache.delete("folder1/subfolder/file1")

    assert not (file_cache.directory / "folder1" / "subfolder").exists()
    assert not (file_cache.directory / "folder1").exists()


def test_size_limit():
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = FileCache(temp_dir, size_limit=100)
        val1 = "x" * 50
        val2 = "y" * 60
        cache.set("key1", val1)
        cache.set("key2", val2)

        assert len(val1.encode("utf-8")) <= 100
        assert len(val1.encode("utf-8") + val2.encode("utf-8")) > 100

        val3 = "z" * 40
        # This should cause key1 to be evicted
        cache.set("key3", val3)  # 40 bytes

        assert "key1" not in cache
        assert "key2" in cache
        assert "key3" in cache


def test_file_permissions(file_cache):
    import sys

    file_cache.set("test_key", "test_value")
    file_path = file_cache._get_file_path("test_key")
    assert os.access(file_path, os.R_OK)
    assert os.access(file_path, os.W_OK)
    # Skip X_OK check on Windows - Windows doesn't have Unix-style
    # executable permissions (files are executable based on extension)
    if sys.platform != "win32":
        assert not os.access(file_path, os.X_OK)


def test_unicode_keys_and_values(file_cache):
    unicode_key = "üñîçødé_këy"
    unicode_value = "üñîçødé_vålüé"
    file_cache.set(unicode_key, unicode_value)
    assert file_cache.get(unicode_key) == unicode_value


def test_empty_string_as_key_and_value(file_cache):
    file_cache.set("", "")
    assert file_cache.get("") == ""


def test_none_as_value(file_cache):
    file_cache.set("none_key", None)
    assert file_cache.get("none_key") is None


def test_special_characters_in_key(file_cache):
    special_key = "!@#$%^&*()_+{}[]|\\:;\"'<>,.?/~`"
    file_cache.set(special_key, "special_value")
    assert file_cache.get(special_key) == "special_value"


def test_size_limit_with_empty_key():
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = FileCache(temp_dir, size_limit=100)  # 100 bytes limit
        cache.set("", "x" * 50)  # 50 bytes with empty key
        cache.set("key2", "y" * 60)  # 60 bytes

        # This should cause the empty key to be evicted
        cache.set("key3", "z" * 40)  # 40 bytes

        assert "" not in cache
        assert "key2" in cache
        assert "key3" in cache
        assert cache.get("key2") == "y" * 60
        assert cache.get("key3") == "z" * 40


# Add more tests as needed
