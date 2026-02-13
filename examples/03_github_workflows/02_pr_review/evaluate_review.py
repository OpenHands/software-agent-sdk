#!/usr/bin/env python3
"""
PR Review Evaluation Script

This script runs when a PR is merged or closed to evaluate how well the
review comments were addressed. It creates an evaluation trace in Laminar
that can be processed by a signal to determine review effectiveness.

The evaluation flow:
1. Read the original trace ID from the artifact
2. Fetch PR review comments and thread discussion from GitHub
3. Fetch the final patch/diff
4. Create an evaluation span with all context
5. Optionally score the original trace

Environment Variables:
    LMNR_PROJECT_API_KEY: Laminar project API key (required)
    GITHUB_TOKEN: GitHub token for API access (required)
    PR_NUMBER: Pull request number (required)
    REPO_NAME: Repository name in format owner/repo (required)
    PR_MERGED: Whether the PR was merged ('true' or 'false')
"""

import json

# Configure logging
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from lmnr import Laminar, LaminarClient


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_required_env(name: str) -> str:
    """Get a required environment variable or raise an error."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


def _get_github_headers() -> dict[str, str]:
    """Get headers for GitHub API requests."""
    token = _get_required_env("GITHUB_TOKEN")
    return {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_agent_usernames() -> set[str]:
    """Get the set of agent usernames to identify agent comments.

    Configurable via AGENT_USERNAMES environment variable (comma-separated).
    Defaults to 'openhands-agent,all-hands-bot'.
    """
    usernames = os.getenv("AGENT_USERNAMES", "openhands-agent,all-hands-bot")
    return set(name.strip() for name in usernames.split(",") if name.strip())


def _handle_github_api_error(e: urllib.error.HTTPError, context: str) -> None:
    """Handle GitHub API errors with rate limit awareness."""
    if e.code == 429:
        retry_after = e.headers.get("Retry-After", "60")
        logger.warning(f"Rate limited by GitHub API. Retry after {retry_after}s")
    logger.error(f"Failed to {context}: HTTP {e.code}")


def fetch_pr_review_comments(repo: str, pr_number: str) -> list[dict]:
    """Fetch all review comments on a PR.

    This includes inline code review comments, not regular PR comments.
    """
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    request = urllib.request.Request(url, headers=_get_github_headers())

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _handle_github_api_error(e, "fetch review comments")
        return []


def fetch_pr_issue_comments(repo: str, pr_number: str) -> list[dict]:
    """Fetch issue-style comments on a PR (the main thread)."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    request = urllib.request.Request(url, headers=_get_github_headers())

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _handle_github_api_error(e, "fetch issue comments")
        return []


def fetch_pr_reviews(repo: str, pr_number: str) -> list[dict]:
    """Fetch all reviews on a PR (approve, request changes, comment)."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"
    request = urllib.request.Request(url, headers=_get_github_headers())

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _handle_github_api_error(e, "fetch reviews")
        return []


def fetch_pr_diff(repo: str, pr_number: str) -> str:
    """Fetch the final diff of the PR."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    headers = _get_github_headers()
    headers["Accept"] = "application/vnd.github.v3.diff"

    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        _handle_github_api_error(e, "fetch PR diff")
        return ""


def fetch_pr_info(repo: str, pr_number: str) -> dict:
    """Fetch PR metadata."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    request = urllib.request.Request(url, headers=_get_github_headers())

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _handle_github_api_error(e, "fetch PR info")
        return {}


def extract_agent_comments(
    review_comments: list[dict], issue_comments: list[dict], reviews: list[dict]
) -> list[dict]:
    """Extract comments made by the review agent.

    Agent usernames are configurable via AGENT_USERNAMES environment variable.
    """
    agent_users = _get_agent_usernames()
    agent_comments = []

    # Review comments (inline code comments)
    for comment in review_comments:
        if comment.get("user", {}).get("login") in agent_users:
            agent_comments.append(
                {
                    "type": "review_comment",
                    "id": comment.get("id"),
                    "body": comment.get("body", ""),
                    "path": comment.get("path"),
                    "line": comment.get("line") or comment.get("original_line"),
                    "created_at": comment.get("created_at"),
                }
            )

    # Issue comments (main thread)
    for comment in issue_comments:
        if comment.get("user", {}).get("login") in agent_users:
            agent_comments.append(
                {
                    "type": "issue_comment",
                    "id": comment.get("id"),
                    "body": comment.get("body", ""),
                    "created_at": comment.get("created_at"),
                }
            )

    # Review bodies
    for review in reviews:
        if review.get("user", {}).get("login") in agent_users and review.get("body"):
            agent_comments.append(
                {
                    "type": "review",
                    "id": review.get("id"),
                    "body": review.get("body", ""),
                    "state": review.get("state"),
                    "created_at": review.get("submitted_at"),
                }
            )

    return agent_comments


def extract_human_responses(
    review_comments: list[dict],
    issue_comments: list[dict],
    agent_users: set[str] | None = None,
) -> list[dict]:
    """Extract comments/responses from humans (non-agent users).

    Agent usernames are configurable via AGENT_USERNAMES environment variable.
    """
    if agent_users is None:
        agent_users = _get_agent_usernames()
    human_responses = []

    for comment in review_comments:
        if comment.get("user", {}).get("login") not in agent_users:
            human_responses.append(
                {
                    "type": "review_comment",
                    "user": comment.get("user", {}).get("login"),
                    "body": comment.get("body", ""),
                    "in_reply_to_id": comment.get("in_reply_to_id"),
                    "created_at": comment.get("created_at"),
                }
            )

    for comment in issue_comments:
        if comment.get("user", {}).get("login") not in agent_users:
            human_responses.append(
                {
                    "type": "issue_comment",
                    "user": comment.get("user", {}).get("login"),
                    "body": comment.get("body", ""),
                    "created_at": comment.get("created_at"),
                }
            )

    return human_responses


def truncate_text(text: str, max_chars: int = 50000) -> str:
    """Truncate text to stay within reasonable API payload limits.

    Max 50k chars chosen to stay well under typical API payload limits
    while preserving enough context for evaluation. This keeps the
    evaluation trace size manageable for Laminar processing.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... [truncated, {len(text)} total chars]"


def main():
    """Run the PR review evaluation."""
    logger.info("Starting PR review evaluation...")

    # Get required environment variables
    pr_number = _get_required_env("PR_NUMBER")
    repo_name = _get_required_env("REPO_NAME")
    pr_merged = os.getenv("PR_MERGED", "false").lower() == "true"

    logger.info(f"Evaluating PR #{pr_number} in {repo_name}")
    logger.info(f"PR was merged: {pr_merged}")

    # Read original trace info from artifact
    trace_info_path = Path("laminar_trace_info.json")
    original_trace_id = None
    original_span_context = None
    original_trace_data = {}

    if trace_info_path.exists():
        with open(trace_info_path) as f:
            original_trace_data = json.load(f)
            original_trace_id = original_trace_data.get("trace_id")
            original_span_context = original_trace_data.get("span_context")
            logger.info(f"Original trace ID: {original_trace_id}")
            if original_span_context:
                logger.info(
                    "Found span context - will add evaluation to original trace"
                )
            else:
                logger.info("No span context - evaluation will create standalone trace")
    else:
        logger.warning(
            "No trace info file found - evaluation will create standalone trace"
        )

    # Fetch PR data from GitHub
    logger.info("Fetching PR data from GitHub...")
    review_comments = fetch_pr_review_comments(repo_name, pr_number)
    issue_comments = fetch_pr_issue_comments(repo_name, pr_number)
    reviews = fetch_pr_reviews(repo_name, pr_number)
    final_diff = fetch_pr_diff(repo_name, pr_number)
    pr_info = fetch_pr_info(repo_name, pr_number)

    logger.info(f"Found {len(review_comments)} review comments")
    logger.info(f"Found {len(issue_comments)} issue comments")
    logger.info(f"Found {len(reviews)} reviews")

    # Extract agent comments and human responses
    agent_comments = extract_agent_comments(review_comments, issue_comments, reviews)
    human_responses = extract_human_responses(review_comments, issue_comments)

    logger.info(f"Agent made {len(agent_comments)} comments")
    logger.info(f"Humans made {len(human_responses)} responses")

    # Initialize Laminar for tracing
    Laminar.initialize()

    # Create evaluation context
    evaluation_context = {
        "pr_number": pr_number,
        "repo_name": repo_name,
        "pr_merged": pr_merged,
        "pr_title": pr_info.get("title", ""),
        "pr_state": pr_info.get("state", ""),
        "original_trace_id": original_trace_id,
        "agent_comments": agent_comments,
        "human_responses": human_responses,
        "final_diff": truncate_text(final_diff),
        "total_review_comments": len(review_comments),
        "total_issue_comments": len(issue_comments),
    }

    # Create an evaluation span that can be processed by a Laminar signal
    # The signal will analyze the agent comments vs final diff to determine
    # which suggestions were addressed.
    #
    # IMPORTANT: If we have the original span context, we use parent_span_context
    # to add this span as a child of the original trace. This allows Laminar
    # signals to operate on the complete trace (review + evaluation) together.
    with Laminar.start_as_current_span(
        name="pr_review_evaluation",
        input=evaluation_context,
        tags=["pr-review-evaluation"],
        parent_span_context=original_span_context,
    ):
        # Set trace metadata for filtering and linking
        Laminar.set_trace_metadata(
            {
                "original_trace_id": original_trace_id or "none",
                "evaluation_type": "pr_review_effectiveness",
                "pr_number": pr_number,
                "repo_name": repo_name,
                "pr_merged": str(pr_merged),
            }
        )

        # Log summary for visibility
        summary = {
            "pr": f"{repo_name}#{pr_number}",
            "merged": pr_merged,
            "agent_comments_count": len(agent_comments),
            "human_responses_count": len(human_responses),
            "diff_length": len(final_diff),
        }
        logger.info(f"Evaluation summary: {json.dumps(summary)}")

        # Set output with key metrics
        Laminar.set_span_output(
            {
                "summary": summary,
                "ready_for_signal": True,
            }
        )

    # Flush to ensure span is sent
    Laminar.flush()

    # If we have the original trace ID, we can also score it directly
    # This provides immediate feedback without waiting for signal processing
    if original_trace_id:
        try:
            client = LaminarClient()

            # PLACEHOLDER SCORE: This is a simple engagement metric, NOT a measure
            # of review effectiveness. The actual effectiveness score will come from
            # the Laminar signal which analyzes whether suggestions were implemented.
            #
            # This score only indicates:
            # - Whether humans responded to agent comments (engagement)
            # - Whether the PR was merged (completion)
            #
            # It does NOT measure:
            # - Whether agent suggestions were actually helpful
            # - Whether suggestions were implemented in the final code
            # - Quality of the review feedback
            preliminary_score = 0.0
            if agent_comments:
                engagement_ratio = min(len(human_responses) / len(agent_comments), 1.0)
                preliminary_score = engagement_ratio * 0.5  # Scale to 0-0.5

                if pr_merged:
                    preliminary_score += 0.3

            client.evaluators.score(
                name="review_engagement",
                trace_id=original_trace_id,
                score=preliminary_score,
                metadata={
                    "agent_comments": len(agent_comments),
                    "human_responses": len(human_responses),
                    "pr_merged": pr_merged,
                    "note": "Placeholder - signal provides effectiveness analysis",
                    "score_type": "engagement_only",
                },
            )
            logger.info(
                f"Added preliminary score {preliminary_score:.2f} "
                f"to original trace {original_trace_id}"
            )

            # Tag the original trace to indicate evaluation was done
            client.tags.tag(original_trace_id, ["evaluated", f"pr-{pr_number}"])
            logger.info(f"Tagged original trace {original_trace_id}")

        except Exception as e:
            logger.warning(f"Failed to score original trace: {e}")
            # Don't fail the workflow if scoring fails

    # Get and print the evaluation trace ID
    eval_trace_id = Laminar.get_trace_id()
    print("\n=== PR Review Evaluation ===")
    print(f"PR: {repo_name}#{pr_number}")
    print(f"Merged: {pr_merged}")
    print(f"Agent Comments: {len(agent_comments)}")
    print(f"Human Responses: {len(human_responses)}")
    if original_trace_id:
        print(f"Original Review Trace: {original_trace_id}")
    if eval_trace_id:
        print(f"Evaluation Trace: {eval_trace_id}")

    logger.info("PR review evaluation completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        sys.exit(1)
