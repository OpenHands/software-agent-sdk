"""Tests for PR review context functions in the PR review agent."""

import sys
from pathlib import Path


# Import the PR review functions
pr_review_path = (
    Path(__file__).parent.parent.parent
    / "examples"
    / "03_github_workflows"
    / "02_pr_review"
)
sys.path.insert(0, str(pr_review_path))
from agent_script import (  # noqa: E402  # type: ignore[import-not-found]
    MAX_REVIEW_CONTEXT,
    _fetch_with_fallback,
    _format_thread,
    format_review_context,
)


class TestFormatReviewContext:
    """Tests for format_review_context function."""

    def test_empty_reviews_and_threads(self):
        """Test with no reviews and no threads."""
        result = format_review_context([], [])
        assert result == ""

    def test_single_approved_review(self):
        """Test formatting a single approved review."""
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "APPROVED",
                "body": "LGTM!",
            }
        ]
        result = format_review_context(reviews, [])
        assert "### Previous Reviews" in result
        assert "✅ **reviewer1** (APPROVED)" in result
        assert "LGTM!" in result

    def test_changes_requested_review(self):
        """Test formatting a changes requested review."""
        reviews = [
            {
                "user": {"login": "reviewer2"},
                "state": "CHANGES_REQUESTED",
                "body": "Please fix the bug",
            }
        ]
        result = format_review_context(reviews, [])
        assert "🔴 **reviewer2** (CHANGES_REQUESTED)" in result
        assert "Please fix the bug" in result

    def test_commented_review(self):
        """Test formatting a commented review."""
        reviews = [
            {
                "user": {"login": "reviewer3"},
                "state": "COMMENTED",
                "body": "A few suggestions",
            }
        ]
        result = format_review_context(reviews, [])
        assert "💬 **reviewer3** (COMMENTED)" in result

    def test_dismissed_review(self):
        """Test formatting a dismissed review."""
        reviews = [
            {
                "user": {"login": "reviewer4"},
                "state": "DISMISSED",
                "body": "",
            }
        ]
        result = format_review_context(reviews, [])
        assert "❌ **reviewer4** (DISMISSED)" in result

    def test_pending_review(self):
        """Test formatting a pending review."""
        reviews = [
            {
                "user": {"login": "reviewer5"},
                "state": "PENDING",
                "body": "Draft review",
            }
        ]
        result = format_review_context(reviews, [])
        assert "⏳ **reviewer5** (PENDING)" in result

    def test_unknown_state_review(self):
        """Test formatting a review with unknown state."""
        reviews = [
            {
                "user": {"login": "reviewer6"},
                "state": "UNKNOWN_STATE",
                "body": "",
            }
        ]
        result = format_review_context(reviews, [])
        assert "❓ **reviewer6** (UNKNOWN_STATE)" in result

    def test_review_with_empty_body(self):
        """Test review with empty body doesn't add extra lines."""
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "APPROVED",
                "body": "",
            }
        ]
        result = format_review_context(reviews, [])
        assert "✅ **reviewer1** (APPROVED)" in result
        # Should not have indented body
        assert "  >" not in result

    def test_review_body_truncation(self):
        """Test that long review bodies are truncated."""
        long_body = "x" * 600
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "COMMENTED",
                "body": long_body,
            }
        ]
        result = format_review_context(reviews, [])
        # Body should be truncated to 500 chars + "..."
        assert "..." in result
        assert len(long_body) > 500

    def test_multiple_reviews(self):
        """Test formatting multiple reviews."""
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "APPROVED",
                "body": "LGTM!",
            },
            {
                "user": {"login": "reviewer2"},
                "state": "CHANGES_REQUESTED",
                "body": "Please fix",
            },
        ]
        result = format_review_context(reviews, [])
        assert "reviewer1" in result
        assert "reviewer2" in result
        assert "APPROVED" in result
        assert "CHANGES_REQUESTED" in result

    def test_unresolved_thread(self):
        """Test formatting an unresolved thread."""
        threads = [
            {
                "path": "src/module.py",
                "line": 42,
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "reviewer1"},
                            "body": "This needs fixing",
                        }
                    ]
                },
            }
        ]
        result = format_review_context([], threads)
        assert "### Unresolved Review Threads" in result
        assert "src/module.py:42" in result
        assert "⚠️ UNRESOLVED" in result
        assert "reviewer1" in result
        assert "This needs fixing" in result

    def test_resolved_thread(self):
        """Test formatting a resolved thread."""
        threads = [
            {
                "path": "src/module.py",
                "line": 10,
                "isResolved": True,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "reviewer1"},
                            "body": "Fixed now",
                        }
                    ]
                },
            }
        ]
        result = format_review_context([], threads)
        assert "### Resolved Review Threads" in result
        assert "✅ RESOLVED" in result

    def test_outdated_thread(self):
        """Test formatting an outdated thread."""
        threads = [
            {
                "path": "src/module.py",
                "line": 10,
                "isResolved": False,
                "isOutdated": True,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "reviewer1"},
                            "body": "Old comment",
                        }
                    ]
                },
            }
        ]
        result = format_review_context([], threads)
        assert "(outdated)" in result

    def test_thread_without_line_number(self):
        """Test formatting a thread without line number."""
        threads = [
            {
                "path": "src/module.py",
                "line": None,
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "reviewer1"},
                            "body": "General comment",
                        }
                    ]
                },
            }
        ]
        result = format_review_context([], threads)
        assert "src/module.py" in result
        # Should not have :None
        assert ":None" not in result

    def test_mixed_resolved_unresolved_threads(self):
        """Test formatting both resolved and unresolved threads."""
        threads = [
            {
                "path": "file1.py",
                "line": 10,
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [{"author": {"login": "r1"}, "body": "Unresolved"}]
                },
            },
            {
                "path": "file2.py",
                "line": 20,
                "isResolved": True,
                "isOutdated": False,
                "comments": {
                    "nodes": [{"author": {"login": "r2"}, "body": "Resolved"}]
                },
            },
        ]
        result = format_review_context([], threads)
        assert "### Unresolved Review Threads" in result
        assert "### Resolved Review Threads" in result

    def test_reviews_and_threads_combined(self):
        """Test formatting both reviews and threads."""
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "COMMENTED",
                "body": "See inline comments",
            }
        ]
        threads = [
            {
                "path": "src/module.py",
                "line": 42,
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "reviewer1"},
                            "body": "Fix this",
                        }
                    ]
                },
            }
        ]
        result = format_review_context(reviews, threads)
        assert "### Previous Reviews" in result
        assert "### Unresolved Review Threads" in result

    def test_truncation_when_exceeding_max_size(self):
        """Test that context is truncated when exceeding max size."""
        # Create many reviews to exceed max size
        reviews = [
            {
                "user": {"login": f"reviewer{i}"},
                "state": "COMMENTED",
                "body": "x" * 500,
            }
            for i in range(100)
        ]
        result = format_review_context(reviews, [], max_size=1000)
        assert len(result) <= 1100  # Allow some buffer for truncation message
        assert "truncated" in result

    def test_missing_user_field(self):
        """Test handling of missing user field."""
        reviews = [
            {
                "user": {},
                "state": "APPROVED",
                "body": "LGTM",
            }
        ]
        result = format_review_context(reviews, [])
        assert "unknown" in result

    def test_missing_author_in_comment(self):
        """Test handling of missing author in thread comment."""
        threads = [
            {
                "path": "file.py",
                "line": 10,
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "author": {},
                            "body": "Comment without author",
                        }
                    ]
                },
            }
        ]
        result = format_review_context([], threads)
        assert "unknown" in result

    def test_null_user_field(self):
        """Test handling of null user field."""
        reviews = [
            {
                "user": None,
                "state": "APPROVED",
                "body": "LGTM",
            }
        ]
        result = format_review_context(reviews, [])
        # Should handle None gracefully
        assert "APPROVED" in result


class TestFormatThread:
    """Tests for _format_thread function."""

    def test_basic_thread(self):
        """Test formatting a basic thread."""
        thread = {
            "path": "src/main.py",
            "line": 100,
            "isResolved": False,
            "isOutdated": False,
            "comments": {
                "nodes": [
                    {
                        "author": {"login": "reviewer1"},
                        "body": "Please fix this",
                    }
                ]
            },
        }
        lines = _format_thread(thread)
        result = "\n".join(lines)
        assert "src/main.py:100" in result
        assert "⚠️ UNRESOLVED" in result
        assert "reviewer1" in result
        assert "Please fix this" in result

    def test_resolved_thread(self):
        """Test formatting a resolved thread."""
        thread = {
            "path": "src/main.py",
            "line": 50,
            "isResolved": True,
            "isOutdated": False,
            "comments": {"nodes": []},
        }
        lines = _format_thread(thread)
        result = "\n".join(lines)
        assert "✅ RESOLVED" in result

    def test_outdated_thread(self):
        """Test formatting an outdated thread."""
        thread = {
            "path": "src/main.py",
            "line": 50,
            "isResolved": False,
            "isOutdated": True,
            "comments": {"nodes": []},
        }
        lines = _format_thread(thread)
        result = "\n".join(lines)
        assert "(outdated)" in result

    def test_thread_comment_truncation(self):
        """Test that long comments in threads are truncated."""
        long_body = "y" * 400
        thread = {
            "path": "src/main.py",
            "line": 50,
            "isResolved": False,
            "isOutdated": False,
            "comments": {
                "nodes": [
                    {
                        "author": {"login": "reviewer1"},
                        "body": long_body,
                    }
                ]
            },
        }
        lines = _format_thread(thread)
        result = "\n".join(lines)
        # Body should be truncated to 300 chars + "..."
        assert "..." in result

    def test_thread_with_multiple_comments(self):
        """Test thread with multiple comments."""
        thread = {
            "path": "src/main.py",
            "line": 50,
            "isResolved": False,
            "isOutdated": False,
            "comments": {
                "nodes": [
                    {"author": {"login": "reviewer1"}, "body": "First comment"},
                    {"author": {"login": "reviewer2"}, "body": "Second comment"},
                ]
            },
        }
        lines = _format_thread(thread)
        result = "\n".join(lines)
        assert "reviewer1" in result
        assert "reviewer2" in result
        assert "First comment" in result
        assert "Second comment" in result

    def test_thread_with_empty_comment_body(self):
        """Test thread with empty comment body."""
        thread = {
            "path": "src/main.py",
            "line": 50,
            "isResolved": False,
            "isOutdated": False,
            "comments": {
                "nodes": [
                    {"author": {"login": "reviewer1"}, "body": ""},
                ]
            },
        }
        lines = _format_thread(thread)
        result = "\n".join(lines)
        # Empty body should not add author line
        assert "reviewer1" not in result

    def test_thread_missing_path(self):
        """Test thread with missing path."""
        thread = {
            "line": 50,
            "isResolved": False,
            "isOutdated": False,
            "comments": {"nodes": []},
        }
        lines = _format_thread(thread)
        result = "\n".join(lines)
        assert "unknown" in result


class TestFetchWithFallback:
    """Tests for _fetch_with_fallback function."""

    def test_successful_fetch(self):
        """Test successful data fetch."""
        mock_data = [{"id": 1}, {"id": 2}]
        result = _fetch_with_fallback("items", lambda: mock_data)
        assert result == mock_data

    def test_fetch_with_exception(self):
        """Test fetch that raises an exception."""

        def failing_fetch():
            raise RuntimeError("API error")

        result = _fetch_with_fallback("items", failing_fetch)
        assert result == []

    def test_fetch_with_http_error(self):
        """Test fetch that raises HTTP error."""

        def http_error_fetch():
            raise Exception("HTTP 403 Forbidden")

        result = _fetch_with_fallback("items", http_error_fetch)
        assert result == []

    def test_fetch_with_timeout(self):
        """Test fetch that times out."""

        def timeout_fetch():
            raise TimeoutError("Connection timed out")

        result = _fetch_with_fallback("items", timeout_fetch)
        assert result == []


class TestMaxReviewContextConstant:
    """Tests for MAX_REVIEW_CONTEXT constant."""

    def test_max_review_context_value(self):
        """Test that MAX_REVIEW_CONTEXT has expected value."""
        assert MAX_REVIEW_CONTEXT == 30000

    def test_truncation_at_max_size(self):
        """Test truncation happens at max_size boundary."""
        # Create content that exceeds max size
        reviews = [
            {
                "user": {"login": "reviewer"},
                "state": "COMMENTED",
                "body": "x" * 500,
            }
            for _ in range(100)
        ]
        result = format_review_context(reviews, [], max_size=MAX_REVIEW_CONTEXT)
        # Result should be truncated
        assert "truncated" in result or len(result) <= MAX_REVIEW_CONTEXT + 100


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_none_values_in_review(self):
        """Test handling of None values in review data."""
        reviews = [
            {
                "user": None,
                "state": None,
                "body": None,
            }
        ]
        # Should not raise an exception
        result = format_review_context(reviews, [])
        assert isinstance(result, str)

    def test_empty_comments_nodes(self):
        """Test thread with empty comments nodes list."""
        threads = [
            {
                "path": "file.py",
                "line": 10,
                "isResolved": False,
                "isOutdated": False,
                "comments": {"nodes": []},
            }
        ]
        result = format_review_context([], threads)
        assert "file.py" in result

    def test_missing_comments_key(self):
        """Test thread with missing comments key."""
        threads = [
            {
                "path": "file.py",
                "line": 10,
                "isResolved": False,
                "isOutdated": False,
            }
        ]
        result = format_review_context([], threads)
        assert "file.py" in result

    def test_multiline_review_body(self):
        """Test review with multiline body."""
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "COMMENTED",
                "body": "Line 1\nLine 2\nLine 3",
            }
        ]
        result = format_review_context(reviews, [])
        # Each line should be indented
        assert "  > Line 1" in result
        assert "  > Line 2" in result
        assert "  > Line 3" in result

    def test_multiline_thread_comment(self):
        """Test thread comment with multiline body."""
        threads = [
            {
                "path": "file.py",
                "line": 10,
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "reviewer1"},
                            "body": "Line A\nLine B",
                        }
                    ]
                },
            }
        ]
        result = format_review_context([], threads)
        assert "  > Line A" in result
        assert "  > Line B" in result

    def test_special_characters_in_body(self):
        """Test handling of special characters in review body."""
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "COMMENTED",
                "body": "Code: `foo()` and **bold** and _italic_",
            }
        ]
        result = format_review_context(reviews, [])
        assert "`foo()`" in result
        assert "**bold**" in result

    def test_unicode_in_review(self):
        """Test handling of unicode characters."""
        reviews = [
            {
                "user": {"login": "reviewer1"},
                "state": "APPROVED",
                "body": "Great work! 🎉 日本語テスト",
            }
        ]
        result = format_review_context(reviews, [])
        assert "🎉" in result
        assert "日本語テスト" in result
