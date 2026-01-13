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

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.git.utils import run_git_command
from openhands.tools.preset.default import get_default_condenser, get_default_tools


# Add the script directory to Python path so we can import prompt.py
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from prompt import PROMPT  # noqa: E402


logger = get_logger(__name__)

# Maximum total diff size
MAX_TOTAL_DIFF = 100000


@dataclass(frozen=True)
class ReviewComment:
    path: str
    side: str
    line: int
    body: str
    start_line: int | None = None
    start_side: str | None = None


def _extract_json_object(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty agent response")

    # Fast-path: already valid JSON
    try:
        json.loads(text)
        return text
    except Exception:
        pass

    # Common failure mode: fenced code blocks
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
        json.loads(candidate)
        return candidate

    # Fallback: first {...} block
    brace = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if brace:
        candidate = brace.group(0).strip()
        json.loads(candidate)
        return candidate

    raise ValueError("Could not find JSON object in agent response")


def parse_review_response(review_content: str) -> tuple[str, str, list[ReviewComment]]:
    raw_json = _extract_json_object(review_content)
    data = json.loads(raw_json)

    event = str(data.get("event", "COMMENT")).upper()
    if event not in {"COMMENT", "APPROVE", "REQUEST_CHANGES"}:
        raise ValueError(f"Invalid event: {event}")

    summary = str(data.get("summary", "")).strip()
    if not summary:
        summary = "Automated review from OpenHands. See inline comments."

    comments_in: list[dict[str, Any]] = data.get("comments") or []
    comments: list[ReviewComment] = []
    for c in comments_in:
        if not isinstance(c, dict):
            continue
        path = str(c.get("path", "")).strip()
        side = str(c.get("side", "RIGHT")).upper().strip() or "RIGHT"
        body = str(c.get("body", "")).strip()
        line = c.get("line")
        start_line = c.get("start_line")
        start_side = c.get("start_side")

        if not path or not body or line is None:
            continue
        if side not in {"RIGHT", "LEFT"}:
            side = "RIGHT"

        comments.append(
            ReviewComment(
                path=path,
                side=side,
                line=int(line),
                start_line=int(start_line) if start_line is not None else None,
                start_side=str(start_side).upper() if start_side is not None else None,
                body=body,
            )
        )

    return event, summary, comments


def get_feedback_base_url(repo: str) -> str:
    configured = (os.getenv("FEEDBACK_BASE_URL") or "").strip()
    if configured:
        return configured.rstrip("/") + "/"

    # Default to GitHub project pages URL.
    # Example: https://OWNER.github.io/REPO/
    owner, repo_name = repo.split("/", 1)
    return f"https://{owner}.github.io/{repo_name}/"


def build_feedback_url(
    base_url: str,
    *,
    repo: str,
    pr_number: str,
    run_id: str,
    comment_key: str,
    rating: str,
    path: str | None = None,
    line: int | None = None,
) -> str:
    return_to = f"https://github.com/{repo}/pull/{pr_number}"
    params: dict[str, str] = {
        "repo": repo,
        "pr": str(pr_number),
        "run_id": run_id,
        "comment_key": comment_key,
        "rating": rating,
        "return_to": return_to,
    }
    if path:
        params["path"] = path
    if line is not None:
        params["line"] = str(line)

    return f"{base_url}?{urllib.parse.urlencode(params)}"


def append_feedback_links(
    body: str,
    *,
    base_url: str,
    repo: str,
    pr_number: str,
    run_id: str,
    comment_key: str,
    path: str | None = None,
    line: int | None = None,
) -> str:
    up = build_feedback_url(
        base_url,
        repo=repo,
        pr_number=pr_number,
        run_id=run_id,
        comment_key=comment_key,
        rating="up",
        path=path,
        line=line,
    )
    down = build_feedback_url(
        base_url,
        repo=repo,
        pr_number=pr_number,
        run_id=run_id,
        comment_key=comment_key,
        rating="down",
        path=path,
        line=line,
    )

    footer = f"\n\n---\nFeedback: [👍 Useful]({up}) | [👎 Not useful]({down})"
    return (body or "").rstrip() + footer


def post_pr_review(
    *,
    repo: str,
    pr_number: str,
    token: str,
    commit_id: str,
    event: str,
    body: str,
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"
    payload: dict[str, Any] = {
        "commit_id": commit_id,
        "event": event,
        "body": body,
    }
    if comments:
        payload["comments"] = comments

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        details = (e.read() or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"GitHub create review failed: HTTP {e.code} {e.reason}. {details}"
        ) from e




def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


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

    github_token = _get_required_env("GITHUB_TOKEN")
    run_id = os.getenv("GITHUB_RUN_ID") or uuid.uuid4().hex

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

        # Prevent the agent from posting to GitHub directly via gh/curl.
        # We keep the token in-memory for posting the final review.
        os.environ.pop("GITHUB_TOKEN", None)

        # Create the review prompt using the template
        # Include the skill trigger keyword to activate the appropriate skill
        skill_trigger = (
            "/codereview" if review_style == "standard" else "/codereview-roasted"
        )
        prompt = PROMPT.format(
            title=pr_info.get("title", "N/A"),
            body=pr_info.get("body", "No description provided"),
            repo_name=pr_info.get("repo_name", "N/A"),
            base_branch=pr_info.get("base_branch", "main"),
            head_branch=pr_info.get("head_branch", "N/A"),
            pr_number=pr_info.get("number", "N/A"),
            commit_id=commit_id,
            skill_trigger=skill_trigger,
            diff=pr_diff,
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

        # Create AgentContext with public skills enabled
        # This loads skills from https://github.com/OpenHands/skills including:
        # - /codereview: Standard code review skill
        # - /codereview-roasted: Linus Torvalds style brutally honest review
        agent_context = AgentContext(
            load_public_skills=True,
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
        secrets: dict[str, str] = {}
        if api_key:
            secrets["LLM_API_KEY"] = api_key

        conversation = Conversation(
            agent=agent,
            workspace=cwd,
            secrets=secrets,
        )

        logger.info("Starting PR review analysis...")
        logger.info("Agent received the PR diff in the initial message")
        logger.info(f"Using skill trigger: {skill_trigger}")
        logger.info(
            "Agent will return structured JSON only; this script will post the GitHub review "
            "and append feedback links to every inline comment."
        )

        conversation.send_message(prompt)
        conversation.run()

        review_content = get_agent_final_response(conversation.state.events)
        if not review_content:
            raise RuntimeError("Agent did not return a final response")
        logger.info(f"Agent final response: {len(review_content)} characters")

        repo = str(pr_info["repo_name"])
        pr_number = str(pr_info["number"])
        feedback_base_url = get_feedback_base_url(repo)

        try:
            event, summary, review_comments = parse_review_response(review_content)
            review_body = append_feedback_links(
                summary,
                base_url=feedback_base_url,
                repo=repo,
                pr_number=pr_number,
                run_id=run_id,
                comment_key="summary",
            )

            gh_comments: list[dict[str, Any]] = []
            for idx, c in enumerate(review_comments, start=1):
                comment_body = append_feedback_links(
                    c.body,
                    base_url=feedback_base_url,
                    repo=repo,
                    pr_number=pr_number,
                    run_id=run_id,
                    comment_key=str(idx),
                    path=c.path,
                    line=c.line,
                )

                payload: dict[str, Any] = {
                    "path": c.path,
                    "side": c.side,
                    "line": c.line,
                    "body": comment_body,
                }
                if c.start_line is not None:
                    payload["start_line"] = c.start_line
                    payload["start_side"] = c.start_side or c.side

                gh_comments.append(payload)

        except Exception as e:
            logger.error(f"Failed to parse agent JSON output: {e}")
            event = "COMMENT"
            raw = review_content.strip()
            if len(raw) > 20000:
                raw = raw[:20000] + "\n\n...[truncated]..."

            review_body = append_feedback_links(
                "Automated review output could not be parsed into structured comments. "
                "Posting raw output instead.\n\n" + raw,
                base_url=feedback_base_url,
                repo=repo,
                pr_number=pr_number,
                run_id=run_id,
                comment_key="summary",
            )
            gh_comments = []

        logger.info(f"Posting GitHub review: event={event}, comments={len(gh_comments)}")
        resp = post_pr_review(
            repo=repo,
            pr_number=pr_number,
            token=github_token,
            commit_id=commit_id,
            event=event,
            body=review_body,
            comments=gh_comments,
        )
        logger.info(f"Posted GitHub review id={resp.get('id')}")

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

        logger.info("PR review completed successfully")

    except Exception as e:
        logger.error(f"PR review failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
