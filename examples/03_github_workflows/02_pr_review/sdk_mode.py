"""SDK Mode - Run PR review locally using the OpenHands SDK.

This module provides the SDK implementation for PR review, running the agent
locally with full control over the LLM configuration.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from lmnr import Laminar

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.context.skills import load_project_skills
from openhands.sdk.conversation import get_agent_final_response
from openhands.tools.preset.default import get_default_condenser, get_default_tools


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation

logger = get_logger(__name__)


def run_agent_review(
    prompt: str,
    pr_info: dict[str, Any],
    commit_id: str,
    review_style: str,
) -> None:
    """Run PR review using the SDK (local execution).

    Args:
        prompt: The formatted review prompt
        pr_info: PR information dict with keys: number, title, body, repo_name,
                 base_branch, head_branch
        commit_id: The HEAD commit SHA
        review_style: Review style ('standard' or 'roasted')
    """
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable is required for SDK mode")

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm_config: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "usage_id": "pr_review_agent",
        "drop_params": True,
    }
    if base_url:
        llm_config["base_url"] = base_url

    llm = LLM(**llm_config)

    cwd = os.getcwd()

    # Load project-specific skills from the repository being reviewed
    project_skills = load_project_skills(cwd)
    logger.info(
        f"Loaded {len(project_skills)} project skills: "
        f"{[s.name for s in project_skills]}"
    )

    # Create AgentContext with public skills enabled and project skills
    agent_context = AgentContext(
        load_public_skills=True,
        skills=project_skills,
    )

    # Create agent with default tools and agent context
    agent = Agent(
        llm=llm,
        tools=get_default_tools(enable_browser=False),
        agent_context=agent_context,
        system_prompt_kwargs={"cli_mode": True},
        condenser=get_default_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )

    # Create conversation with secrets for masking
    secrets: dict[str, str] = {}
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
    logger.info("Agent will post inline review comments directly via GitHub API")

    # Send the prompt and run the agent
    conversation.send_message(prompt)
    conversation.run()

    # Log the final response for debugging purposes
    review_content = get_agent_final_response(conversation.state.events)
    if review_content:
        logger.info(f"Agent final response: {len(review_content)} characters")

    _print_cost_summary(conversation)
    _save_laminar_trace(pr_info, commit_id, review_style)

    logger.info("PR review completed successfully")


def _print_cost_summary(conversation: BaseConversation) -> None:
    """Print cost information for CI output."""
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


def _save_laminar_trace(
    pr_info: dict[str, Any], commit_id: str, review_style: str
) -> None:
    """Save Laminar trace info for delayed evaluation."""
    trace_id = Laminar.get_trace_id()
    laminar_span_context = Laminar.get_laminar_span_context()
    span_context = (
        laminar_span_context.model_dump(mode="json") if laminar_span_context else None
    )

    if trace_id and laminar_span_context:
        # Set trace metadata within an active span context
        with Laminar.start_as_current_span(
            name="pr-review-metadata",
            parent_span_context=laminar_span_context,
        ) as _:
            pr_url = (
                f"https://github.com/{pr_info['repo_name']}/pull/{pr_info['number']}"
            )
            Laminar.set_trace_metadata(
                {
                    "pr_number": pr_info["number"],
                    "repo_name": pr_info["repo_name"],
                    "pr_url": pr_url,
                    "workflow_phase": "review",
                    "review_style": review_style,
                }
            )

        # Store trace context in file for GitHub artifact upload
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
        logger.warning("No Laminar trace ID found - observability may not be enabled")
