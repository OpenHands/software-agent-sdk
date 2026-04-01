"""Tests for GlobExecutor implementation."""

import tempfile
import threading
from pathlib import Path

from openhands.tools.glob import GlobAction
from openhands.tools.glob.impl import GlobExecutor


def test_glob_executor_initialization():
    """Test that GlobExecutor initializes correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GlobExecutor(working_dir=temp_dir)
        assert executor.working_dir == Path(temp_dir).resolve()


def test_glob_executor_basic_pattern():
    """Test basic glob pattern matching."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files
        (Path(temp_dir) / "test1.py").write_text("# Test 1")
        (Path(temp_dir) / "test2.py").write_text("# Test 2")
        (Path(temp_dir) / "readme.md").write_text("# README")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*.py")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 2
        assert all(f.endswith(".py") for f in observation.files)
        assert observation.pattern == "*.py"
        assert observation.search_path == str(Path(temp_dir).resolve())


def test_glob_executor_recursive_pattern():
    """Test recursive glob patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create nested directory structure
        src_dir = Path(temp_dir) / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("# App")

        tests_dir = Path(temp_dir) / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("# Test")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="**/*.py")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 2
        assert all(f.endswith(".py") for f in observation.files)


def test_glob_executor_custom_path():
    """Test glob with custom search path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create subdirectory with files
        sub_dir = Path(temp_dir) / "subdir"
        sub_dir.mkdir()
        (sub_dir / "file1.txt").write_text("Content 1")
        (sub_dir / "file2.txt").write_text("Content 2")

        # Create file in main directory (should not be found)
        (Path(temp_dir) / "main.txt").write_text("Main content")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*.txt", path=str(sub_dir))
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 2
        assert observation.search_path == str(sub_dir.resolve())
        assert all(str(sub_dir) in f for f in observation.files)


def test_glob_executor_invalid_path():
    """Test glob with invalid search path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*.py", path="/nonexistent/path")
        observation = executor(action)

        assert observation.is_error is True
        assert "is not a valid directory" in observation.text
        assert len(observation.files) == 0


def test_glob_executor_no_matches():
    """Test glob with no matching files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create non-matching files
        (Path(temp_dir) / "readme.md").write_text("# README")
        (Path(temp_dir) / "config.json").write_text("{}")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*.py")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 0
        assert not observation.truncated


def test_glob_executor_directories_excluded():
    """Test that directories are excluded from results."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create directories and files
        (Path(temp_dir) / "src").mkdir()
        (Path(temp_dir) / "tests").mkdir()
        (Path(temp_dir) / "file.txt").write_text("Content")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*")
        observation = executor(action)

        assert observation.is_error is False
        # Should only find the file, not directories
        assert len(observation.files) == 1
        assert observation.files[0].endswith("file.txt")


def test_glob_executor_sorting():
    """Test that files are sorted by modification time."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create files with different modification times
        import time

        file1 = Path(temp_dir) / "file1.txt"
        file1.write_text("First")
        time.sleep(0.1)

        file2 = Path(temp_dir) / "file2.txt"
        file2.write_text("Second")
        time.sleep(0.1)

        file3 = Path(temp_dir) / "file3.txt"
        file3.write_text("Third")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*.txt")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 3

        # Files should be sorted by modification time (newest first)
        # file3 should be first (most recent)
        assert "file3.txt" in observation.files[0]


def test_glob_executor_truncation():
    """Test that results are truncated to 100 files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create more than 100 files
        for i in range(150):
            (Path(temp_dir) / f"file_{i:03d}.txt").write_text(f"Content {i}")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*.txt")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 100
        assert observation.truncated is True


def test_glob_executor_complex_patterns():
    """Test complex glob patterns."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create files with various extensions
        files = [
            "config.json",
            "config.yaml",
            "config.yml",
            "config.toml",
            "readme.md",
            "app.py",
        ]

        for file_name in files:
            (Path(temp_dir) / file_name).write_text(f"Content of {file_name}")

        executor = GlobExecutor(working_dir=temp_dir)

        # Test wildcard pattern for config files
        action = GlobAction(pattern="config.*")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 4  # All config files
        extensions = {Path(f).suffix for f in observation.files}
        assert extensions == {".json", ".yaml", ".yml", ".toml"}


def test_glob_executor_exception_handling():
    """Test that executor handles exceptions gracefully."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GlobExecutor(working_dir=temp_dir)

        # Create action with problematic path that might cause issues
        # This tests the general exception handling in the executor
        action = GlobAction(pattern="*.py", path=temp_dir)
        observation = executor(action)

        # Should not raise exception, even if there are no matches
        assert observation.is_error is False or isinstance(observation.content, str)
        assert isinstance(observation.files, list)


def test_glob_executor_absolute_paths():
    """Test that executor returns absolute paths."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test file
        (Path(temp_dir) / "test.py").write_text("# Test")

        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*.py")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 1

        # Check that returned path is absolute
        file_path = observation.files[0]
        assert Path(file_path).is_absolute()
        assert Path(file_path).exists()


def test_glob_executor_empty_directory():
    """Test glob in empty directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 0
        assert not observation.truncated


def test_extract_search_path_from_pattern_absolute_with_recursive():
    """Test _extract_search_path_from_pattern with absolute path and **."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(
        "/path/to/dir/**/*.py"
    )

    assert search_path == Path("/path/to/dir").resolve()
    assert pattern == "**/*.py"


def test_extract_search_path_from_pattern_absolute_without_recursive():
    """Test _extract_search_path_from_pattern with absolute path without **."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(
        "/path/to/dir/*.py"
    )

    assert search_path == Path("/path/to/dir").resolve()
    assert pattern == "*.py"


def test_extract_search_path_from_pattern_relative():
    """Test _extract_search_path_from_pattern with relative pattern."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern("**/*.py")

    assert search_path is None
    assert pattern == "**/*.py"


def test_extract_search_path_from_pattern_relative_simple():
    """Test _extract_search_path_from_pattern with simple relative pattern."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern("*.py")

    assert search_path is None
    assert pattern == "*.py"


def test_extract_search_path_from_pattern_empty():
    """Test _extract_search_path_from_pattern with empty pattern."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern("")

    assert search_path is None
    assert pattern == "**/*"


def test_extract_search_path_from_pattern_home_directory():
    """Test _extract_search_path_from_pattern with ~ (home directory)."""
    home = Path.home()
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(
        "~/documents/**/*.txt"
    )

    assert search_path == (home / "documents").resolve()
    assert pattern == "**/*.txt"


def test_extract_search_path_from_pattern_root_glob():
    """Test _extract_search_path_from_pattern with glob at root level."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern("/*/*.py")

    assert search_path == Path("/").resolve()
    assert pattern == "*/*.py"


def test_extract_search_path_from_pattern_nested_glob():
    """Test _extract_search_path_from_pattern with glob in middle of path."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(
        "/path/to/*/subdir/*.py"
    )

    assert search_path == Path("/path/to").resolve()
    assert pattern == "*/subdir/*.py"


def test_extract_search_path_from_pattern_deep_nesting():
    """Test _extract_search_path_from_pattern with deeply nested absolute path."""
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(
        "/usr/local/lib/python3.13/**/*.so"
    )

    assert search_path == Path("/usr/local/lib/python3.13").resolve()
    assert pattern == "**/*.so"


def test_glob_executor_concurrent_different_directories():
    """Test that concurrent glob calls on different directories return correct results.

    This exercises the fallback (Python glob) path. Because _execute_with_glob
    uses os.chdir(), concurrent calls can interfere with each other if not
    properly handled. With ripgrep available the subprocess path is used
    instead, which is inherently thread-safe.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create two separate directories with distinct files
        dir_a = Path(temp_dir) / "dir_a"
        dir_a.mkdir()
        for i in range(5):
            (dir_a / f"alpha_{i}.py").write_text(f"# alpha {i}")

        dir_b = Path(temp_dir) / "dir_b"
        dir_b.mkdir()
        for i in range(5):
            (dir_b / f"beta_{i}.txt").write_text(f"# beta {i}")

        executor = GlobExecutor(working_dir=temp_dir)

        results: list[tuple[str, list[str]]] = []
        results_lock = threading.Lock()
        errors: list[Exception] = []

        def search_dir(name: str, path: str, pattern: str):
            try:
                action = GlobAction(pattern=pattern, path=path)
                obs = executor(action)
                with results_lock:
                    results.append((name, obs.files))
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(4):
            t_a = threading.Thread(target=search_dir, args=("a", str(dir_a), "*.py"))
            t_b = threading.Thread(target=search_dir, args=("b", str(dir_b), "*.txt"))
            threads.extend([t_a, t_b])

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent glob calls raised errors: {errors}"
        assert len(results) == 8, f"Expected 8 results, got {len(results)}"
        results_a = [files for name, files in results if name == "a"]
        results_b = [files for name, files in results if name == "b"]
        assert len(results_a) == 4
        assert len(results_b) == 4
        assert all(len(files) == 5 for files in results_a)
        assert all(len(files) == 5 for files in results_b)
        assert all(all("alpha_" in Path(f).name for f in files) for files in results_a)
        assert all(all("beta_" in Path(f).name for f in files) for files in results_b)
