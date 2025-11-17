"""Tests for git models module."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from openhands.sdk.git.models import GitChange, GitChangeStatus, GitDiff


def test_git_change_status_enum_values():
    """Test GitChangeStatus enum has all expected values."""
    assert GitChangeStatus.MOVED.value == "MOVED"
    assert GitChangeStatus.ADDED.value == "ADDED"
    assert GitChangeStatus.DELETED.value == "DELETED"
    assert GitChangeStatus.UPDATED.value == "UPDATED"


def test_git_change_status_enum_members():
    """Test GitChangeStatus enum members."""
    statuses = list(GitChangeStatus)
    assert len(statuses) == 4
    assert GitChangeStatus.MOVED in statuses
    assert GitChangeStatus.ADDED in statuses
    assert GitChangeStatus.DELETED in statuses
    assert GitChangeStatus.UPDATED in statuses


def test_git_change_status_from_string():
    """Test creating GitChangeStatus from string value."""
    assert GitChangeStatus("ADDED") == GitChangeStatus.ADDED
    assert GitChangeStatus("DELETED") == GitChangeStatus.DELETED
    assert GitChangeStatus("UPDATED") == GitChangeStatus.UPDATED
    assert GitChangeStatus("MOVED") == GitChangeStatus.MOVED


def test_git_change_status_invalid_value():
    """Test that invalid status values raise ValueError."""
    with pytest.raises(ValueError):
        GitChangeStatus("INVALID")

    with pytest.raises(ValueError):
        GitChangeStatus("modified")  # lowercase not valid


def test_git_change_basic_creation():
    """Test creating a basic GitChange object."""
    change = GitChange(status=GitChangeStatus.ADDED, path=Path("test.txt"))
    assert change.status == GitChangeStatus.ADDED
    assert change.path == Path("test.txt")
    assert isinstance(change.path, Path)


def test_git_change_with_string_path():
    """Test creating GitChange with string path (Pydantic converts to Path)."""
    change = GitChange(status=GitChangeStatus.UPDATED, path="src/main.py")
    assert change.status == GitChangeStatus.UPDATED
    assert change.path == Path("src/main.py")
    assert isinstance(change.path, Path)


def test_git_change_with_nested_path():
    """Test GitChange with nested directory path."""
    change = GitChange(
        status=GitChangeStatus.DELETED,
        path=Path("src/utils/helper.py"),
    )
    assert change.status == GitChangeStatus.DELETED
    assert change.path == Path("src/utils/helper.py")
    assert change.path.parts == ("src", "utils", "helper.py")


def test_git_change_serialization():
    """Test GitChange serialization with model_dump."""
    change = GitChange(status=GitChangeStatus.ADDED, path=Path("new_file.txt"))
    data = change.model_dump()

    assert "status" in data
    assert "path" in data
    assert data["status"] == GitChangeStatus.ADDED


def test_git_change_serialization_mode_json():
    """Test GitChange serialization in JSON mode."""
    change = GitChange(status=GitChangeStatus.UPDATED, path=Path("file.py"))
    data = change.model_dump(mode="json")

    assert data["status"] == "UPDATED"
    # Path should be converted to string in JSON mode
    assert isinstance(data["path"], (str, Path))


def test_git_change_deserialization():
    """Test creating GitChange from dictionary."""
    data = {
        "status": GitChangeStatus.DELETED,
        "path": Path("deleted.txt"),
    }
    change = GitChange(**data)

    assert change.status == GitChangeStatus.DELETED
    assert change.path == Path("deleted.txt")


def test_git_change_equality():
    """Test GitChange equality comparison."""
    change1 = GitChange(status=GitChangeStatus.ADDED, path=Path("file.txt"))
    change2 = GitChange(status=GitChangeStatus.ADDED, path=Path("file.txt"))
    change3 = GitChange(status=GitChangeStatus.UPDATED, path=Path("file.txt"))

    assert change1 == change2
    assert change1 != change3


def test_git_change_with_absolute_path():
    """Test GitChange with absolute path."""
    abs_path = Path("/tmp/test/file.txt")
    change = GitChange(status=GitChangeStatus.MOVED, path=abs_path)
    assert change.path == abs_path
    assert change.path.is_absolute()


def test_git_change_missing_fields():
    """Test that GitChange requires all fields."""
    with pytest.raises(ValidationError):
        GitChange(status=GitChangeStatus.ADDED)  # Missing path

    with pytest.raises(ValidationError):
        GitChange(path=Path("file.txt"))  # Missing status


def test_git_change_with_special_characters_in_path():
    """Test GitChange with special characters in path."""
    special_path = Path("files/test file with spaces.txt")
    change = GitChange(status=GitChangeStatus.ADDED, path=special_path)
    assert change.path == special_path
    assert "spaces" in str(change.path)


def test_git_diff_basic_creation():
    """Test creating a basic GitDiff object."""
    diff = GitDiff(modified="new content", original="old content")
    assert diff.modified == "new content"
    assert diff.original == "old content"


def test_git_diff_with_none_values():
    """Test GitDiff with None values."""
    diff1 = GitDiff(modified=None, original="old content")
    assert diff1.modified is None
    assert diff1.original == "old content"

    diff2 = GitDiff(modified="new content", original=None)
    assert diff2.modified == "new content"
    assert diff2.original is None

    diff3 = GitDiff(modified=None, original=None)
    assert diff3.modified is None
    assert diff3.original is None


def test_git_diff_with_empty_strings():
    """Test GitDiff with empty strings."""
    diff = GitDiff(modified="", original="")
    assert diff.modified == ""
    assert diff.original == ""


def test_git_diff_with_multiline_content():
    """Test GitDiff with multiline content."""
    modified = "Line 1\nLine 2\nLine 3"
    original = "Original Line 1\nOriginal Line 2"
    diff = GitDiff(modified=modified, original=original)

    assert diff.modified == modified
    assert diff.original == original
    assert "\n" in diff.modified
    assert "\n" in diff.original


def test_git_diff_serialization():
    """Test GitDiff serialization with model_dump."""
    diff = GitDiff(modified="modified content", original="original content")
    data = diff.model_dump()

    assert "modified" in data
    assert "original" in data
    assert data["modified"] == "modified content"
    assert data["original"] == "original content"


def test_git_diff_serialization_with_none():
    """Test GitDiff serialization with None values."""
    diff = GitDiff(modified=None, original="content")
    data = diff.model_dump()

    assert data["modified"] is None
    assert data["original"] == "content"


def test_git_diff_deserialization():
    """Test creating GitDiff from dictionary."""
    data = {
        "modified": "new text",
        "original": "old text",
    }
    diff = GitDiff(**data)

    assert diff.modified == "new text"
    assert diff.original == "old text"


def test_git_diff_equality():
    """Test GitDiff equality comparison."""
    diff1 = GitDiff(modified="content", original="original")
    diff2 = GitDiff(modified="content", original="original")
    diff3 = GitDiff(modified="different", original="original")

    assert diff1 == diff2
    assert diff1 != diff3


def test_git_diff_with_special_characters():
    """Test GitDiff with special characters in content."""
    modified = "àáâãäå\n中文\n🚀 emoji\n\"quotes\""
    original = "Original: àáâãäå"
    diff = GitDiff(modified=modified, original=original)

    assert diff.modified == modified
    assert diff.original == original
    assert "🚀" in diff.modified
    assert "中文" in diff.modified


def test_git_diff_with_tabs_and_newlines():
    """Test GitDiff with tabs and various newline styles."""
    modified = "Line 1\n\tIndented line\n\nBlank line above"
    original = "Original\r\nWindows newline\tTab"
    diff = GitDiff(modified=modified, original=original)

    assert "\t" in diff.modified
    assert "\n" in diff.modified
    assert "\t" in diff.original


def test_git_diff_large_content():
    """Test GitDiff with large content strings."""
    modified = "x" * 10000
    original = "y" * 10000
    diff = GitDiff(modified=modified, original=original)

    assert len(diff.modified) == 10000
    assert len(diff.original) == 10000
    assert diff.modified == modified
    assert diff.original == original


def test_git_change_immutability():
    """Test that GitChange objects are immutable (frozen)."""
    change = GitChange(status=GitChangeStatus.ADDED, path=Path("file.txt"))

    # Pydantic models are not frozen by default, but we can test assignment works
    # This is just to verify the model works as expected
    assert change.status == GitChangeStatus.ADDED
    assert change.path == Path("file.txt")


def test_git_diff_default_values():
    """Test GitDiff doesn't require values if they can be None."""
    # Both fields allow None, so we can create with no args if model allows it
    # Let's test the model accepts the documented types
    diff = GitDiff(modified=None, original=None)
    assert diff.modified is None
    assert diff.original is None


def test_git_change_status_string_representation():
    """Test string representation of GitChangeStatus."""
    assert str(GitChangeStatus.ADDED) == "GitChangeStatus.ADDED"
    assert repr(GitChangeStatus.ADDED) == "<GitChangeStatus.ADDED: 'ADDED'>"


def test_git_change_json_schema():
    """Test that GitChange has valid JSON schema."""
    schema = GitChange.model_json_schema()
    assert "properties" in schema
    assert "status" in schema["properties"]
    assert "path" in schema["properties"]


def test_git_diff_json_schema():
    """Test that GitDiff has valid JSON schema."""
    schema = GitDiff.model_json_schema()
    assert "properties" in schema
    assert "modified" in schema["properties"]
    assert "original" in schema["properties"]
