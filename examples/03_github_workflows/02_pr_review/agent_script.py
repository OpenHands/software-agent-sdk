#!/usr/bin/env python3
"""
Example: PR Review Agent

This script runs OpenHands agent to review a pull request and provide
fine-grained review comments. The agent has full repository access and uses
bash commands to analyze changes in context and post detailed review feedback
directly via `gh` or the GitHub API.

This example demonstrates how to use skills for code review:
- `/codereview` - Standard code review skill
- `/codereview-roasted` - Linus Torvalds style brutally honest review

The agent posts inline review comments on specific lines of code using the
GitHub API, rather than posting one giant comment under the PR.

The agent also considers previous review context including:
- Existing review comments and their resolution status
- Previous review decisions (APPROVED, CHANGES_REQUESTED, etc.)
- Review threads (resolved and unresolved)

Designed for use with GitHub Actions workflows triggered by PR labels.

Environment Variables:
    LLM_API_KEY: API key for the LLM (required)
    LLM_MODEL: Language model to use (default: anthropic/claude-sonnet-4-5-20250929)
    LLM_BASE_URL: Optional base URL for LLM API
    GITHUB_TOKEN: GitHub token for API access (required)
    PR_NUMBER: Pull request number (required)
    PR_TITLE: Pull request title (required)
    PR_BODY: Pull request body (optional)
    PR_BASE_BRANCH: Base branch name (required)
    PR_HEAD_BRANCH: Head branch name (required)
    REPO_NAME: Repository name in format owner/repo (required)
    REVIEW_STYLE: Review style ('standard' or 'roasted', default: 'standard')

For setup instructions, usage examples, and GitHub Actions integration,
see README.md in this directory.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lmnr import Laminar

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.context.skills import load_project_skills
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.git.utils import run_git_command
from openhands.tools.preset.default import get_default_condenser, get_default_tools


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import format_prompt  # noqa: E402


logger = get_logger(__name__)

# Maximum total diff size
MAX_TOTAL_DIFF = 100000
# Maximum size for review context to avoid overwhelming the prompt
# Keeps context under ~7500 tokens (assuming ~4 chars/token average)
MAX_REVIEW_CONTEXT = 30000
# Maximum time (seconds) for GraphQL pagination to prevent hanging on slow APIs
MAX_PAGINATION_TIME = 120


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


def _call_github_api(
    url: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    accept: str = "application/vnd.github+json",
) -> Any:
    """Make a GitHub API request (REST or GraphQL).

    This function handles both REST API calls and GraphQL queries (via the /graphql
    endpoint). The function name reflects this dual purpose.

    Args:
        url: Full API URL or path (will be prefixed with api.github.com if needed)
        method: HTTP method (GET, POST, etc.)
        data: JSON data to send (for POST/PUT requests, including GraphQL queries)
        accept: Accept header value

    Returns:
        Parsed JSON response or raw text for diff requests
    """
    token = _get_required_env("GITHUB_TOKEN")

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


def get_pr_reviews(pr_number: str, max_reviews: int = 100) -> list[dict[str, Any]]:
    """Fetch the latest reviews for a PR using GraphQL.

    Uses GraphQL with `last` to fetch the most recent reviews directly,
    avoiding the need to paginate through all reviews from oldest to newest.

    Args:
        pr_number: The PR number
        max_reviews: Maximum number of reviews to return (default: 100)

    Returns a list of review objects containing:
    - id: Review ID
    - user: Author information
    - body: Review body text
    - state: APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED, PENDING
    - submitted_at: When the review was submitted
    """
    repo = _get_required_env("REPO_NAME")
    owner, repo_name = repo.split("/")

    # Use GraphQL to fetch the latest reviews directly
    # `last: N` fetches the N most recent items
    query = """
    query(
      $owner: String!
      $repo: String!
      $pr_number: Int!
      $count: Int!
      $cursor: String
    ) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr_number) {
          reviews(last: $count, before: $cursor) {
            pageInfo {
              hasPreviousPage
              startCursor
            }
            nodes {
              id
              author {
                login
              }
              body
              state
              submittedAt
            }
          }
        }
      }
    }
    """

    all_reviews: list[dict[str, Any]] = []
    cursor = None
    start_time = time.time()
    page_count = 0

    while len(all_reviews) < max_reviews:
        # Check for pagination timeout
        elapsed = time.time() - start_time
        if elapsed > MAX_PAGINATION_TIME:
            logger.warning(
                f"Reviews pagination timeout after {elapsed:.1f}s, "
                f"fetched {len(all_reviews)} reviews across {page_count} pages"
            )
            break

        # Fetch up to remaining needed reviews
        remaining = max_reviews - len(all_reviews)
        fetch_count = min(remaining, 100)  # GraphQL max is 100 per request

        variables = {
            "owner": owner,
            "repo": repo_name,
            "pr_number": int(pr_number),
            "count": fetch_count,
            "cursor": cursor,
        }

        result = _call_github_api(
            "https://api.github.com/graphql",
            method="POST",
            data={"query": query, "variables": variables},
        )

        if "errors" in result:
            logger.warning(f"GraphQL errors fetching reviews: {result['errors']}")
            break

        pr_data = result.get("data", {}).get("repository", {}).get("pullRequest")
        if not pr_data:
            break

        reviews_data = pr_data.get("reviews", {})
        nodes = reviews_data.get("nodes", [])
        page_count += 1

        if not nodes:
            break

        # Convert GraphQL format to REST-like format for compatibility
        for node in nodes:
            author = node.get("author") or {}
            all_reviews.append(
                {
                    "id": node.get("id"),
                    "user": {"login": author.get("login", "unknown")},
                    "body": node.get("body", ""),
                    "state": node.get("state", "UNKNOWN"),
                    "submitted_at": node.get("submittedAt"),
                }
            )

        logger.debug(
            f"Fetched page {page_count} with {len(nodes)} reviews "
            f"(total: {len(all_reviews)})"
        )

        page_info = reviews_data.get("pageInfo", {})
        if not page_info.get("hasPreviousPage"):
            break
        cursor = page_info.get("startCursor")

    # Reviews are fetched newest-first with `last`, reverse to get chronological order
    # (oldest first) for consistent display
    return list(reversed(all_reviews))


def get_review_threads_graphql(pr_number: str) -> list[dict[str, Any]]:
    """Fetch the latest review threads with resolution status using GraphQL API.

    The REST API doesn't expose thread resolution status, so we use GraphQL.
    Uses `last` to fetch the most recent threads first, ensuring we get the
    latest discussions rather than the oldest ones.

    Note: This query fetches up to 100 review threads per page, each with up to
    50 comments. For PRs exceeding these limits, older threads/comments may be
    omitted. We paginate through threads but not through comments within threads.

    Returns a list of thread objects containing:
    - id: Thread ID
    - isResolved: Whether the thread is resolved
    - isOutdated: Whether the thread is outdated (code changed)
    - path: File path
    - line: Line number
    - comments: List of comments in the thread (up to 50 per thread)
    """
    repo = _get_required_env("REPO_NAME")
    owner, repo_name = repo.split("/")

    # Use `last` to fetch the most recent threads first
    # `before: $cursor` paginates backwards through older threads
    query = """
    query($owner: String!, $repo: String!, $pr_number: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr_number) {
          reviewThreads(last: 100, before: $cursor) {
            pageInfo {
              hasPreviousPage
              startCursor
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
    has_more_pages = False

    while True:
        # Check for overall pagination timeout
        elapsed = time.time() - start_time
        if elapsed > MAX_PAGINATION_TIME:
            logger.warning(
                f"GraphQL pagination timeout after {elapsed:.1f}s, "
                f"fetched {len(threads)} threads across {page_count} pages"
            )
            break

        variables = {
            "owner": owner,
            "repo": repo_name,
            "pr_number": int(pr_number),
            "cursor": cursor,
        }

        result = _call_github_api(
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
        has_more_pages = page_info.get("hasPreviousPage", False)
        if not has_more_pages:
            break
        cursor = page_info.get("startCursor")

    # Warn only if there are actually more pages we didn't fetch
    if has_more_pages:
        logger.warning(
            f"Review threads limited to {len(threads)} threads. "
            "Some threads may be omitted for PRs with extensive review history."
        )

    # Threads are fetched newest-first with `last`, reverse to get chronological order
    return list(reversed(threads))


def format_review_context(
    reviews: list[dict[str, Any]],
    threads: list[dict[str, Any]],
    max_size: int = MAX_REVIEW_CONTEXT,
) -> str:
    """Format review history into a context string for the agent.

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
        """Add a section if it fits within max_size. Returns True if added."""
        nonlocal current_size
        section_size = len(section) + 1  # +1 for newline separator
        if current_size + section_size > max_size:
            return False
        sections.append(section)
        current_size += section_size
        return True

    # Format reviews (high-level review decisions)
    if reviews:
        review_lines: list[str] = ["### Previous Reviews\n"]
        for review in reviews:
            user_data = review.get("user") or {}
            user = user_data.get("login", "unknown")
            state = review.get("state") or "UNKNOWN"
            body = (review.get("body") or "").strip()

            # Map state to emoji for visual clarity
            state_emoji = {
                "APPROVED": "✅",
                "CHANGES_REQUESTED": "🔴",
                "COMMENTED": "💬",
                "DISMISSED": "❌",
                "PENDING": "⏳",
            }.get(state, "❓")

            review_lines.append(f"- {state_emoji} **{user}** ({state})")
            if body:
                # Indent the body and truncate if too long
                body_preview = body[:500] + "..." if len(body) > 500 else body
                indented = "\n".join(f"  > {line}" for line in body_preview.split("\n"))
                review_lines.append(indented)
            review_lines.append("")

        review_section = "\n".join(review_lines)
        if not _add_section(review_section):
            # Even reviews section doesn't fit, return truncation message
            return (
                f"... [review context truncated, "
                f"content exceeds {max_size:,} chars] ..."
            )

    # Format review threads with resolution status
    if threads:
        resolved_threads = [t for t in threads if t.get("isResolved")]
        unresolved_threads = [t for t in threads if not t.get("isResolved")]

        # Unresolved threads (higher priority)
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

        # Resolved threads (lower priority, add if space remains)
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

    # Thread header
    status = "✅ RESOLVED" if is_resolved else "⚠️ UNRESOLVED"
    outdated = " (outdated)" if is_outdated else ""
    location = f"{path}"
    if line_num:
        location += f":{line_num}"

    lines.append(f"**{location}**{outdated} - {status}")

    # Thread comments
    comments_data = thread.get("comments") or {}
    comments = comments_data.get("nodes") or []
    for comment in comments:
        author_data = comment.get("author") or {}
        author = author_data.get("login", "unknown")
        body = (comment.get("body") or "").strip()
        if body:
            # Truncate individual comments if too long
            body_preview = body[:300] + "..." if len(body) > 300 else body
            indented = "\n".join(f"  > {line}" for line in body_preview.split("\n"))
            lines.append(f"  - **{author}**:")
            lines.append(indented)

    lines.append("")
    return lines


def _fetch_with_fallback(
    name: str, fetch_fn: Callable[[], list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Fetch data with error handling and logging.

    Args:
        name: Name of the data being fetched (for logging)
        fetch_fn: Function to call to fetch the data

    Returns:
        Fetched data or empty list on error
    """
    try:
        data = fetch_fn()
        logger.info(f"Fetched {len(data)} {name}")
        return data
    except Exception as e:
        logger.warning(f"Failed to fetch {name}: {e}")
        return []


def get_pr_review_context(pr_number: str) -> str:
    """Get all review context for a PR.

    Fetches reviews and review threads, then formats them into a context string.

    Args:
        pr_number: The PR number

    Returns:
        Formatted review context string, or empty string if no context
    """
    reviews = _fetch_with_fallback("reviews", lambda: get_pr_reviews(pr_number))
    threads = _fetch_with_fallback(
        "review threads", lambda: get_review_threads_graphql(pr_number)
    )

    return format_review_context(reviews, threads)


def get_pr_diff_via_github_api(pr_number: str) -> str:
    """Fetch the PR diff exactly as GitHub renders it.

    Uses the GitHub REST API "Get a pull request" endpoint with an `Accept`
    header requesting diff output.

    This avoids depending on local git refs (often stale/missing in
    `pull_request_target` checkouts).
    """

    repo = _get_required_env("REPO_NAME")
    token = _get_required_env("GITHUB_TOKEN")

    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
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


def truncate_text(diff_text: str, max_total: int = MAX_TOTAL_DIFF) -> str:
    if len(diff_text) <= max_total:
        return diff_text

    total_chars = len(diff_text)
    return (
        diff_text[:max_total]
        + f"\n\n... [total diff truncated, {total_chars:,} chars total, "
        + f"showing first {max_total:,}] ..."
    )


def get_truncated_pr_diff() -> str:
    """Get the PR diff with truncation.

    This uses GitHub as the source of truth so the review matches the PR's
    "Files changed" view.
    """

    pr_number = _get_required_env("PR_NUMBER")
    diff_text = get_pr_diff_via_github_api(pr_number)
    return truncate_text(diff_text)


def get_head_commit_sha(repo_dir: Path | None = None) -> str:
    """
    Get the SHA of the HEAD commit.

    Args:
        repo_dir: Path to the repository (defaults to cwd)

    Returns:
        The commit SHA
    """
    if repo_dir is None:
        repo_dir = Path.cwd()

    return run_git_command(["git", "rev-parse", "HEAD"], repo_dir).strip()


def main():
    """Run the PR review agent."""
    logger.info("Starting PR review process...")

    # Validate required environment variables
    required_vars = [
        "LLM_API_KEY",
        "GITHUB_TOKEN",
        "PR_NUMBER",
        "PR_TITLE",
        "PR_BASE_BRANCH",
        "PR_HEAD_BRANCH",
        "REPO_NAME",
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)

    github_token = os.getenv("GITHUB_TOKEN")

    # Get PR information
    pr_info = {
        "number": os.getenv("PR_NUMBER"),
        "title": os.getenv("PR_TITLE"),
        "body": os.getenv("PR_BODY", ""),
        "repo_name": os.getenv("REPO_NAME"),
        "base_branch": os.getenv("PR_BASE_BRANCH"),
        "head_branch": os.getenv("PR_HEAD_BRANCH"),
    }

    # Get review style - default to standard
    review_style = os.getenv("REVIEW_STYLE", "standard").lower()
    if review_style not in ("standard", "roasted"):
        logger.warning(f"Unknown REVIEW_STYLE '{review_style}', using 'standard'")
        review_style = "standard"

    logger.info(f"Reviewing PR #{pr_info['number']}: {pr_info['title']}")
    logger.info(f"Review style: {review_style}")

    try:
        pr_diff = get_truncated_pr_diff()
        logger.info(f"Got PR diff with {len(pr_diff)} characters")

        # Get the HEAD commit SHA for inline comments
        commit_id = get_head_commit_sha()
        logger.info(f"HEAD commit SHA: {commit_id}")

        # Fetch previous review context (comments, threads, resolution status)
        pr_number = pr_info.get("number", "")
        review_context = get_pr_review_context(pr_number)
        if review_context:
            logger.info(f"Got review context with {len(review_context)} characters")
        else:
            logger.info("No previous review context found")

        # Create the review prompt using the template
        # Include the skill trigger keyword to activate the appropriate skill
        skill_trigger = (
            "/codereview" if review_style == "standard" else "/codereview-roasted"
        )
        prompt = format_prompt(
            skill_trigger=skill_trigger,
            title=pr_info.get("title", "N/A"),
            body=pr_info.get("body") or "No description provided",
            repo_name=pr_info.get("repo_name", "N/A"),
            base_branch=pr_info.get("base_branch", "main"),
            head_branch=pr_info.get("head_branch", "N/A"),
            pr_number=pr_number,
            commit_id=commit_id,
            diff=pr_diff,
            review_context=review_context,
        )

        # Configure LLM
        api_key = os.getenv("LLM_API_KEY")
        model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
        base_url = os.getenv("LLM_BASE_URL")

        llm_config = {
            "model": model,
            "api_key": api_key,
            "usage_id": "pr_review_agent",
            "drop_params": True,
        }

        if base_url:
            llm_config["base_url"] = base_url

        llm = LLM(**llm_config)

        # Get the current working directory as workspace
        cwd = os.getcwd()

        # Load project-specific skills from the repository being reviewed
        # This includes AGENTS.md, .cursorrules, and skills from .agents/skills/
        project_skills = load_project_skills(cwd)
        logger.info(
            f"Loaded {len(project_skills)} project skills: "
            f"{[s.name for s in project_skills]}"
        )

        # Create AgentContext with public skills enabled and project skills
        # Public skills from https://github.com/OpenHands/skills include:
        # - /codereview: Standard code review skill
        # - /codereview-roasted: Linus Torvalds style brutally honest review
        # Project skills include repo-specific guidance (AGENTS.md, etc.)
        agent_context = AgentContext(
            load_public_skills=True,
            skills=project_skills,
        )

        # Create agent with default tools and agent context
        # Note: agent_context must be passed at initialization since Agent is frozen
        agent = Agent(
            llm=llm,
            tools=get_default_tools(enable_browser=False),  # CLI mode - no browser
            agent_context=agent_context,
            system_prompt_kwargs={"cli_mode": True},
            condenser=get_default_condenser(
                llm=llm.model_copy(update={"usage_id": "condenser"})
            ),
        )

        # Create conversation with secrets for masking
        # These secrets will be masked in agent output to prevent accidental exposure
        secrets = {}
        if api_key:
            secrets["LLM_API_KEY"] = api_key
        if github_token:
            secrets["GITHUB_TOKEN"] = github_token

        conversation = Conversation(
            agent=agent,
            workspace=cwd,
            secrets=secrets,
        )

        logger.info("Starting PR review analysis...")
        logger.info("Agent received the PR diff in the initial message")
        logger.info(f"Using skill trigger: {skill_trigger}")
        logger.info("Agent will post inline review comments directly via GitHub API")

        # Send the prompt and run the agent
        # The agent will analyze the code and post inline review comments
        # directly to the PR using the GitHub API
        conversation.send_message(prompt)
        conversation.run()

        # The agent should have posted review comments via GitHub API
        # Log the final response for debugging purposes
        review_content = get_agent_final_response(conversation.state.events)
        if review_content:
            logger.info(f"Agent final response: {len(review_content)} characters")

        # Print cost information for CI output
        metrics = conversation.conversation_stats.get_combined_metrics()
        print("\n=== PR Review Cost Summary ===")
        print(f"Total Cost: ${metrics.accumulated_cost:.6f}")
        if metrics.accumulated_token_usage:
            token_usage = metrics.accumulated_token_usage
            print(f"Prompt Tokens: {token_usage.prompt_tokens}")
            print(f"Completion Tokens: {token_usage.completion_tokens}")
            if token_usage.cache_read_tokens > 0:
                print(f"Cache Read Tokens: {token_usage.cache_read_tokens}")
            if token_usage.cache_write_tokens > 0:
                print(f"Cache Write Tokens: {token_usage.cache_write_tokens}")

        # Capture and store trace context for delayed evaluation
        # When the PR is merged/closed, we can use this context to add the
        # evaluation span to the same trace, enabling signals to analyze both
        # the original review and evaluation together.
        # Note: Laminar methods gracefully handle the uninitialized case by
        # returning None or early-returning, so no try/except needed.
        trace_id = Laminar.get_trace_id()
        # Use model_dump(mode='json') to ensure UUIDs are serialized as strings
        # for JSON compatibility. get_laminar_span_context_dict() returns UUID
        # objects which are not JSON serializable.
        laminar_span_context = Laminar.get_laminar_span_context()
        span_context = (
            laminar_span_context.model_dump(mode="json")
            if laminar_span_context
            else None
        )

        if trace_id:
            # Set trace metadata for later retrieval and filtering
            Laminar.set_trace_metadata(
                {
                    "pr_number": pr_info["number"],
                    "repo_name": pr_info["repo_name"],
                    "workflow_phase": "review",
                    "review_style": review_style,
                }
            )

            # Store trace context in file for GitHub artifact upload
            # This allows the evaluation workflow to add its span to this trace
            # The span_context includes trace_id, span_id, and span_path needed
            # to continue the trace across separate workflow runs.
            trace_data = {
                "trace_id": str(trace_id),
                "span_context": span_context,
                "pr_number": pr_info["number"],
                "repo_name": pr_info["repo_name"],
                "commit_id": commit_id,
                "review_style": review_style,
            }
            with open("laminar_trace_info.json", "w") as f:
                json.dump(trace_data, f, indent=2)
            logger.info(f"Laminar trace ID: {trace_id}")
            if span_context:
                logger.info("Laminar span context captured for trace continuation")
            print("\n=== Laminar Trace ===")
            print(f"Trace ID: {trace_id}")

            # Ensure trace is flushed to Laminar before workflow ends
            Laminar.flush()
        else:
            logger.warning(
                "No Laminar trace ID found - observability may not be enabled"
            )

        logger.info("PR review completed successfully")

    except Exception as e:
        logger.error(f"PR review failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
