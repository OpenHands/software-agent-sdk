"""Tests for the visualize_diff functionality in FileEditorObservation."""

from rich.text import Text

from openhands.tools.file_editor.definition import FileEditorObservation
from openhands.tools.file_editor.utils.diff import (
    get_edit_groups,
    visualize_diff,
)


def test_visualize_diff_simple_replacement():
    """Test visualize_diff with a simple string replacement."""
    old_content = """def hello():
    print("Hello, World!")
    return True"""

    new_content = """def hello():
    print("Hello, Universe!")
    return True"""

    observation = FileEditorObservation(
        command="str_replace",
        path="/test/file.py",
        old_content=old_content,
        new_content=new_content,
        prev_exist=True,
    )

    assert observation.path == "/test/file.py"
    diff = visualize_diff(
        observation.path, observation.old_content, observation.new_content
    )

    # Check that the diff contains expected elements
    diff_str = str(diff)
    assert "[File /test/file.py edited with 1 changes.]" in diff_str
    assert "[begin of edit 1 / 1]" in diff_str
    assert "[end of edit 1 / 1]" in diff_str
    assert "(content before edit)" in diff_str
    assert "(content after edit)" in diff_str
    assert '-2|    print("Hello, World!")' in diff_str
    assert '+2|    print("Hello, Universe!")' in diff_str


def test_visualize_diff_no_changes():
    """Test visualize_diff when there are no changes."""
    content = """def hello():
    print("Hello, World!")
    return True"""

    observation = FileEditorObservation(
        command="str_replace",
        path="/test/file.py",
        old_content=content,
        new_content=content,
        prev_exist=True,
    )

    assert observation.path == "/test/file.py"
    diff = visualize_diff(
        observation.path, observation.old_content, observation.new_content
    )

    expected_msg = (
        "(no changes detected. Please make sure your edits change "
        "the content of the existing file.)\n"
    )
    assert isinstance(diff, Text)
    assert str(diff) == expected_msg


def test_visualize_diff_multiple_changes():
    """Test visualize_diff with multiple changes in the same hunk."""
    old_content = """def calculate(a, b):
    result = a + b
    print(f"Result: {result}")
    return result

def main():
    x = 5
    y = 10
    calculate(x, y)"""

    new_content = """def calculate(a, b):
    result = a * b  # Changed from + to *
    print(f"Product: {result}")  # Changed message
    return result

def main():
    x = 7  # Changed value
    y = 10
    calculate(x, y)"""

    observation = FileEditorObservation(
        command="str_replace",
        path="/test/calc.py",
        old_content=old_content,
        new_content=new_content,
        prev_exist=True,
    )
    assert observation.path == "/test/calc.py"
    diff = visualize_diff(
        observation.path, observation.old_content, observation.new_content
    )

    # Check that the diff contains expected elements
    diff_str = str(diff)
    assert "[File /test/calc.py edited with 1 changes.]" in diff_str
    assert "-2|    result = a + b" in diff_str
    assert "+2|    result = a * b  # Changed from + to *" in diff_str
    assert '-3|    print(f"Result: {result}")' in diff_str
    assert '+3|    print(f"Product: {result}")  # Changed message' in diff_str
    assert "-7|    x = 5" in diff_str
    assert "+7|    x = 7  # Changed value" in diff_str


def test_visualize_diff_attempted_edit():
    """Test visualize_diff with change_applied=False."""
    old_content = "old line"
    new_content = "new line"

    observation = FileEditorObservation(
        command="str_replace",
        path="/test/file.py",
        old_content=old_content,
        new_content=new_content,
        prev_exist=True,
    )

    assert observation.path == "/test/file.py"
    diff = visualize_diff(
        observation.path,
        observation.old_content,
        observation.new_content,
        change_applied=False,
    )

    diff_str = str(diff)
    assert "[Changes are NOT applied to /test/file.py" in diff_str
    assert "ATTEMPTED edit" in diff_str
    assert "[begin of ATTEMPTED edit 1 / 1]" in diff_str
    assert "[end of ATTEMPTED edit 1 / 1]" in diff_str


def test_visualize_diff_caching():
    """Test that diff visualization is cached properly."""
    old_content = "old line"
    new_content = "new line"

    observation = FileEditorObservation(
        command="str_replace",
        path="/test/file.py",
        old_content=old_content,
        new_content=new_content,
        prev_exist=True,
    )

    # First call should compute and cache
    assert observation._diff_cache is None
    assert observation.path == "/test/file.py"
    diff1 = visualize_diff(
        observation.path, observation.old_content, observation.new_content
    )

    # Second call should use cache
    diff2 = visualize_diff(
        observation.path, observation.old_content, observation.new_content
    )

    assert diff1 == diff2


def test_visualize_diff_custom_context_lines():
    """Test visualize_diff with custom number of context lines."""
    old_content = """line1
line2
old_line
line4
line5
line6
line7"""

    new_content = """line1
line2
new_line
line4
line5
line6
line7"""

    observation = FileEditorObservation(
        command="str_replace",
        path="/test/file.py",
        old_content=old_content,
        new_content=new_content,
        prev_exist=True,
    )

    # Test with 1 context line
    assert observation.path == "/test/file.py"
    diff_1_context = visualize_diff(
        observation.path,
        observation.old_content,
        observation.new_content,
        n_context_lines=1,
    )

    # Reset cache to test different context
    observation._diff_cache = None

    # Test with 3 context lines
    diff_3_context = visualize_diff(
        observation.path,
        observation.old_content,
        observation.new_content,
        n_context_lines=3,
    )

    # The diffs should be different due to different context
    assert diff_1_context != diff_3_context


def test_get_edit_groups():
    """Test the get_edit_groups method."""
    old_content = """line1
old_line2
line3"""

    new_content = """line1
new_line2
line3"""

    observation = FileEditorObservation(
        command="str_replace",
        path="/test/file.py",
        old_content=old_content,
        new_content=new_content,
        prev_exist=True,
    )
    assert observation.path == "/test/file.py"
    assert observation.old_content == old_content
    assert observation.new_content == new_content

    edit_groups = get_edit_groups(
        observation.old_content, observation.new_content, n_context_lines=1
    )

    assert len(edit_groups) == 1
    assert edit_groups[0].before_edits
    assert edit_groups[0].after_edits
    assert len(edit_groups[0].before_edits) == 3  # 1 context + 1 change + 1 context
    assert len(edit_groups[0].after_edits) == 3


def test_get_edit_groups_no_content():
    """Test get_edit_groups when old_content or new_content is None."""
    # Test with None values directly - should return empty list
    edit_groups = get_edit_groups(None, "some content")
    assert edit_groups == []

    edit_groups = get_edit_groups("some content", None)
    assert edit_groups == []

    edit_groups = get_edit_groups(None, None)
    assert edit_groups == []

    # Test with empty string vs content - should return edit groups
    edit_groups = get_edit_groups("", "some content")
    assert len(edit_groups) == 1
    assert edit_groups[0].before_edits == ["-1|"]
    assert edit_groups[0].after_edits == ["+1|some content"]

    edit_groups = get_edit_groups("some content", "")
    assert len(edit_groups) == 1
    assert edit_groups[0].before_edits == ["-1|some content"]
    assert edit_groups[0].after_edits == ["+1|"]


def test_visualize_diff_none_content():
    """Test visualize_diff when content is None."""
    observation = FileEditorObservation(
        command="str_replace",
        path="/test/file.py",
        old_content=None,
        new_content=None,
        prev_exist=True,
    )

    # Should not crash and should return the "no changes detected" message
    assert observation.path == "/test/file.py"
    diff = visualize_diff(
        observation.path, observation.old_content, observation.new_content
    )

    # When both contents are None, it's treated as no changes
    expected_msg = (
        "(no changes detected. Please make sure your edits change "
        "the content of the existing file.)\n"
    )
    assert isinstance(diff, Text)
    assert str(diff) == expected_msg
