"""Shared agent utilities for PR review.

This module provides shared abstractions for creating and configuring the
OpenHands agent used in PR review, including LLM configuration, AgentContext,
and conversation management.
"""

from __future__ import annotations

import json
import os
from typing import Any

from lmnr import Laminar

from openhands.sdk import LLM, Agent, AgentContext, Conversation, get_logger
from openhands.sdk.context.skills import load_project_skills
from openhands.sdk.conversation.base import BaseConversation
from openhands.tools.preset.default import get_default_condenser, get_default_tools


logger = get_logger(__name__)


def create_llm(
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LLM:
    """Create an LLM instance with the given configuration.

    Args:
        api_key: LLM API key (defaults to LLM_API_KEY env var)
        model: Model name (defaults to LLM_MODEL env var or claude-sonnet-4-5)
        base_url: Base URL for LLM API (defaults to LLM_BASE_URL env var)

    Returns:
        Configured LLM instance
    """
    api_key = api_key or os.getenv("LLM_API_KEY")
    model = model or os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = base_url or os.getenv("LLM_BASE_URL")

    llm_config: dict[str, Any] = {
        "model": model,
        "usage_id": "pr_review_agent",
        "drop_params": True,
    }
    if api_key:
        llm_config["api_key"] = api_key
    if base_url:
        llm_config["base_url"] = base_url

    return LLM(**llm_config)


def create_agent(llm: LLM, workspace_path: str | None = None) -> Agent:
    """Create an Agent instance with default tools and project skills.

    Args:
        llm: LLM instance to use
        workspace_path: Path to workspace for loading project skills (defaults to cwd)

    Returns:
        Configured Agent instance
    """
    workspace_path = workspace_path or os.getcwd()

    # Load project-specific skills from the repository being reviewed
    project_skills = load_project_skills(workspace_path)
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
    return Agent(
        llm=llm,
        tools=get_default_tools(enable_browser=False),
        agent_context=agent_context,
        system_prompt_kwargs={"cli_mode": True},
        condenser=get_default_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )


def create_conversation(
    agent: Agent,
    workspace: Any,
    secrets: dict[str, str] | None = None,
) -> BaseConversation:
    """Create a Conversation instance.

    Args:
        agent: Agent instance to use
        workspace: Workspace path (str) or Workspace instance
        secrets: Secrets to mask in agent output

    Returns:
        Configured Conversation instance (LocalConversation or RemoteConversation)
    """
    # Conversation is a factory that returns LocalConversation or RemoteConversation
    # based on the workspace type
    conv: BaseConversation = Conversation(  # type: ignore[assignment]
        agent=agent,
        workspace=workspace,
        secrets=secrets or {},
    )
    return conv


def print_cost_summary(conversation: BaseConversation) -> None:
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


def save_laminar_trace(
    pr_info: dict[str, Any],
    commit_id: str,
    review_style: str,
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
