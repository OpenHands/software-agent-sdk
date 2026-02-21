"""
Multi-model review runner for PR review debate.

This module runs parallel code reviews using multiple LLM models
and collects their initial reviews.
"""

from __future__ import annotations

import concurrent.futures
import os
from typing import TYPE_CHECKING

from models import PRInfo, ReviewerModel, ReviewResult
from prompt import format_initial_review_prompt
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.tools.preset.default import get_default_condenser, get_default_tools


if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def create_llm_for_model(
    model: ReviewerModel,
    api_key: str,
    base_url: str | None = None,
) -> LLM:
    """Create an LLM instance for a specific model.

    Args:
        model: The reviewer model to use
        api_key: API key for the LLM
        base_url: Optional base URL for the LLM API

    Returns:
        Configured LLM instance
    """
    config: dict = {
        "model": model.value,
        "api_key": SecretStr(api_key),
        "usage_id": f"pr_review_{model.name.lower()}",
        "drop_params": True,
        "stream": False,  # Disable streaming for parallel execution
    }

    if base_url:
        config["base_url"] = base_url

    return LLM(**config)


def run_single_review(
    model: ReviewerModel,
    pr_info: PRInfo,
    api_key: str,
    base_url: str | None = None,
    skill_trigger: str = "/codereview",
) -> ReviewResult:
    """Run a single review with a specific model.

    Args:
        model: The reviewer model to use
        pr_info: Pull request information
        api_key: API key for the LLM
        base_url: Optional base URL for the LLM API
        skill_trigger: Skill trigger for the review style

    Returns:
        ReviewResult with the model's review
    """
    logger.info(f"Starting review with {model.display_name}")

    try:
        llm = create_llm_for_model(model, api_key, base_url)

        # Create the review prompt
        prompt = format_initial_review_prompt(
            skill_trigger=skill_trigger,
            title=pr_info.title,
            body=pr_info.body,
            repo_name=pr_info.repo_name,
            base_branch=pr_info.base_branch,
            head_branch=pr_info.head_branch,
            pr_number=pr_info.number,
            commit_id=pr_info.commit_id,
            diff=pr_info.diff,
            review_context=pr_info.review_context,
        )

        # Create agent with public skills
        agent_context = AgentContext(load_public_skills=True)
        agent = Agent(
            llm=llm,
            tools=get_default_tools(enable_browser=False),
            agent_context=agent_context,
            system_prompt_kwargs={"cli_mode": True},
            condenser=get_default_condenser(
                llm=llm.model_copy(
                    update={"usage_id": f"condenser_{model.name.lower()}"}
                )
            ),
        )

        # Create conversation
        cwd = os.getcwd()
        secrets = {"LLM_API_KEY": api_key}
        if github_token := os.getenv("GITHUB_TOKEN"):
            secrets["GITHUB_TOKEN"] = github_token

        conversation = Conversation(
            agent=agent,
            workspace=cwd,
            secrets=secrets,
        )

        # Run the review
        conversation.send_message(prompt)
        conversation.run()

        # Extract results
        review_text = get_agent_final_response(conversation.state.events) or ""
        metrics = conversation.conversation_stats.get_combined_metrics()

        token_usage = {}
        if metrics.accumulated_token_usage:
            tu = metrics.accumulated_token_usage
            token_usage = {
                "prompt_tokens": tu.prompt_tokens,
                "completion_tokens": tu.completion_tokens,
            }

        logger.info(f"Completed review with {model.display_name}")

        return ReviewResult(
            model=model,
            review_text=review_text,
            cost=metrics.accumulated_cost,
            token_usage=token_usage,
        )

    except Exception as e:
        logger.error(f"Review failed for {model.display_name}: {e}")
        return ReviewResult(
            model=model,
            review_text="",
            error=str(e),
        )


def run_parallel_reviews(
    pr_info: PRInfo,
    models: list[ReviewerModel] | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    skill_trigger: str = "/codereview",
    max_workers: int = 3,
) -> dict[ReviewerModel, ReviewResult]:
    """Run reviews in parallel using multiple models.

    Args:
        pr_info: Pull request information
        models: List of models to use (defaults to all available)
        api_key: API key for the LLM (defaults to LLM_API_KEY env var)
        base_url: Optional base URL for the LLM API
        skill_trigger: Skill trigger for the review style
        max_workers: Maximum parallel workers

    Returns:
        Dictionary mapping models to their review results
    """
    if models is None:
        models = [
            ReviewerModel.GPT_5_2,
            ReviewerModel.CLAUDE_SONNET_4_5,
            ReviewerModel.GEMINI_3_FLASH,
        ]

    if api_key is None:
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise ValueError("LLM_API_KEY environment variable is required")

    if base_url is None:
        base_url = os.getenv("LLM_BASE_URL")

    logger.info(f"Running parallel reviews with {len(models)} models")

    results: dict[ReviewerModel, ReviewResult] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_model = {
            executor.submit(
                run_single_review,
                model,
                pr_info,
                api_key,
                base_url,
                skill_trigger,
            ): model
            for model in models
        }

        for future in concurrent.futures.as_completed(future_to_model):
            model = future_to_model[future]
            try:
                result = future.result()
                results[model] = result
            except Exception as e:
                logger.error(f"Exception for {model.display_name}: {e}")
                results[model] = ReviewResult(
                    model=model,
                    review_text="",
                    error=str(e),
                )

    successful = sum(1 for r in results.values() if not r.error)
    logger.info(f"Completed {successful}/{len(models)} reviews successfully")

    return results


def consolidate_reviews(reviews: dict[ReviewerModel, ReviewResult]) -> str:
    """Consolidate multiple reviews into a single summary.

    Args:
        reviews: Dictionary mapping models to their review results

    Returns:
        Consolidated review summary
    """
    lines = ["# Consolidated Code Reviews\n"]

    for model, result in reviews.items():
        lines.append(f"## Review from {model.display_name}\n")

        if result.error:
            lines.append(f"*Error during review: {result.error}*\n")
        elif result.review_text:
            lines.append(result.review_text)
        else:
            lines.append("*No review content generated.*\n")

        lines.append("\n---\n")

    return "\n".join(lines)
