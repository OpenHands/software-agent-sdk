"""Tests for PR review context functions in the PR review agent."""

import json
import os
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch


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
    _call_github_api,
    _fetch_with_fallback,
    _format_thread,
    format_review_context,
    get_pr_review_context,
    get_pr_reviews,
    get_review_threads_graphql,
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


class TestCallGithubApi:
    """Tests for _call_github_api function."""

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_successful_get_request(self, mock_urlopen):
        """Test successful GET request returns parsed JSON."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 123, "name": "test"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = _call_github_api("/repos/owner/repo")

        assert result == {"id": 123, "name": "test"}
        mock_urlopen.assert_called_once()

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_successful_post_request_with_data(self, mock_urlopen):
        """Test successful POST request with JSON data."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"success": true}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = _call_github_api(
            "https://api.github.com/graphql",
            method="POST",
            data={"query": "test query"},
        )

        assert result == {"success": True}

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_diff_request_returns_raw_text(self, mock_urlopen):
        """Test diff request returns raw text instead of JSON."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"diff --git a/file.py b/file.py\n+new line"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = _call_github_api(
            "/repos/owner/repo/pulls/1",
            accept="application/vnd.github.v3.diff",
        )

        assert "diff --git" in result
        assert "+new line" in result

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_http_error_raises_runtime_error(self, mock_urlopen):
        """Test HTTP error is converted to RuntimeError with details."""
        error_body = BytesIO(b"Not Found")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=error_body,
        )

        try:
            _call_github_api("/repos/owner/repo")
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "HTTP 404" in str(e)
            assert "Not Found" in str(e)

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_url_error_raises_runtime_error(self, mock_urlopen):
        """Test URL error (network issue) is converted to RuntimeError."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        try:
            _call_github_api("/repos/owner/repo")
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "Connection refused" in str(e)

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_invalid_json_raises_runtime_error(self, mock_urlopen):
        """Test invalid JSON response raises RuntimeError."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"not valid json {"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        try:
            _call_github_api("/repos/owner/repo")
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "invalid JSON" in str(e)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_token_raises_value_error(self):
        """Test missing GITHUB_TOKEN raises ValueError."""
        # Remove GITHUB_TOKEN if it exists
        os.environ.pop("GITHUB_TOKEN", None)

        try:
            _call_github_api("/repos/owner/repo")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "GITHUB_TOKEN" in str(e)

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_url_prefix_added_for_relative_path(self, mock_urlopen):
        """Test that relative paths get api.github.com prefix."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        _call_github_api("/repos/owner/repo")

        # Check that the URL was prefixed
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        assert request.full_url == "https://api.github.com/repos/owner/repo"

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
    @patch("urllib.request.urlopen")
    def test_full_url_not_modified(self, mock_urlopen):
        """Test that full URLs are not modified."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        _call_github_api("https://api.github.com/graphql")

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        assert request.full_url == "https://api.github.com/graphql"


class TestGetPrReviews:
    """Tests for get_pr_reviews function."""

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_fetches_reviews_successfully(self, mock_urlopen):
        """Test successful fetch of PR reviews via GraphQL."""
        # GraphQL with `last` returns newest first, so we put the newer review first
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {"hasPreviousPage": False, "startCursor": None},
                            "nodes": [
                                {
                                    "id": "2",
                                    "author": {"login": "reviewer2"},
                                    "body": "Please fix",
                                    "state": "CHANGES_REQUESTED",
                                    "submittedAt": "2024-01-02T00:00:00Z",
                                },
                                {
                                    "id": "1",
                                    "author": {"login": "reviewer1"},
                                    "body": "LGTM",
                                    "state": "APPROVED",
                                    "submittedAt": "2024-01-01T00:00:00Z",
                                },
                            ],
                        }
                    }
                }
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(graphql_response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_pr_reviews("123")

        # Results are reversed to chronological order (oldest first)
        assert len(result) == 2
        assert result[0]["state"] == "APPROVED"  # Older review first
        assert result[1]["state"] == "CHANGES_REQUESTED"  # Newer review second

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_uses_graphql_with_last_for_latest_reviews(self, mock_urlopen):
        """Test that GraphQL query uses 'last' to fetch latest reviews, not oldest."""
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {"hasPreviousPage": False, "startCursor": None},
                            "nodes": [],
                        }
                    }
                }
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(graphql_response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        get_pr_reviews("456")

        # Verify the GraphQL query uses 'last' (not 'first') to get latest reviews
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        request_body = request.data.decode("utf-8")
        assert "reviews(last:" in request_body
        assert "before: $cursor" in request_body
        assert "hasPreviousPage" in request_body
        assert "startCursor" in request_body
        # Ensure we're NOT using 'first' which would get oldest reviews
        assert "reviews(first:" not in request_body

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_multi_page_pagination_returns_chronological_order(self, mock_urlopen):
        """Test that multi-page pagination returns reviews in chronological order.

        When fetching 150 reviews across 2 pages using `last`, the API returns
        newest-first. The function should reverse them to chronological order
        (oldest first) for consistent display.
        """
        # Page 1: Most recent 100 reviews (newest first: review_150 down to review_51)
        page1_nodes = [
            {
                "id": f"review_{150 - i}",
                "author": {"login": f"user_{150 - i}"},
                "body": f"Review {150 - i}",
                "state": "COMMENTED",
                "submittedAt": f"2024-01-{150 - i:03d}T00:00:00Z",
            }
            for i in range(100)
        ]
        page1_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {
                                "hasPreviousPage": True,
                                "startCursor": "cursor_to_page2",
                            },
                            "nodes": page1_nodes,
                        }
                    }
                }
            }
        }

        # Page 2: Older 50 reviews (newest first: review_50 down to review_1)
        page2_nodes = [
            {
                "id": f"review_{50 - i}",
                "author": {"login": f"user_{50 - i}"},
                "body": f"Review {50 - i}",
                "state": "COMMENTED",
                "submittedAt": f"2024-01-{50 - i:03d}T00:00:00Z",
            }
            for i in range(50)
        ]
        page2_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": page2_nodes,
                        }
                    }
                }
            }
        }

        mock_response1 = MagicMock()
        mock_response1.read.return_value = json.dumps(page1_response).encode()
        mock_response1.__enter__ = MagicMock(return_value=mock_response1)
        mock_response1.__exit__ = MagicMock(return_value=False)

        mock_response2 = MagicMock()
        mock_response2.read.return_value = json.dumps(page2_response).encode()
        mock_response2.__enter__ = MagicMock(return_value=mock_response2)
        mock_response2.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [mock_response1, mock_response2]

        result = get_pr_reviews("123", max_reviews=200)

        # Verify we got all 150 reviews
        assert len(result) == 150
        assert mock_urlopen.call_count == 2

        # Verify chronological order: oldest (review_1) first, newest (review_150) last
        assert result[0]["id"] == "review_1"
        assert result[0]["body"] == "Review 1"
        assert result[-1]["id"] == "review_150"
        assert result[-1]["body"] == "Review 150"

        # Verify the entire sequence is in ascending order
        for i, review in enumerate(result):
            expected_id = f"review_{i + 1}"
            assert review["id"] == expected_id, (
                f"Expected {expected_id} at index {i}, got {review['id']}"
            )

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_exactly_100_reviews_single_page(self, mock_urlopen):
        """Test boundary condition: exactly 100 reviews in a single page."""
        # 100 reviews, newest first (review_100 down to review_1)
        nodes = [
            {
                "id": f"review_{100 - i}",
                "author": {"login": f"user_{100 - i}"},
                "body": f"Review {100 - i}",
                "state": "COMMENTED",
                "submittedAt": f"2024-01-{100 - i:03d}T00:00:00Z",
            }
            for i in range(100)
        ]
        response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": nodes,
                        }
                    }
                }
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_pr_reviews("123")

        # Verify all 100 reviews returned in chronological order
        assert len(result) == 100
        assert result[0]["id"] == "review_1"  # Oldest first
        assert result[-1]["id"] == "review_100"  # Newest last
        assert mock_urlopen.call_count == 1


class TestGetReviewThreadsGraphql:
    """Tests for get_review_threads_graphql function."""

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_fetches_threads_successfully(self, mock_urlopen):
        """Test successful fetch of review threads via GraphQL."""
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [
                                {
                                    "id": "thread1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "path": "file.py",
                                    "line": 10,
                                    "comments": {"nodes": []},
                                }
                            ],
                        }
                    }
                }
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(graphql_response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_review_threads_graphql("123")

        assert len(result) == 1
        assert result[0]["id"] == "thread1"
        assert result[0]["isResolved"] is False

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_uses_last_for_latest_threads(self, mock_urlopen):
        """Test that GraphQL query uses 'last' to fetch latest threads, not oldest."""
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [],
                        }
                    }
                }
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(graphql_response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        get_review_threads_graphql("123")

        # Verify the GraphQL query uses 'last' (not 'first') to get latest threads
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        request_body = request.data.decode("utf-8")
        assert "reviewThreads(last:" in request_body
        assert "before: $cursor" in request_body
        assert "hasPreviousPage" in request_body
        assert "startCursor" in request_body
        # Ensure we're NOT using 'first' which would get oldest threads
        assert "reviewThreads(first:" not in request_body

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_handles_pagination(self, mock_urlopen):
        """Test that pagination is handled correctly with hasPreviousPage."""
        # First page response (most recent threads)
        page1_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": True,
                                "startCursor": "cursor1",
                            },
                            "nodes": [{"id": "thread2", "isResolved": True}],
                        }
                    }
                }
            }
        }
        # Second page response (older threads)
        page2_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [{"id": "thread1", "isResolved": False}],
                        }
                    }
                }
            }
        }

        mock_response1 = MagicMock()
        mock_response1.read.return_value = json.dumps(page1_response).encode()
        mock_response1.__enter__ = MagicMock(return_value=mock_response1)
        mock_response1.__exit__ = MagicMock(return_value=False)

        mock_response2 = MagicMock()
        mock_response2.read.return_value = json.dumps(page2_response).encode()
        mock_response2.__enter__ = MagicMock(return_value=mock_response2)
        mock_response2.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [mock_response1, mock_response2]

        result = get_review_threads_graphql("123")

        # Results are reversed to chronological order (oldest first)
        assert len(result) == 2
        assert result[0]["id"] == "thread1"  # Older thread first
        assert result[1]["id"] == "thread2"  # Newer thread second
        assert mock_urlopen.call_count == 2

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_multi_page_pagination_returns_chronological_order(self, mock_urlopen):
        """Test that multi-page pagination returns threads in chronological order.

        When fetching 150 threads across 2 pages using `last`, the API returns
        newest-first. The function should reverse them to chronological order
        (oldest first) for consistent display.
        """
        # Page 1: Most recent 100 threads (newest first: thread_150 down to thread_51)
        page1_nodes = [
            {
                "id": f"thread_{150 - i}",
                "isResolved": (150 - i) % 2 == 0,
                "isOutdated": False,
                "path": f"file_{150 - i}.py",
                "line": 150 - i,
                "comments": {"nodes": []},
            }
            for i in range(100)
        ]
        page1_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": True,
                                "startCursor": "cursor_to_page2",
                            },
                            "nodes": page1_nodes,
                        }
                    }
                }
            }
        }

        # Page 2: Older 50 threads (newest first: thread_50 down to thread_1)
        page2_nodes = [
            {
                "id": f"thread_{50 - i}",
                "isResolved": (50 - i) % 2 == 0,
                "isOutdated": False,
                "path": f"file_{50 - i}.py",
                "line": 50 - i,
                "comments": {"nodes": []},
            }
            for i in range(50)
        ]
        page2_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": page2_nodes,
                        }
                    }
                }
            }
        }

        mock_response1 = MagicMock()
        mock_response1.read.return_value = json.dumps(page1_response).encode()
        mock_response1.__enter__ = MagicMock(return_value=mock_response1)
        mock_response1.__exit__ = MagicMock(return_value=False)

        mock_response2 = MagicMock()
        mock_response2.read.return_value = json.dumps(page2_response).encode()
        mock_response2.__enter__ = MagicMock(return_value=mock_response2)
        mock_response2.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [mock_response1, mock_response2]

        result = get_review_threads_graphql("123")

        # Verify we got all 150 threads
        assert len(result) == 150
        assert mock_urlopen.call_count == 2

        # Verify chronological order: oldest (thread_1) first, newest (thread_150) last
        assert result[0]["id"] == "thread_1"
        assert result[0]["path"] == "file_1.py"
        assert result[-1]["id"] == "thread_150"
        assert result[-1]["path"] == "file_150.py"

        # Verify the entire sequence is in ascending order
        for i, thread in enumerate(result):
            expected_id = f"thread_{i + 1}"
            assert thread["id"] == expected_id, (
                f"Expected {expected_id} at index {i}, got {thread['id']}"
            )

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_exactly_100_threads_single_page(self, mock_urlopen):
        """Test boundary condition: exactly 100 threads in a single page."""
        # 100 threads, newest first (thread_100 down to thread_1)
        nodes = [
            {
                "id": f"thread_{100 - i}",
                "isResolved": (100 - i) % 2 == 0,
                "isOutdated": False,
                "path": f"file_{100 - i}.py",
                "line": 100 - i,
                "comments": {"nodes": []},
            }
            for i in range(100)
        ]
        response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": nodes,
                        }
                    }
                }
            }
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_review_threads_graphql("123")

        # Verify all 100 threads returned in chronological order
        assert len(result) == 100
        assert result[0]["id"] == "thread_1"  # Oldest first
        assert result[-1]["id"] == "thread_100"  # Newest last
        assert mock_urlopen.call_count == 1

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_handles_graphql_errors(self, mock_urlopen):
        """Test that GraphQL errors are handled gracefully."""
        error_response = {
            "errors": [{"message": "Something went wrong"}],
            "data": None,
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(error_response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_review_threads_graphql("123")

        # Should return empty list on GraphQL error
        assert result == []

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_handles_missing_pr_data(self, mock_urlopen):
        """Test handling when PR data is missing from response."""
        response = {"data": {"repository": {"pullRequest": None}}}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_review_threads_graphql("123")

        assert result == []

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    @patch("time.time")
    def test_pagination_timeout(self, mock_time, mock_urlopen):
        """Test that pagination times out after MAX_PAGINATION_TIME."""
        # Simulate time passing beyond timeout
        mock_time.side_effect = [0, 0, 150]  # start, first check, second check (>120s)

        page_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": True,
                                "startCursor": "cursor1",
                            },
                            "nodes": [{"id": "thread1"}],
                        }
                    }
                }
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(page_response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_review_threads_graphql("123")

        # Should have fetched one page before timeout
        assert len(result) == 1
        assert mock_urlopen.call_count == 1

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_no_warning_when_all_threads_fetched(self, mock_urlopen):
        """Test no warning when all threads fetched (hasPreviousPage=False)."""
        # Response with exactly 100 threads but hasPreviousPage=False
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [{"id": f"thread{i}"} for i in range(100)],
                        }
                    }
                }
            }
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(graphql_response).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_review_threads_graphql("123")

        # Should return all 100 threads without warning
        # (warning only occurs if hasPreviousPage is True after we stop fetching)
        assert len(result) == 100


class TestGetPrReviewContext:
    """Tests for get_pr_review_context function."""

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_combines_reviews_and_threads(self, mock_urlopen):
        """Test that reviews and threads are combined into context."""
        # Mock GraphQL reviews response
        reviews_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [
                                {
                                    "id": "r1",
                                    "author": {"login": "reviewer1"},
                                    "body": "LGTM",
                                    "state": "APPROVED",
                                    "submittedAt": "2024-01-01T00:00:00Z",
                                }
                            ],
                        }
                    }
                }
            }
        }
        # Mock GraphQL threads response
        threads_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [
                                {
                                    "id": "t1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "path": "file.py",
                                    "line": 10,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "r1"},
                                                "body": "Fix this",
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        }

        mock_response1 = MagicMock()
        mock_response1.read.return_value = json.dumps(reviews_response).encode()
        mock_response1.__enter__ = MagicMock(return_value=mock_response1)
        mock_response1.__exit__ = MagicMock(return_value=False)

        mock_response2 = MagicMock()
        mock_response2.read.return_value = json.dumps(threads_response).encode()
        mock_response2.__enter__ = MagicMock(return_value=mock_response2)
        mock_response2.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [mock_response1, mock_response2]

        result = get_pr_review_context("123")

        assert "reviewer1" in result
        assert "APPROVED" in result
        assert "file.py" in result
        assert "Fix this" in result

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_handles_api_failure_gracefully(self, mock_urlopen):
        """Test that API failures are handled gracefully."""
        error_body = BytesIO(b"Internal Server Error")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=500,
            msg="Internal Server Error",
            hdrs=None,  # type: ignore[arg-type]
            fp=error_body,
        )

        result = get_pr_review_context("123")

        # Should return empty string on failure
        assert result == ""

    @patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "REPO_NAME": "owner/repo"})
    @patch("urllib.request.urlopen")
    def test_returns_empty_when_no_reviews_or_threads(self, mock_urlopen):
        """Test returns empty string when no reviews or threads exist."""
        # Empty reviews (GraphQL format)
        reviews_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviews": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [],
                        }
                    }
                }
            }
        }
        mock_response1 = MagicMock()
        mock_response1.read.return_value = json.dumps(reviews_response).encode()
        mock_response1.__enter__ = MagicMock(return_value=mock_response1)
        mock_response1.__exit__ = MagicMock(return_value=False)

        # Empty threads
        threads_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasPreviousPage": False,
                                "startCursor": None,
                            },
                            "nodes": [],
                        }
                    }
                }
            }
        }
        mock_response2 = MagicMock()
        mock_response2.read.return_value = json.dumps(threads_response).encode()
        mock_response2.__enter__ = MagicMock(return_value=mock_response2)
        mock_response2.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [mock_response1, mock_response2]

        result = get_pr_review_context("123")

        assert result == ""
