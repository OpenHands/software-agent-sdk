"""Tests for truncate utility functions."""

from openhands.sdk.utils import (
    DEFAULT_TEXT_CONTENT_LIMIT,
    DEFAULT_TRUNCATE_NOTICE,
    maybe_truncate,
)


def test_maybe_truncate_no_limit():
    """Test that maybe_truncate returns original content when no limit is set."""
    content = "This is a test string"
    result = maybe_truncate(content, truncate_after=None)
    assert result == content


def test_maybe_truncate_under_limit():
    """Test that maybe_truncate returns original content when under limit."""
    content = "Short string"
    result = maybe_truncate(content, truncate_after=100)
    assert result == content


def test_maybe_truncate_over_limit():
    """Test that maybe_truncate truncates content when over limit using head-and-tail."""  # noqa: E501
    content = "A" * 1000
    limit = 200  # Use a larger limit to accommodate the notice
    result = maybe_truncate(content, truncate_after=limit)

    # Calculate expected head and tail
    notice_len = len(DEFAULT_TRUNCATE_NOTICE)
    available_chars = limit - notice_len
    half = available_chars // 2
    head_chars = half + (available_chars % 2)
    tail_chars = half
    expected = content[:head_chars] + DEFAULT_TRUNCATE_NOTICE + content[-tail_chars:]

    assert result == expected
    assert len(result) == limit


def test_maybe_truncate_custom_notice():
    """Test that maybe_truncate uses custom truncation notice with head-and-tail."""
    content = "A" * 100
    limit = 50
    custom_notice = " [TRUNCATED]"
    result = maybe_truncate(
        content, truncate_after=limit, truncate_notice=custom_notice
    )

    # Calculate expected head and tail with custom notice
    notice_len = len(custom_notice)
    available_chars = limit - notice_len
    half = available_chars // 2
    head_chars = half + (available_chars % 2)
    tail_chars = half
    expected = content[:head_chars] + custom_notice + content[-tail_chars:]

    assert result == expected
    assert len(result) == limit


def test_maybe_truncate_exact_limit():
    """Test that maybe_truncate doesn't truncate when exactly at limit."""
    content = "A" * 50
    limit = 50
    result = maybe_truncate(content, truncate_after=limit)
    assert result == content


def test_default_limits():
    """Test that default limits are reasonable values."""
    assert DEFAULT_TEXT_CONTENT_LIMIT == 50_000
    assert isinstance(DEFAULT_TRUNCATE_NOTICE, str)
    assert len(DEFAULT_TRUNCATE_NOTICE) > 0


def test_maybe_truncate_empty_string():
    """Test that maybe_truncate handles empty strings correctly."""
    result = maybe_truncate("", truncate_after=100)
    assert result == ""


def test_maybe_truncate_zero_limit():
    """Test that maybe_truncate handles zero limit correctly."""
    content = "test"
    result = maybe_truncate(content, truncate_after=0)
    # Zero limit is treated as no limit (same as None)
    assert result == content


def test_maybe_truncate_head_and_tail():
    """Test that maybe_truncate preserves head and tail content."""
    content = "BEGINNING" + "X" * 100 + "ENDING"
    limit = 50
    custom_notice = "[MIDDLE_TRUNCATED]"
    result = maybe_truncate(
        content, truncate_after=limit, truncate_notice=custom_notice
    )

    # Should preserve beginning and end
    assert result.startswith("BEGINNING")
    assert result.endswith("ENDING")
    assert custom_notice in result
    assert len(result) == limit


def test_maybe_truncate_notice_too_large():
    """Test behavior when truncation notice is larger than limit."""
    content = "A" * 100
    limit = 10
    large_notice = "X" * 20  # Larger than limit
    result = maybe_truncate(content, truncate_after=limit, truncate_notice=large_notice)

    # Should return truncated notice only
    assert result == large_notice[:limit]
    assert len(result) == limit


def test_maybe_truncate_file_deduplication(tmp_path):
    """Test that identical content creates the same file and doesn't duplicate."""
    content = "A" * 1000
    limit = 200
    save_dir = str(tmp_path)

    # First call should create a file
    result1 = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Second call with same content should reference the same file
    result2 = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Both results should be identical (same file referenced)
    assert result1 == result2
    assert "<response clipped>" in result1

    # Check that only one file was created
    files = list(tmp_path.glob("test_output_*.txt"))
    assert len(files) == 1

    # Verify the file contains the full content
    saved_file = files[0]
    assert saved_file.read_text() == content


def test_maybe_truncate_different_content_different_files(tmp_path):
    """Test that different content creates different files."""
    content1 = "A" * 1000
    content2 = "B" * 1000
    limit = 500
    save_dir = str(tmp_path)

    # First call with content1
    result1 = maybe_truncate(
        content1, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Second call with content2
    result2 = maybe_truncate(
        content2, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Results should be different (different files referenced)
    assert result1 != result2
    assert "<response clipped>" in result1
    assert "<response clipped>" in result2

    assert len(result1) == limit
    assert len(result2) == limit

    # Check that two files were created
    files = list(tmp_path.glob("test_output_*.txt"))
    assert len(files) == 2

    # Verify each file contains the correct content
    file_contents = {f.read_text() for f in files}
    assert file_contents == {content1, content2}


def test_maybe_truncate_same_content_different_prefix_different_files(tmp_path):
    """Test that same content with different prefixes creates different files."""
    content = "A" * 1000
    limit = 400
    save_dir = str(tmp_path)

    # First call with prefix "bash"
    result1 = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="bash"
    )

    # Second call with prefix "editor"
    result2 = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="editor"
    )

    # Results should be different (different files due to different prefixes)
    assert result1 != result2
    assert "<response clipped>" in result1
    assert "<response clipped>" in result2

    # Check that two files were created with different prefixes
    bash_files = list(tmp_path.glob("bash_output_*.txt"))
    editor_files = list(tmp_path.glob("editor_output_*.txt"))
    assert len(bash_files) == 1
    assert len(editor_files) == 1

    # Verify both files contain the same content
    assert bash_files[0].read_text() == content
    assert editor_files[0].read_text() == content


def test_maybe_truncate_hash_based_filename(tmp_path):
    """Test that filenames are based on content hash, not timestamp."""
    import hashlib

    content = (
        "Test content for hashing " * 20
    )  # Make content long enough to trigger truncation
    limit = 300  # Force truncation but allow space for truncate notice
    save_dir = str(tmp_path)

    # Calculate expected hash
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    expected_filename = f"test_output_{expected_hash}.txt"

    # Call maybe_truncate
    result = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Check that the expected file was created
    expected_file_path = tmp_path / expected_filename
    assert expected_file_path.exists()
    assert expected_file_path.read_text() == content

    # Check that the result references the correct file
    assert str(expected_file_path) in result


def test_maybe_truncate_persist_notice_exceeds_limit(tmp_path):
    """Test behavior when enhanced persist notice is longer than truncate limit."""
    content = "A" * 1000
    limit = 50  # Very small limit (enhanced notice is larger than 113 chars)
    save_dir = str(tmp_path)

    result = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Should truncate the base notice itself to fit within limit
    assert len(result) == limit
    # File is not created because base notice doesn't fit
    # (no point saving if we can't tell user about it)
    files = list(tmp_path.glob("test_output_*.txt"))
    assert len(files) == 0


def test_maybe_truncate_persist_head_char_moves_since_remaining_less_than_proposed_head(
    tmp_path,
):
    """
    Test behavior when notice fits initially, but the head char is
    shifted due to less than remaining space
    """
    content = "A" * 1000
    limit = 500  # Choose the limit around the middle will trigger the condition
    save_dir = str(tmp_path)

    result = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    assert len(result) == limit
    files = list(tmp_path.glob("test_output_*.txt"))
    assert len(files) == 1
    # Should not contain any tail content since head chars took all remaining space
    assert result.endswith("</NOTE>")


def test_maybe_truncate_persist_notice_leaves_minimal_room(tmp_path):
    """Test when persist notice leaves minimal room for head/tail content."""
    content = "BEGINNING" + "X" * 1000 + "ENDING"
    # Set limit such that persist notice leaves only a few chars for content
    limit = 300  # Adjust based on typical persist notice length
    save_dir = str(tmp_path)

    result = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    assert len(result) == limit
    # Should still try to include some head/tail if possible
    assert "test_output_" in result  # File path should be in result
    # Verify file was created
    files = list(tmp_path.glob("test_output_*.txt"))
    assert len(files) == 1
    assert files[0].read_text() == content


def test_maybe_truncate_line_number_accuracy(tmp_path):
    """Test that line number in persist notice is accurate."""
    import re

    # Create content with known line structure
    lines = [f"Line {i}\n" for i in range(1, 101)]
    content = "".join(lines)
    limit = 500  # Force truncation
    save_dir = str(tmp_path)

    result = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Extract line number from result
    match = re.search(r"line (\d+)", result)
    assert match is not None
    line_num = int(match.group(1))

    # Verify the line number is reasonable (should be somewhere in the middle)
    assert 1 <= line_num <= len(lines)


def test_maybe_truncate_short_content_with_persistence(tmp_path):
    """Test that short content doesn't get persisted unnecessarily."""
    content = "Short"
    limit = 100  # Much larger than content
    save_dir = str(tmp_path)

    result = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    # Should return original content without truncation or saving
    assert result == content
    # No file should be created since truncation didn't occur
    files = list(tmp_path.glob("test_output_*.txt"))
    assert len(files) == 0


def test_maybe_truncate_unicode_content_persistence(tmp_path):
    """Test persistence with Unicode content."""
    content = "Hello 世界 🌍 " * 100  # Mix of ASCII, Chinese, and emoji
    limit = 200
    save_dir = str(tmp_path)

    result = maybe_truncate(
        content, truncate_after=limit, save_dir=save_dir, tool_prefix="test"
    )

    assert len(result) == limit
    # Verify file was created and contains correct Unicode content
    files = list(tmp_path.glob("test_output_*.txt"))
    assert len(files) == 1
    saved_content = files[0].read_text(encoding="utf-8")
    assert saved_content == content
