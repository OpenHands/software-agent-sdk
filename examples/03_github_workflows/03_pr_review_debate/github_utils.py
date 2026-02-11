"""
GitHub API utilities for PR review workflows.

This module provides functions for interacting with the GitHub API,
including fetching PR diffs, reviews, and review threads.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from openhands.sdk.git.utils import run_git_command
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# Maximum total diff size
MAX_TOTAL_DIFF = 100000
# Maximum size for review context to avoid overwhelming the prompt
MAX_REVIEW_CONTEXT = 30000
# Maximum time (seconds) for GraphQL pagination
MAX_PAGINATION_TIME = 120


def get_required_env(name: str) -> str:
    """Get a required environment variable, raising if not set."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


def call_github_api(
    url: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    accept: str = "application/vnd.github+json",
    token: str | None = None,
) -> Any:
    """Make a GitHub API request (REST or GraphQL).

    Args:
        url: Full API URL or path (will be prefixed with api.github.com if needed)
        method: HTTP method (GET, POST, etc.)
        data: JSON data to send (for POST/PUT requests, including GraphQL queries)
        accept: Accept header value
        token: GitHub token (defaults to GITHUB_TOKEN env var)

    Returns:
        Parsed JSON response or raw text for diff requests
    """
    if token is None:
        token = get_required_env("GITHUB_TOKEN")

    if not url.startswith("http"):
        url = f"https://api.github.com{url}"

    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", accept)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    if data:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(data).encode("utf-8")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw_data = response.read()
            if "diff" in accept:
                return raw_data.decode("utf-8", errors="replace")
            return json.loads(raw_data.decode("utf-8"))
    except urllib.error.HTTPError as e:
        details = (e.read() or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub API request failed: HTTP {e.code} {e.reason}. {details}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub API request failed: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GitHub API returned invalid JSON: {e}") from e


def get_pr_reviews(
    pr_number: str, repo_name: str | None = None
) -> list[dict[str, Any]]:
    """Fetch all reviews for a PR.

    Args:
        pr_number: The PR number
        repo_name: Repository name in format owner/repo (defaults to REPO_NAME env var)

    Returns:
        A list of review objects containing:
        - id: Review ID
        - user: Author information
        - body: Review body text
        - state: APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED, PENDING
        - submitted_at: When the review was submitted
    """
    if repo_name is None:
        repo_name = get_required_env("REPO_NAME")
    url = f"/repos/{repo_name}/pulls/{pr_number}/reviews"
    return call_github_api(url)


def get_review_threads_graphql(
    pr_number: str, repo_name: str | None = None
) -> list[dict[str, Any]]:
    """Fetch review threads with resolution status using GraphQL API.

    Args:
        pr_number: The PR number
        repo_name: Repository name in format owner/repo (defaults to REPO_NAME env var)

    Returns:
        A list of thread objects containing:
        - id: Thread ID
        - isResolved: Whether the thread is resolved
        - isOutdated: Whether the thread is outdated
        - path: File path
        - line: Line number
        - comments: List of comments in the thread
    """
    if repo_name is None:
        repo_name = get_required_env("REPO_NAME")
    owner, repo = repo_name.split("/")

    query = """
    query($owner: String!, $repo: String!, $pr_number: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr_number) {
          reviewThreads(first: 100, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              isResolved
              isOutdated
              path
              line
              comments(first: 50) {
                nodes {
                  id
                  author {
                    login
                  }
                  body
                  createdAt
                }
              }
            }
          }
        }
      }
    }
    """

    threads: list[dict[str, Any]] = []
    cursor = None
    start_time = time.time()
    page_count = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed > MAX_PAGINATION_TIME:
            logger.warning(
                f"GraphQL pagination timeout after {elapsed:.1f}s, "
                f"fetched {len(threads)} threads across {page_count} pages"
            )
            break

        variables = {
            "owner": owner,
            "repo": repo,
            "pr_number": int(pr_number),
            "cursor": cursor,
        }

        result = call_github_api(
            "https://api.github.com/graphql",
            method="POST",
            data={"query": query, "variables": variables},
        )

        if "errors" in result:
            logger.warning(f"GraphQL errors: {result['errors']}")
            break

        pr_data = result.get("data", {}).get("repository", {}).get("pullRequest")
        if not pr_data:
            break

        review_threads = pr_data.get("reviewThreads", {})
        nodes = review_threads.get("nodes", [])
        threads.extend(nodes)
        page_count += 1

        logger.debug(
            f"Fetched page {page_count} with {len(nodes)} threads "
            f"(total: {len(threads)})"
        )

        page_info = review_threads.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    if len(threads) >= 100 and page_count == 1:
        logger.warning(
            f"Fetched {len(threads)} review threads (at page limit). "
            "Some threads may be omitted for PRs with extensive review history."
        )

    return threads


def _format_thread(thread: dict[str, Any]) -> list[str]:
    """Format a single review thread.

    Args:
        thread: Thread object from GraphQL

    Returns:
        List of formatted lines
    """
    lines: list[str] = []
    path = thread.get("path", "unknown")
    line_num = thread.get("line")
    is_outdated = thread.get("isOutdated", False)
    is_resolved = thread.get("isResolved", False)

    status = "✅ RESOLVED" if is_resolved else "⚠️ UNRESOLVED"
    outdated = " (outdated)" if is_outdated else ""
    location = f"{path}"
    if line_num:
        location += f":{line_num}"

    lines.append(f"**{location}**{outdated} - {status}")

    comments_data = thread.get("comments") or {}
    comments = comments_data.get("nodes") or []
    for comment in comments:
        author_data = comment.get("author") or {}
        author = author_data.get("login", "unknown")
        body = (comment.get("body") or "").strip()
        if body:
            body_preview = body[:300] + "..." if len(body) > 300 else body
            indented = "\n".join(f"  > {line}" for line in body_preview.split("\n"))
            lines.append(f"  - **{author}**:")
            lines.append(indented)

    lines.append("")
    return lines


def format_review_context(
    reviews: list[dict[str, Any]],
    threads: list[dict[str, Any]],
    max_size: int = MAX_REVIEW_CONTEXT,
) -> str:
    """Format review history into a context string.

    Args:
        reviews: List of review objects from get_pr_reviews()
        threads: List of thread objects from get_review_threads_graphql()
        max_size: Maximum size of the formatted context

    Returns:
        Formatted markdown string with review history
    """
    if not reviews and not threads:
        return ""

    sections: list[str] = []
    current_size = 0

    def _add_section(section: str) -> bool:
        nonlocal current_size
        section_size = len(section) + 1
        if current_size + section_size > max_size:
            return False
        sections.append(section)
        current_size += section_size
        return True

    if reviews:
        review_lines: list[str] = ["### Previous Reviews\n"]
        for review in reviews:
            user_data = review.get("user") or {}
            user = user_data.get("login", "unknown")
            state = review.get("state") or "UNKNOWN"
            body = (review.get("body") or "").strip()

            state_emoji = {
                "APPROVED": "✅",
                "CHANGES_REQUESTED": "🔴",
                "COMMENTED": "💬",
                "DISMISSED": "❌",
                "PENDING": "⏳",
            }.get(state, "❓")

            review_lines.append(f"- {state_emoji} **{user}** ({state})")
            if body:
                body_preview = body[:500] + "..." if len(body) > 500 else body
                indented = "\n".join(f"  > {line}" for line in body_preview.split("\n"))
                review_lines.append(indented)
            review_lines.append("")

        review_section = "\n".join(review_lines)
        if not _add_section(review_section):
            return (
                f"... [review context truncated, "
                f"content exceeds {max_size:,} chars] ..."
            )

    if threads:
        resolved_threads = [t for t in threads if t.get("isResolved")]
        unresolved_threads = [t for t in threads if not t.get("isResolved")]

        if unresolved_threads:
            header = (
                "### Unresolved Review Threads\n\n"
                "*These threads have not been resolved and may need attention:*\n"
            )
            if not _add_section(header):
                count = len(unresolved_threads)
                sections.append(
                    f"\n... [truncated, {count} unresolved threads omitted] ..."
                )
            else:
                threads_added = 0
                for thread in unresolved_threads:
                    thread_lines = _format_thread(thread)
                    thread_section = "\n".join(thread_lines)
                    if not _add_section(thread_section):
                        remaining = len(unresolved_threads) - threads_added
                        sections.append(
                            f"\n... [truncated, {remaining} unresolved "
                            "threads omitted] ..."
                        )
                        break
                    threads_added += 1

        if resolved_threads and current_size < max_size:
            header = (
                "### Resolved Review Threads\n\n"
                "*These threads have been resolved but provide context:*\n"
            )
            if _add_section(header):
                threads_added = 0
                for thread in resolved_threads:
                    thread_lines = _format_thread(thread)
                    thread_section = "\n".join(thread_lines)
                    if not _add_section(thread_section):
                        remaining = len(resolved_threads) - threads_added
                        sections.append(
                            f"\n... [truncated, {remaining} resolved "
                            "threads omitted] ..."
                        )
                        break
                    threads_added += 1

    return "\n".join(sections)


def get_pr_diff_via_api(pr_number: str, repo_name: str | None = None) -> str:
    """Fetch the PR diff from GitHub API.

    Args:
        pr_number: The PR number
        repo_name: Repository name (defaults to REPO_NAME env var)

    Returns:
        The diff text
    """
    if repo_name is None:
        repo_name = get_required_env("REPO_NAME")
    token = get_required_env("GITHUB_TOKEN")

    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github.v3.diff")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as e:
        details = (e.read() or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub diff API request failed: HTTP {e.code} {e.reason}. {details}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub diff API request failed: {e.reason}") from e

    return data.decode("utf-8", errors="replace")


def truncate_text(text: str, max_size: int = MAX_TOTAL_DIFF) -> str:
    """Truncate text to max_size with a note about truncation."""
    if len(text) <= max_size:
        return text

    total_chars = len(text)
    return (
        text[:max_size]
        + f"\n\n... [truncated, {total_chars:,} chars total, "
        + f"showing first {max_size:,}] ..."
    )


def get_truncated_pr_diff(pr_number: str | None = None) -> str:
    """Get the PR diff with truncation.

    Args:
        pr_number: The PR number (defaults to PR_NUMBER env var)

    Returns:
        The truncated diff text
    """
    if pr_number is None:
        pr_number = get_required_env("PR_NUMBER")
    diff_text = get_pr_diff_via_api(pr_number)
    return truncate_text(diff_text)


def get_head_commit_sha(repo_dir: Path | None = None) -> str:
    """Get the SHA of the HEAD commit.

    Args:
        repo_dir: Path to the repository (defaults to cwd)

    Returns:
        The commit SHA
    """
    if repo_dir is None:
        repo_dir = Path.cwd()
    return run_git_command(["git", "rev-parse", "HEAD"], repo_dir).strip()


def get_pr_review_context(pr_number: str) -> str:
    """Get all review context for a PR.

    Args:
        pr_number: The PR number

    Returns:
        Formatted review context string, or empty string if no context
    """
    try:
        reviews = get_pr_reviews(pr_number)
        logger.info(f"Fetched {len(reviews)} reviews")
    except Exception as e:
        logger.warning(f"Failed to fetch reviews: {e}")
        reviews = []

    try:
        threads = get_review_threads_graphql(pr_number)
        logger.info(f"Fetched {len(threads)} review threads")
    except Exception as e:
        logger.warning(f"Failed to fetch review threads: {e}")
        threads = []

    return format_review_context(reviews, threads)
