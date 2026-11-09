"""Tests for the simplified TODO scanner functionality."""

import sys
import tempfile
from pathlib import Path

import pytest


# Import the scanner functions
todo_mgmt_path = (
    Path(__file__).parent.parent.parent
    / "examples"
    / "03_github_workflows"
    / "03_todo_management"
)
sys.path.append(str(todo_mgmt_path))
from scanner import (  # noqa: E402  # type: ignore[import-not-found]
    scan_directory,
    scan_file_for_todos,
)


def test_scan_python_file_with_todos():
    """Test scanning a Python file with TODO comments."""
    content = """#!/usr/bin/env python3
def function1():
    # TODO(openhands): Add input validation
    return "hello"

def function2():
    # TODO(openhands): Implement error handling
    pass
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        f.flush()

        todos = scan_file_for_todos(Path(f.name))

    Path(f.name).unlink()

    assert len(todos) == 2
    assert todos[0]["description"] == "Add input validation"
    assert todos[1]["description"] == "Implement error handling"


@pytest.mark.parametrize(
    ("suffix", "content", "expected_description"),
    [
        pytest.param(
            ".ts",
            """function processData(): string {
    // TODO(openhands): Add validation
    return data;
}
""",
            "Add validation",
            id="typescript",
        ),
        pytest.param(
            ".java",
            """public class Test {
    public void method() {
        // TODO(openhands): Implement this method
        System.out.println("Hello");
    }
}
""",
            "Implement this method",
            id="java",
        ),
        pytest.param(
            ".rs",
            """fn main() {
    // TODO(openhands): Add error handling
    println!("Hello, world!");
}""",
            "Add error handling",
            id="rust",
        ),
    ],
)
def test_scan_language_file(suffix, content, expected_description):
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(content)
        f.flush()

        todos = scan_file_for_todos(Path(f.name))

    Path(f.name).unlink()

    assert len(todos) == 1
    assert todos[0]["description"] == expected_description


def test_scan_unsupported_file_extension():
    """Test that unsupported file extensions are ignored."""
    content = """// TODO(openhands): This should be ignored"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(content)
        f.flush()

        todos = scan_file_for_todos(Path(f.name))

    Path(f.name).unlink()

    assert len(todos) == 0


def test_scan_all_todos():
    """Test that all TODO(openhands) comments are found."""
    content = """def test():
    # TODO(openhands): This should be found
    # TODO(openhands): This should also be found
    # TODO(openhands): https://github.com/owner/repo/pull/123
    pass
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        f.flush()

        todos = scan_file_for_todos(Path(f.name))

    Path(f.name).unlink()

    assert len(todos) == 3
    assert todos[0]["description"] == "This should be found"
    assert todos[1]["description"] == "This should also be found"
    assert todos[2]["description"] == "https://github.com/owner/repo/pull/123"


def test_scan_directory():
    """Test scanning a directory with multiple files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create Python file with TODO (avoid "test" in filename)
        py_file = temp_path / "main.py"
        py_file.write_text("# TODO(openhands): Python todo\nprint('hello')")

        # Create TypeScript file with TODO (avoid "test" in filename)
        ts_file = temp_path / "app.ts"
        ts_file.write_text("// TODO(openhands): TypeScript todo\nconsole.log('hello');")

        # Create unsupported file (should be ignored)
        js_file = temp_path / "script.js"
        js_file.write_text("// TODO(openhands): Should be ignored")

        todos = scan_directory(temp_path)

        assert len(todos) == 2
        descriptions = [todo["description"] for todo in todos]
        assert "Python todo" in descriptions
        assert "TypeScript todo" in descriptions


def test_todo_with_continuation_lines():
    """Test TODO with continuation comment lines."""
    content = """def test():
    # TODO(openhands): Add error handling
    # This should handle network timeouts
    # and retry failed requests
    # with exponential backoff
    pass
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        f.flush()

        todos = scan_file_for_todos(Path(f.name))

    Path(f.name).unlink()

    assert len(todos) == 1
    expected_desc = (
        "Add error handling This should handle network timeouts "
        "and retry failed requests with exponential backoff"
    )
    assert todos[0]["description"] == expected_desc


def test_todo_without_description():
    """Test TODO without initial description but with continuation lines."""
    content = """def test():
    # TODO(openhands)
    # Implement user authentication
    # with proper session management
    pass
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        f.flush()

        todos = scan_file_for_todos(Path(f.name))

    Path(f.name).unlink()

    assert len(todos) == 1
    expected_desc = "Implement user authentication with proper session management"
    assert todos[0]["description"] == expected_desc


def test_empty_file():
    """Test scanning an empty file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("")
        f.flush()

        todos = scan_file_for_todos(Path(f.name))

    Path(f.name).unlink()

    assert len(todos) == 0


def test_custom_todo_identifier():
    """Test scanning with a custom TODO identifier."""
    content = """def test():
    # TODO(myteam): Custom identifier test
    # This should be found with custom identifier
    pass
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        f.flush()

        # Test with custom identifier
        todos = scan_file_for_todos(Path(f.name), "TODO(myteam)")

    Path(f.name).unlink()

    assert len(todos) == 1
    assert todos[0]["description"] == (
        "Custom identifier test This should be found with custom identifier"
    )


def test_custom_identifier_with_special_chars():
    """Test custom identifier with regex special characters."""
    content = """def test():
    # TODO[urgent]: Special chars in identifier
    pass
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(content)
        f.flush()

        # Test with identifier containing regex special chars
        todos = scan_file_for_todos(Path(f.name), "TODO[urgent]")

    Path(f.name).unlink()

    assert len(todos) == 1
    assert todos[0]["description"] == "Special chars in identifier"
