"""Tests for memory usage in file editor."""

import gc
import os
import tempfile
from pathlib import Path

import psutil
import pytest
from filelock import FileLock

from openhands.tools.file_editor import file_editor

from .conftest import assert_successful_result


# Apply the forked marker and serialize execution across workers
pytestmark = [pytest.mark.forked, pytest.mark.usefixtures("isolate_memory_usage_tests")]


@pytest.fixture(scope="function")
def isolate_memory_usage_tests():
    """Guard memory-sensitive tests from parallel execution."""
    lock_path = Path(tempfile.gettempdir()) / "openhands_str_replace_memory.lock"
    with FileLock(lock_path):
        yield


def test_file_read_memory_usage(temp_file):
    """Test that reading a large file uses memory efficiently."""
    # Create a large file (~5MB) to stress memory while staying below limits
    file_size_mb = 5.0
    line_size = 100  # bytes per line approximately
    num_lines = int((file_size_mb * 1024 * 1024) // line_size)

    print(f"\nCreating test file with {num_lines} lines...")
    with open(temp_file, "w") as f:
        for i in range(num_lines):
            f.write(f"Line {i}: " + "x" * (line_size - 10) + "\n")

    actual_size = os.path.getsize(temp_file) / (1024 * 1024)
    print(f"File created, size: {actual_size:.2f} MB")

    # Force Python to release file handles and clear buffers
    gc.collect()

    # Warm up the editor so imports/cache allocations are excluded from measurement
    warmup_result = file_editor(
        command="view",
        path=temp_file,
        view_range=[1, 1],
    )
    assert_successful_result(warmup_result)
    del warmup_result
    gc.collect()

    # Get initial memory usage
    initial_memory = psutil.Process(os.getpid()).memory_info().rss
    print(f"Initial memory usage: {initial_memory / 1024 / 1024:.2f} MB")

    # Test reading specific lines
    try:
        result = file_editor(
            command="view",
            path=temp_file,
            view_range=[5000, 5100],  # Read 100 lines from middle
        )
    except Exception as e:
        print(f"\nError during file read: {str(e)}")
        raise

    # Pull output before measuring and drop references to encourage GC
    assert_successful_result(result)
    content = result.text
    del result
    gc.collect()

    # Check memory usage after reading
    current_memory = psutil.Process(os.getpid()).memory_info().rss
    memory_growth = current_memory - initial_memory
    print(
        f"Memory growth after reading 100 lines: {memory_growth / 1024 / 1024:.2f} MB"
    )

    # Memory growth should be small since we're only reading 100 lines
    # Allow for some overhead but it should be much less than file size
    # Increased to account for chardet's memory usage and environmental variations
    max_growth_mb = 6  # 6MB max growth to account for normal variations
    assert memory_growth <= max_growth_mb * 1024 * 1024, (
        f"Memory growth too high: {memory_growth / 1024 / 1024:.2f} MB "
        f"(limit: {max_growth_mb} MB)"
    )

    # Verify we got the correct lines
    line_count = content.count("\n")
    assert line_count >= 99, f"Should have read at least 99 lines, got {line_count}"
    assert "Line 5000:" in content, "Should contain the first requested line"
    assert "Line 5099:" in content, "Should contain the last requested line"

    print("Test completed successfully")


@pytest.mark.skipif(
    os.environ.get("CI", "false").lower() == "true",
    reason="Skip memory leak test on CI since it will break due to memory limits",
)
def test_file_editor_memory_leak(temp_file):
    """Test to demonstrate memory growth during multiple file edits."""
    print("\nStarting memory leak test...")

    # Create initial content that's large enough to test but not overwhelming
    # Keep total file size under 10MB to avoid file validation errors
    base_content = (
        "Initial content with some reasonable length to make the file larger\n"
    )
    content = base_content * 100
    print(f"\nCreating initial file with {len(content)} bytes")
    with open(temp_file, "w") as f:
        f.write(content)
    print(f"Initial file created, size: {os.path.getsize(temp_file) / 1024:.1f} KB")

    # Force Python to release file handles and clear buffers
    gc.collect()

    # Warm up the editor so imports/cache allocations are excluded from measurement
    warmup_result = file_editor(
        command="view",
        path=temp_file,
        view_range=[1, 1],
    )
    assert_successful_result(warmup_result)
    del warmup_result
    gc.collect()

    # Set memory limit to 128MB to make it more likely to catch issues
    memory_limit = 128 * 1024 * 1024  # 128MB in bytes
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        print("Memory limit set successfully")
    except Exception as e:
        print(f"Warning: Could not set memory limit: {str(e)}")
        # If we can't set memory limit, we'll still run the test but rely on
        # growth checks

    initial_memory = psutil.Process(os.getpid()).memory_info().rss
    print(f"\nInitial memory usage: {initial_memory / 1024 / 1024:.2f} MB")

    # Store memory readings for analysis
    memory_readings = []
    file_size_mb = 0.0

    try:
        # Perform edits with reasonable content size
        for i in range(500):  # Reduced iterations to avoid memory issues in CI
            # Create content for each edit - keep it small to avoid file size limits
            old_content = f"content_{i}\n" * 5  # 5 lines per edit
            new_content = f"content_{i + 1}\n" * 5

            # Instead of appending, we'll replace content to keep file size stable
            with open(temp_file) as f:
                current_content = f.read()

            # Insert old_content at a random position while keeping file size stable
            insert_pos = len(current_content) // 2
            new_file_content = (
                current_content[:insert_pos]
                + old_content
                + current_content[insert_pos + len(old_content) :]
            )
            with open(temp_file, "w") as f:
                f.write(new_file_content)

            # Perform the edit
            try:
                if i == 0:
                    print(
                        f"\nInitial file size: "
                        f"{os.path.getsize(temp_file) / (1024 * 1024):.2f} MB"
                    )
                    print(f"Sample content to replace: {old_content[:100]}...")
                result = file_editor(
                    command="str_replace",
                    path=temp_file,
                    old_str=old_content,
                    new_str=new_content,
                )
                if i == 0:
                    content_str = result.text
                    print(f"First edit result: {content_str[:200]}...")
            except Exception as e:
                print(f"\nError during edit {i}:")
                print(f"File size: {os.path.getsize(temp_file) / (1024 * 1024):.2f} MB")
                print(f"Error: {str(e)}")
                raise

            if i % 25 == 0:  # Check more frequently
                try:
                    current_memory = psutil.Process(os.getpid()).memory_info().rss
                    memory_mb = current_memory / 1024 / 1024
                    memory_readings.append(memory_mb)
                except (psutil.Error, MemoryError, OSError) as e:
                    # In resource-constrained environments (like CI), psutil might fail
                    # Skip memory monitoring but continue the test
                    print(f"Warning: Could not get memory info: {e}")
                    continue

                # Get current file size
                file_size_mb = os.path.getsize(temp_file) / (1024 * 1024)

                # Only do memory analysis if we have memory readings
                if memory_readings:
                    print(f"\nIteration {i}:")
                    print(f"Memory usage: {memory_mb:.2f} MB")
                    print(f"File size: {file_size_mb:.2f} MB")

                    # Calculate memory growth
                    memory_growth = current_memory - initial_memory
                    growth_percent = (memory_growth / initial_memory) * 100
                    print(
                        f"Memory growth: {memory_growth / 1024 / 1024:.2f} MB "
                        f"({growth_percent:.1f}%)"
                    )

                    # Fail if memory growth is too high
                    assert memory_growth < memory_limit, (
                        f"Memory growth exceeded limit after {i} edits. "
                        f"Growth: {memory_growth / 1024 / 1024:.2f} MB"
                    )

                    # Check for consistent growth pattern
                    if len(memory_readings) >= 3:
                        # Calculate growth rate between last 3 readings
                        growth_rate = (memory_readings[-1] - memory_readings[-3]) / 2
                        print(f"Recent growth rate: {growth_rate:.2f} MB per 50 edits")

                        # Fail if we see consistent growth above a threshold
                        # Allow more growth for initial allocations and CI environment
                        # variations
                        max_growth = (
                            3 if i < 100 else 2
                        )  # MB per 50 edits (increased tolerance)
                        if growth_rate > max_growth:
                            pytest.fail(
                                f"Consistent memory growth detected: "
                                f"{growth_rate:.2f} MB per 50 edits after {i} edits"
                            )
                else:
                    print(
                        f"\nIteration {i}: File size: {file_size_mb:.2f} MB "
                        f"(memory monitoring disabled)"
                    )

    except MemoryError:
        pytest.fail("Memory limit exceeded - possible memory leak detected")
    except Exception as e:
        if "Cannot allocate memory" in str(e):
            pytest.fail("Memory limit exceeded - possible memory leak detected")
        print(f"\nFinal file size: {file_size_mb:.2f} MB")
        raise

    # Print final statistics
    print("\nMemory usage statistics:")
    if memory_readings:
        print(f"Initial memory: {memory_readings[0]:.2f} MB")
        print(f"Final memory: {memory_readings[-1]:.2f} MB")
        print(f"Total growth: {(memory_readings[-1] - memory_readings[0]):.2f} MB")
    else:
        print("Memory monitoring was disabled due to resource constraints")
    print(f"Final file size: {file_size_mb:.2f} MB")
