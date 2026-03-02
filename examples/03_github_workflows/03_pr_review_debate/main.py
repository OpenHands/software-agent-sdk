#!/usr/bin/env python3
"""
Multi-Model PR Review with Debate

This script runs PR reviews using multiple AI models (GPT 5.2, Claude Sonnet 4.5,
and Gemini 3 Flash), then orchestrates a debate between the reviewers to produce
a consolidated, well-reasoned final review.

The debate mechanism allows the AI models to:
1. Share their initial reviews
2. Discuss disagreements using inter-agent communication tools
3. Work toward consensus on key issues
4. Produce a final consolidated review

Environment Variables:
    LLM_API_KEY: API key for the LLM (required)
    LLM_BASE_URL: Optional base URL for LLM API
    GITHUB_TOKEN: GitHub token for API access (required)
    PR_NUMBER: Pull request number (required)
    PR_TITLE: Pull request title (required)
    PR_BODY: Pull request body (optional)
    PR_BASE_BRANCH: Base branch name (required)
    PR_HEAD_BRANCH: Head branch name (required)
    REPO_NAME: Repository name in format owner/repo (required)
    REVIEW_STYLE: Review style ('standard' or 'roasted', default: 'standard')
    MAX_DEBATE_ROUNDS: Maximum debate rounds (default: 3)

For setup instructions, see README.md in this directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openhands.sdk import get_logger


# Add current directory to path for local imports
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from debate_orchestrator import DebateOrchestrator  # noqa: E402
from github_utils import (  # noqa: E402
    get_head_commit_sha,
    get_pr_review_context,
    get_required_env,
    get_truncated_pr_diff,
)
from models import PRInfo, ReviewerModel  # noqa: E402
from review_runner import consolidate_reviews, run_parallel_reviews  # noqa: E402


logger = get_logger(__name__)


def get_pr_info() -> PRInfo:
    """Get PR information from environment variables."""
    return PRInfo(
        number=get_required_env("PR_NUMBER"),
        title=get_required_env("PR_TITLE"),
        body=os.getenv("PR_BODY", ""),
        repo_name=get_required_env("REPO_NAME"),
        base_branch=get_required_env("PR_BASE_BRANCH"),
        head_branch=get_required_env("PR_HEAD_BRANCH"),
    )


def main():
    """Run the multi-model PR review with debate."""
    logger.info("Starting multi-model PR review with debate...")

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

    try:
        # Get PR information
        pr_info = get_pr_info()
        logger.info(f"Reviewing PR #{pr_info.number}: {pr_info.title}")

        # Get PR diff
        pr_info.diff = get_truncated_pr_diff(pr_info.number)
        logger.info(f"Got PR diff with {len(pr_info.diff)} characters")

        # Get HEAD commit SHA
        pr_info.commit_id = get_head_commit_sha()
        logger.info(f"HEAD commit SHA: {pr_info.commit_id}")

        # Get previous review context
        pr_info.review_context = get_pr_review_context(pr_info.number)
        if pr_info.review_context:
            logger.info(
                f"Got review context with {len(pr_info.review_context)} characters"
            )
        else:
            logger.info("No previous review context found")

        # Get review style
        review_style = os.getenv("REVIEW_STYLE", "standard").lower()
        if review_style not in ("standard", "roasted"):
            logger.warning(f"Unknown REVIEW_STYLE '{review_style}', using 'standard'")
            review_style = "standard"
        skill_trigger = (
            "/codereview" if review_style == "standard" else "/codereview-roasted"
        )
        logger.info(f"Review style: {review_style}")

        # Phase 1: Run parallel reviews with multiple models
        logger.info("=== Phase 1: Running parallel reviews ===")
        models_to_use = [
            ReviewerModel.GPT_5_2,
            ReviewerModel.CLAUDE_SONNET_4_5,
            ReviewerModel.GEMINI_3_FLASH,
        ]

        initial_reviews = run_parallel_reviews(
            pr_info=pr_info,
            models=models_to_use,
            skill_trigger=skill_trigger,
        )

        # Check for successful reviews
        successful_reviews = {
            m: r for m, r in initial_reviews.items() if not r.error and r.review_text
        }
        if len(successful_reviews) < 2:
            logger.error(
                f"Only {len(successful_reviews)} successful reviews. "
                "Need at least 2 for debate."
            )
            # Fall back to single review
            if successful_reviews:
                model, result = next(iter(successful_reviews.items()))
                print(f"\n=== {model.display_name}'s Review ===")
                print(result.review_text)
            sys.exit(1)

        # Print initial reviews summary
        print("\n=== Initial Reviews Summary ===")
        for model, result in initial_reviews.items():
            if result.error:
                print(f"- {model.display_name}: ERROR - {result.error}")
            else:
                print(f"- {model.display_name}: {len(result.review_text)} chars")
                if result.cost > 0:
                    print(f"  Cost: ${result.cost:.6f}")

        # Phase 2: Run debate (if enabled)
        max_debate_rounds = int(os.getenv("MAX_DEBATE_ROUNDS", "3"))
        if max_debate_rounds > 0 and len(successful_reviews) >= 2:
            logger.info("=== Phase 2: Running debate between reviewers ===")

            orchestrator = DebateOrchestrator(max_rounds=max_debate_rounds)
            debate_result = orchestrator.run_debate(
                pr_info=pr_info,
                initial_reviews=successful_reviews,
            )

            # Print final review
            print("\n=== Final Consolidated Review ===")
            print(debate_result.final_consolidated_review)

            # Print cost summary
            print("\n=== Cost Summary ===")
            print(f"Total Cost: ${debate_result.total_cost:.6f}")

            # Save results to file
            results = {
                "pr_number": pr_info.number,
                "pr_title": pr_info.title,
                "repo_name": pr_info.repo_name,
                "models_used": [m.value for m in successful_reviews.keys()],
                "debate_rounds": debate_result.debate_state.current_round,
                "total_cost": debate_result.total_cost,
                "final_review": debate_result.final_consolidated_review,
            }
            with open("debate_review_results.json", "w") as f:
                json.dump(results, f, indent=2)
            logger.info("Results saved to debate_review_results.json")

        else:
            # No debate, just consolidate
            logger.info("Debate disabled or not enough reviewers. Consolidating...")
            consolidated = consolidate_reviews(initial_reviews)
            print("\n=== Consolidated Reviews (No Debate) ===")
            print(consolidated)

        logger.info("Multi-model PR review completed successfully")

    except Exception as e:
        logger.error(f"PR review failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
