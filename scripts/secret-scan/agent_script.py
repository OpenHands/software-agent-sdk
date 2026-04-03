#!/usr/bin/env python3
"""
Secret Scan Orchestrator Agent

Orchestrator that scans Datadog logs and GCS buckets for leaked secrets,
reports findings on a GitHub tracking issue, and delegates fix PRs to
sub-agents (one PR per sub-agent).

Uses the OpenHands SDK with DelegateTool for sub-agent orchestration.

Environment Variables:
    LLM_API_KEY:      API key for the LLM (required)
    LLM_MODEL:        Model name (default: anthropic/claude-sonnet-4-5-20250929)
    LLM_BASE_URL:     Optional base URL for LLM API
    FROM_TIMESTAMP:   Start of scan window (ISO 8601)
    TO_TIMESTAMP:     End of scan window (ISO 8601)
    TRACKING_REPO:    GitHub repo for tracking issue (e.g., OpenHands/evaluation)
    TRACKING_ISSUE:   Issue number to post findings on
    GCS_BUCKETS:      Comma-separated GCS bucket names
    DD_API_KEY:       Datadog API key
    DD_APP_KEY:       Datadog Application key
    GITHUB_TOKEN:     GitHub token for issue comments and PR creation
"""

import os
import sys

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Tool,
    agent_definition_to_factory,
    get_logger,
    register_agent,
)
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.subagent import AgentDefinition
from openhands.sdk.tool import register_tool
from openhands.tools.delegate import DelegateTool, DelegationVisualizer
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool

from prompt import (
    ORCHESTRATOR_PROMPT,
    SECRET_FIXER_SYSTEM_PROMPT,
)

logger = get_logger(__name__)


def main():
    # --- Validate required env vars ---
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        logger.error("LLM_API_KEY is not set.")
        sys.exit(1)

    from_ts = os.getenv("FROM_TIMESTAMP")
    to_ts = os.getenv("TO_TIMESTAMP")
    tracking_repo = os.getenv("TRACKING_REPO")
    tracking_issue = os.getenv("TRACKING_ISSUE")
    gcs_buckets = os.getenv("GCS_BUCKETS", "")

    if not all([from_ts, to_ts, tracking_repo, tracking_issue]):
        logger.error(
            "FROM_TIMESTAMP, TO_TIMESTAMP, TRACKING_REPO, and TRACKING_ISSUE "
            "must all be set."
        )
        sys.exit(1)

    # --- Configure LLM ---
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm_kwargs = {
        "model": model,
        "api_key": api_key,
        "usage_id": "secret-scan-orchestrator",
        "drop_params": True,
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    llm = LLM(**llm_kwargs)

    # --- Register the secret-fixer sub-agent ---
    secret_fixer = AgentDefinition(
        name="secret-fixer",
        description=(
            "Fixes a single secret leak in a repository by cloning it, "
            "applying a minimal code fix, and creating a pull request. "
            "Give it: the repo (owner/repo), the file path, a description "
            "of the leak, and what the fix should do. It returns the PR URL."
        ),
        tools=["terminal", "file_editor"],
        system_prompt=SECRET_FIXER_SYSTEM_PROMPT,
    )

    register_agent(
        name=secret_fixer.name,
        factory_func=agent_definition_to_factory(secret_fixer),
        description=secret_fixer,
    )

    # --- Register DelegateTool ---
    register_tool("DelegateTool", DelegateTool)

    # --- Build orchestrator agent ---
    # The orchestrator has: terminal (for running scan scripts + gh CLI),
    # file_editor (for reading scan results), and DelegateTool (for sub-agents).
    orchestrator = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name="DelegateTool"),
        ],
        condenser=LLMSummarizingCondenser(
            llm=llm.model_copy(update={"usage_id": "condenser"}),
            max_size=80,
            keep_first=4,
        ),
    )

    # --- Create conversation ---
    cwd = os.getcwd()
    conversation = Conversation(
        agent=orchestrator,
        workspace=cwd,
        visualizer=DelegationVisualizer(name="SecretScanOrchestrator"),
    )

    # --- Format the orchestrator prompt ---
    prompt = ORCHESTRATOR_PROMPT.format(
        from_ts=from_ts,
        to_ts=to_ts,
        tracking_repo=tracking_repo,
        tracking_issue=tracking_issue,
        gcs_buckets=gcs_buckets,
    )

    logger.info("Starting secret scan orchestrator...")
    logger.info(f"Scan window: {from_ts} -> {to_ts}")
    logger.info(f"Tracking issue: {tracking_repo}#{tracking_issue}")

    # --- Run ---
    conversation.send_message(prompt)
    conversation.run()

    # --- Report cost ---
    cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
    logger.info(f"Total cost: ${cost:.4f}")
    print(f"SCAN_COST: {cost:.4f}")

    logger.info("Secret scan orchestrator completed.")


if __name__ == "__main__":
    main()
