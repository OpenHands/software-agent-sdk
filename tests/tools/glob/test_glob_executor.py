"""Tests for GlobExecutor implementation."""

import os
import tempfile
import threading
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from openhands.tools.glob import GlobAction
from openhands.tools.glob.impl import GlobExecutor, _expand_braces


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
        assert all(str(sub_dir.resolve()) in f for f in observation.files)


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


def test_execute_with_ripgrep_exactly_100_matches_not_truncated(monkeypatch):
    """The ripgrep backend itself must not flag exactly 100 matches as truncated.

    The subprocess output is stubbed so the ripgrep code path is exercised even
    when ripgrep is not installed on the machine running the tests.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GlobExecutor(working_dir=temp_dir)
        stdout = (
            "\n".join(str(Path(temp_dir) / f"file_{i:03d}.txt") for i in range(100))
            + "\n"
        )

        def fake_run(*args, **kwargs):
            return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr("openhands.tools.glob.impl.subprocess.run", fake_run)
        files, truncated = executor._execute_with_ripgrep("*.txt", Path(temp_dir))

        assert len(files) == 100
        assert truncated is False


def test_execute_with_ripgrep_101_matches_truncated(monkeypatch):
    """One more than the limit must still be truncated through the ripgrep path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GlobExecutor(working_dir=temp_dir)
        stdout = (
            "\n".join(str(Path(temp_dir) / f"file_{i:03d}.txt") for i in range(101))
            + "\n"
        )

        def fake_run(*args, **kwargs):
            return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr("openhands.tools.glob.impl.subprocess.run", fake_run)
        files, truncated = executor._execute_with_ripgrep("*.txt", Path(temp_dir))

        assert len(files) == 100
        assert truncated is True


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


def test_glob_executor_preserves_symlink_paths():
    """Test that the Python glob fallback preserves symlink paths."""
    with tempfile.TemporaryDirectory() as temp_dir:
        real_dir = Path(temp_dir) / "real"
        real_dir.mkdir()
        target = real_dir / "target.data"
        target.write_text("target")

        link = Path(temp_dir) / "link.txt"
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")

        executor = GlobExecutor(working_dir=temp_dir)
        executor._ripgrep_available = False
        action = GlobAction(pattern="*.txt")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 1
        assert Path(observation.files[0]).is_absolute()
        assert Path(observation.files[0]).name == link.name
        assert os.path.islink(observation.files[0])


def test_glob_executor_empty_directory():
    """Test glob in empty directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        executor = GlobExecutor(working_dir=temp_dir)
        action = GlobAction(pattern="*")
        observation = executor(action)

        assert observation.is_error is False
        assert len(observation.files) == 0
        assert not observation.truncated


@pytest.mark.parametrize(
    ("full_pattern", "expected_path", "expected_pattern"),
    [
        pytest.param(
            "/path/to/dir/**/*.py", "/path/to/dir", "**/*.py", id="with_recursive"
        ),
        pytest.param(
            "/path/to/dir/*.py", "/path/to/dir", "*.py", id="without_recursive"
        ),
        pytest.param("/*/*.py", "/", "*/*.py", id="root_glob"),
        pytest.param(
            "/path/to/*/subdir/*.py", "/path/to", "*/subdir/*.py", id="nested_glob"
        ),
        pytest.param(
            "/usr/local/lib/python3.13/**/*.so",
            "/usr/local/lib/python3.13",
            "**/*.so",
            id="deep_nesting",
        ),
    ],
)
def test_extract_search_path_from_pattern_absolute(
    full_pattern, expected_path, expected_pattern
):
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(full_pattern)

    assert search_path == Path(expected_path).resolve()
    assert pattern == expected_pattern


@pytest.mark.parametrize(
    ("full_pattern", "expected_pattern"),
    [
        pytest.param("**/*.py", "**/*.py", id="relative"),
        pytest.param("*.py", "*.py", id="relative_simple"),
        pytest.param("", "**/*", id="empty"),
    ],
)
def test_extract_search_path_from_pattern_without_search_path(
    full_pattern, expected_pattern
):
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(full_pattern)

    assert search_path is None
    assert pattern == expected_pattern


def test_extract_search_path_from_pattern_home_directory():
    """Test _extract_search_path_from_pattern with ~ (home directory)."""
    home = Path.home()
    search_path, pattern = GlobExecutor._extract_search_path_from_pattern(
        "~/documents/**/*.txt"
    )

    assert search_path == (home / "documents").resolve()
    assert pattern == "**/*.txt"


def test_glob_executor_concurrent_with_ripgrep():
    """Test that concurrent ripgrep-based glob calls return correct results.

    Ripgrep spawns independent subprocesses with their own working directory,
    so concurrent calls are inherently thread-safe.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        dir_a = Path(temp_dir) / "dir_a"
        dir_a.mkdir()
        for i in range(5):
            (dir_a / f"alpha_{i}.py").write_text(f"# alpha {i}")

        dir_b = Path(temp_dir) / "dir_b"
        dir_b.mkdir()
        for i in range(5):
            (dir_b / f"beta_{i}.txt").write_text(f"# beta {i}")

        executor = GlobExecutor(working_dir=temp_dir)
        if not executor.is_parallel_safe():
            pytest.skip("ripgrep not installed")

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


def test_python_fallback_expands_brace_patterns(tmp_path):
    # The tool description advertises "**/*.{json,yaml,yml,toml}".
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.yaml").write_text("")
    (tmp_path / "c.py").write_text("")
    executor = GlobExecutor(working_dir=str(tmp_path))

    files, _ = executor._execute_with_glob("**/*.{json,yaml}", tmp_path)

    assert {Path(f).name for f in files} == {"a.json", "b.yaml"}


@pytest.mark.parametrize(
    "pattern, expected",
    [
        ("*.py", ["*.py"]),
        ("*.{json,yaml}", ["*.json", "*.yaml"]),
        ("{a,b}/{c,d}", ["a/c", "a/d", "b/c", "b/d"]),
        ("{a,b{c,d}}", ["a", "bc", "bd"]),
        ("{{a,b},{c,d}}", ["a", "b", "c", "d"]),
        ("{a,b", ["{a,b"]),
    ],
)
def test_expand_braces(pattern, expected):
    assert _expand_braces(pattern) == expected


def test_ripgrep_failure_is_reported_as_an_error_not_as_no_match(monkeypatch, tmp_path):
    """A ripgrep run that could not search must not claim the files are absent.

    An unparsable glob, or a directory the sandbox cannot list, makes ripgrep
    exit 2 with empty stdout; the executor used to report "No files found",
    which is a factual claim about the filesystem for a search that never ran.
    """
    import subprocess

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=2,
            stdout="",
            stderr="rg: error parsing glob '**/*.{py'",
        )

    monkeypatch.setattr(
        "openhands.tools.glob.impl._check_ripgrep_available", lambda: True
    )
    monkeypatch.setattr("openhands.tools.glob.impl.subprocess.run", fake_run)
    executor = GlobExecutor(working_dir=str(tmp_path))

    observation = executor(GlobAction(pattern="**/*.{py"))

    assert observation.is_error is True
    assert "error parsing glob" in observation.text
    assert "No files found" not in observation.text


def test_ripgrep_no_match_still_reports_no_files(monkeypatch, tmp_path):
    """ripgrep exits 1 when nothing matches; that stays a normal empty result."""
    import subprocess

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr=""
        )

    monkeypatch.setattr(
        "openhands.tools.glob.impl._check_ripgrep_available", lambda: True
    )
    monkeypatch.setattr("openhands.tools.glob.impl.subprocess.run", fake_run)
    executor = GlobExecutor(working_dir=str(tmp_path))

    observation = executor(GlobAction(pattern="**/*.py"))

    assert observation.is_error is False
    assert "No files found" in observation.text
