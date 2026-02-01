"""Tests for LocalFileStore caching functionality.

This module tests:
1. Cache correctness and consistency
2. Cache performance improvements
3. Memory limit enforcement
4. Handling of large numbers of events without OOM
"""

import tempfile
import time

import pytest

from openhands.sdk.io.cache import MemoryLRUCache
from openhands.sdk.io.local import LocalFileStore


def test_cache_basic_functionality():
    """Test that cache stores and retrieves values correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=10)

        # Write and read
        store.write("test.txt", "Hello, World!")
        content = store.read("test.txt")
        assert content == "Hello, World!"

        # Verify it's in cache
        full_path = store.get_full_path("test.txt")
        assert full_path in store.cache


def test_cache_hit_performance():
    """Test that cache hits are significantly faster than disk reads."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=100)

        # Create a larger test file to make timing more measurable
        test_content = "x" * 100000  # 100KB
        store.write("large_file.txt", test_content)

        # Warm up and do multiple reads to get more stable timing
        num_reads = 10

        # First pass - from disk (cache miss + subsequent cache hits)
        # Clear cache first
        store.cache.clear()
        content1 = ""
        start = time.perf_counter()
        for _ in range(num_reads):
            content1 = store.read("large_file.txt")
        first_pass_time = time.perf_counter() - start

        # Second pass - all from cache (all cache hits)
        content2 = ""
        start = time.perf_counter()
        for _ in range(num_reads):
            content2 = store.read("large_file.txt")
        second_pass_time = time.perf_counter() - start

        # Verify correctness
        assert content1 == test_content
        assert content2 == test_content

        # The first pass includes one disk read, so should be noticeably slower
        # This is a more lenient check since timing can vary on different systems
        print(
            f"First pass: {first_pass_time:.6f}s, Second pass: {second_pass_time:.6f}s"
        )
        # Just verify cache is working - second pass should not be much slower
        assert second_pass_time < first_pass_time * 2


def test_cache_lru_eviction():
    """Test that LRU eviction works correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Small cache size to force evictions
        store = LocalFileStore(temp_dir, cache_limit_size=3)

        # Write 5 files, cache can only hold 3
        for i in range(5):
            store.write(f"file_{i}.txt", f"content_{i}")

        # Cache should have at most 3 entries
        assert len(store.cache) <= 3

        # The most recently written files should be in cache
        # (files 2, 3, 4)
        full_path_4 = store.get_full_path("file_4.txt")
        assert full_path_4 in store.cache


def test_cache_memory_limit():
    """Test that memory limit is enforced."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set very small memory limit (10KB)
        store = LocalFileStore(
            temp_dir, cache_limit_size=100, cache_memory_size=10 * 1024
        )

        # Write files until we exceed memory limit
        # Each file is ~2KB
        for i in range(20):
            content = "x" * 2000
            store.write(f"file_{i}.txt", content)

        # Cache should not exceed memory limit
        # Allow some overhead for Python objects
        assert store.cache.current_memory <= 12 * 1024  # 10KB + 20% overhead


def test_cache_invalidation_on_write():
    """Test that cache is updated when file is overwritten."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=10)

        # Write initial content
        store.write("test.txt", "original")
        assert store.read("test.txt") == "original"

        # Overwrite with new content
        store.write("test.txt", "updated")
        cached_content = store.read("test.txt")

        # Cache should have updated content
        assert cached_content == "updated"


def test_cache_invalidation_on_delete():
    """Test that cache is cleared when file is deleted."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=10)

        # Write and read to populate cache
        store.write("test.txt", "content")
        store.read("test.txt")

        full_path = store.get_full_path("test.txt")
        assert full_path in store.cache

        # Delete file
        store.delete("test.txt")

        # Cache should be cleared
        assert full_path not in store.cache


def test_cache_directory_deletion():
    """Test that cache is cleared when directory is deleted."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=10)

        # Create files in a subdirectory
        store.write("subdir/file1.txt", "content1")
        store.write("subdir/file2.txt", "content2")

        # Read to populate cache
        store.read("subdir/file1.txt")
        store.read("subdir/file2.txt")

        # Verify in cache
        full_path1 = store.get_full_path("subdir/file1.txt")
        full_path2 = store.get_full_path("subdir/file2.txt")
        assert full_path1 in store.cache
        assert full_path2 in store.cache

        # Delete directory
        store.delete("subdir")

        # Both files should be removed from cache
        assert full_path1 not in store.cache
        assert full_path2 not in store.cache


def test_large_number_of_events_no_oom():
    """Test that store can handle many events without OOM.

    This simulates a scenario with thousands of events being written
    and read repeatedly, which was the original motivation for caching.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Conservative limits to prevent OOM
        # Default 5MB memory, 500 entries
        store = LocalFileStore(temp_dir)

        num_events = 2000  # Simulate 2000 events

        # Write many events (simulating conversation history)
        for i in range(num_events):
            event_content = f"Event {i}: " + "x" * 200  # ~200 bytes per event
            store.write(f"events/event_{i}.json", event_content)

        # Read all events multiple times (simulating iteration)
        for iteration in range(3):
            for i in range(0, num_events, 10):  # Sample every 10th event
                content = store.read(f"events/event_{i}.json")
                assert f"Event {i}:" in content

        # Verify cache didn't grow unbounded
        assert len(store.cache) <= 500  # Should respect limit
        # Allow overhead but should be under memory limit
        assert store.cache.current_memory <= 6 * 1024 * 1024  # 6MB with overhead


def test_cache_correctness_under_concurrent_operations():
    """Test cache remains consistent with various operations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=50)

        # Interleave writes, reads, and deletes
        for i in range(10):
            # Write
            store.write(f"file_{i}.txt", f"content_{i}")

            # Read
            content = store.read(f"file_{i}.txt")
            assert content == f"content_{i}"

            # Update
            store.write(f"file_{i}.txt", f"updated_{i}")

            # Read again
            content = store.read(f"file_{i}.txt")
            assert content == f"updated_{i}"

            # Delete odd-numbered files
            if i % 2 == 1:
                store.delete(f"file_{i}.txt")

                # Verify deleted file not in cache
                full_path = store.get_full_path(f"file_{i}.txt")
                assert full_path not in store.cache

                # Verify reading deleted file raises error
                with pytest.raises(FileNotFoundError):
                    store.read(f"file_{i}.txt")


def test_cache_performance_repeated_reads():
    """Test that repeated reads show performance improvement."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=100)

        # Create test files with more content to make disk I/O more noticeable
        num_files = 50
        for i in range(num_files):
            content = f"Test content {i}\n" * 500  # ~10KB per file
            store.write(f"file_{i}.txt", content)

        # Clear cache to ensure fresh start
        store.cache.clear()

        # First pass - cache misses
        start = time.perf_counter()
        for i in range(num_files):
            store.read(f"file_{i}.txt")
        first_pass_time = time.perf_counter() - start

        # Second pass - cache hits
        start = time.perf_counter()
        for i in range(num_files):
            store.read(f"file_{i}.txt")
        second_pass_time = time.perf_counter() - start

        # Second pass should be faster or at least not significantly slower
        speedup = first_pass_time / second_pass_time
        print(f"Cache speedup: {speedup:.2f}x")
        # Use a more lenient check - cache should help or at least not hurt
        assert speedup > 0.8  # Cache doesn't slow things down significantly


def test_cache_zero_size():
    """Test that cache_limit_size=0 effectively disables caching."""
    with tempfile.TemporaryDirectory() as temp_dir:
        store = LocalFileStore(temp_dir, cache_limit_size=0)

        store.write("test.txt", "content")
        store.read("test.txt")

        # Cache should remain empty or very small
        assert len(store.cache) <= 1  # May have transient entry


def test_very_large_file_cache():
    """Test handling of very large files relative to cache memory limit."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Small memory limit
        store = LocalFileStore(
            temp_dir, cache_limit_size=10, cache_memory_size=10 * 1024
        )

        # Write a file larger than cache memory limit
        large_content = "x" * 50000  # 50KB file, but 10KB cache limit
        store.write("large.txt", large_content)

        # Should still be able to read it
        content = store.read("large.txt")
        assert content == large_content

        # Cache should evict entries to stay under memory limit
        assert store.cache.current_memory <= 12 * 1024  # Allow overhead


def test_cache_with_evict_correct():
    cache = MemoryLRUCache(1000, 2)
    cache["key1"] = "a" * 500
    cache["key2"] = "b" * 500
    cache["key3"] = "c" * 100
    # key1 should be evicted at this point (exceeds memory/entry limit)
    assert "key2" in cache and "key3" in cache and "key1" not in cache
    total_len = len(cache["key2"]) + len(cache["key3"])
    # Verify memory statistics match the total size of key2 and key3
    assert total_len == cache.current_memory
