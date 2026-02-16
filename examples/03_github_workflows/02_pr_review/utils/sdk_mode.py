"""SDK Mode - Run PR review locally using the OpenHands SDK.

This module provides the SDK implementation for PR review, running the agent
locally with full control over the LLM configuration.
"""

from __future__ import annotations

import os
from typing import Any

from openhands.sdk import get_logger
from openhands.sdk.conversation import get_agent_final_response

from .agent_util import (
    create_agent,
    create_conversation,
    create_llm,
    print_cost_summary,
    save_laminar_trace,
)


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

    # Create LLM and agent using shared utilities
    llm = create_llm(api_key=api_key)
    cwd = os.getcwd()
    agent = create_agent(llm=llm, workspace_path=cwd)

    # Create conversation with secrets for masking
    secrets: dict[str, str] = {"LLM_API_KEY": api_key, "GITHUB_TOKEN": github_token}
    conversation = create_conversation(agent=agent, workspace=cwd, secrets=secrets)

    logger.info("Starting PR review analysis...")
    logger.info("Agent received the PR diff in the initial message")
    logger.info("Agent will post inline review comments directly via GitHub API")

    # Send message and run the conversation (blocking for local)
    conversation.send_message(prompt)
    conversation.run()

    # Get final response
    response = get_agent_final_response(conversation.state.events)
    if response:
        logger.info(f"Agent final response: {len(response)} characters")

    # Print cost summary and save trace
    print_cost_summary(conversation)
    save_laminar_trace(pr_info, commit_id, review_style)

    logger.info("PR review completed successfully")
