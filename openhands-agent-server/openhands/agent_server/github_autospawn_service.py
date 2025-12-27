"""Service layer for GitHub autospawn webhook processing."""

import asyncio
import hashlib
import hmac
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.agent_server.github_autospawn_models import (
    GitHubTriggerConfig,
    GitHubWebhookConfig,
)
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import DEBUG
from openhands.sdk.workspace import LocalWorkspace

logger = logging.getLogger(__name__)


def verify_github_signature(
    payload_body: bytes, signature_header: str, secret: SecretStr
) -> bool:
    """Verify GitHub webhook signature using HMAC SHA256.

    Args:
        payload_body: Raw request body bytes
        signature_header: Value of X-Hub-Signature-256 header
        secret: GitHub webhook secret

    Returns:
        True if signature is valid, False otherwise
    """
    if not signature_header:
        logger.warning("Missing X-Hub-Signature-256 header")
        return False

    if not signature_header.startswith("sha256="):
        logger.warning("Invalid signature format: %s", signature_header)
        return False

    # Extract the signature hash
    signature_hash = signature_header[7:]  # Remove 'sha256=' prefix

    # Compute expected signature
    secret_bytes = secret.get_secret_value().encode("utf-8")
    expected_signature = hmac.new(
        secret_bytes, payload_body, hashlib.sha256
    ).hexdigest()

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(signature_hash, expected_signature)


def match_triggers(
    event_type: str,
    payload: dict[str, Any],
    triggers: list[GitHubTriggerConfig],
) -> list[GitHubTriggerConfig]:
    """Find triggers that match the webhook event.

    Args:
        event_type: GitHub event type (e.g., 'pull_request')
        payload: Webhook payload dictionary
        triggers: List of configured triggers

    Returns:
        List of matching trigger configurations
    """
    matched = []
    repo_full_name = payload.get("repository", {}).get("full_name")
    action = payload.get("action")
    ref = payload.get("ref", "")

    # Extract branch name from ref (e.g., 'refs/heads/main' -> 'main')
    branch = None
    if ref.startswith("refs/heads/"):
        branch = ref[11:]

    for trigger in triggers:
        # Check event type match
        if trigger.event != event_type:
            continue

        # Check action match (if specified)
        if trigger.action and trigger.action != action:
            continue

        # Check repo match
        if trigger.repo != repo_full_name:
            continue

        # Check branch match (if specified and applicable)
        if trigger.branch and trigger.branch != branch:
            continue

        matched.append(trigger)
        logger.info(
            "Trigger matched: event=%s action=%s repo=%s",
            event_type,
            action,
            repo_full_name,
        )

    return matched


async def clone_repository(
    clone_url: str, workspace_dir: Path, ref: str | None = None
) -> bool:
    """Clone a repository to the specified workspace directory.

    Args:
        clone_url: Git clone URL
        workspace_dir: Directory to clone into
        ref: Optional git ref to checkout (branch, tag, or commit SHA)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Clone the repository
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            clone_url,
            ".",
            cwd=str(workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error("Failed to clone repository: %s", stderr.decode())
            return False

        # Checkout specific ref if provided
        if ref:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "checkout",
                ref,
                cwd=str(workspace_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error("Failed to checkout ref %s: %s", ref, stderr.decode())
                return False

        logger.info("Successfully cloned repository to %s", workspace_dir)
        return True

    except Exception as e:
        logger.error("Error cloning repository: %s", e)
        return False


def get_pr_ref(payload: dict[str, Any]) -> str | None:
    """Extract the appropriate git ref for a pull request event.

    Args:
        payload: GitHub webhook payload

    Returns:
        Git ref to checkout, or None if not applicable
    """
    # For pull_request events, use the head ref
    if "pull_request" in payload:
        pr = payload["pull_request"]
        # Use the head SHA for the exact commit
        return pr.get("head", {}).get("sha")

    return None


async def process_github_webhook(
    event_type: str,
    payload: dict[str, Any],
    config: GitHubWebhookConfig,
    start_conversation_callback,
) -> int:
    """Process a GitHub webhook event and spawn agents for matching triggers.

    Args:
        event_type: GitHub event type
        payload: Webhook payload
        config: GitHub webhook configuration
        start_conversation_callback: Async function to call to start a conversation
            (typically conversation_service.start_conversation)

    Returns:
        Number of agents spawned
    """
    # Find matching triggers
    matching_triggers = match_triggers(event_type, payload, config.triggers)

    if not matching_triggers:
        logger.info("No triggers matched for event %s", event_type)
        return 0

    # Extract repository info
    repo_url = payload.get("repository", {}).get("clone_url")
    if not repo_url:
        logger.warning("No clone_url found in webhook payload")
        return 0

    # Get PR ref if applicable
    git_ref = get_pr_ref(payload)

    spawned_count = 0

    # Process each matching trigger
    for trigger in matching_triggers:
        workspace_dir = None
        try:
            # Create workspace directory
            if config.workspace_base_dir:
                base_dir = Path(config.workspace_base_dir)
                base_dir.mkdir(parents=True, exist_ok=True)
                workspace_dir = Path(
                    tempfile.mkdtemp(prefix="openhands_autospawn_", dir=base_dir)
                )
            else:
                workspace_dir = Path(tempfile.mkdtemp(prefix="openhands_autospawn_"))

            logger.info("Created workspace: %s", workspace_dir)

            # Clone repository
            clone_success = await clone_repository(repo_url, workspace_dir, git_ref)
            if not clone_success:
                logger.error("Failed to clone repository, skipping trigger")
                continue

            # Create conversation request
            request = StartConversationRequest(
                agent=trigger.agent_config.agent,
                workspace=LocalWorkspace(workspace_path=str(workspace_dir)),
                initial_message={
                    "role": "user",
                    "content": [{"type": "text", "text": trigger.agent_config.task}],
                },
                max_iterations=trigger.agent_config.max_iterations,
            )

            # Start conversation
            conversation_info = await start_conversation_callback(request)
            logger.info(
                "Started conversation %s for trigger", conversation_info.id
            )

            spawned_count += 1

            # Cleanup workspace if configured
            # Note: This happens immediately after starting the conversation.
            # The conversation continues to run asynchronously.
            # If we want to cleanup after the conversation completes,
            # we'd need to add a callback or polling mechanism.
            if config.cleanup_on_success:
                if workspace_dir and workspace_dir.exists():
                    shutil.rmtree(workspace_dir)
                    logger.info("Cleaned up workspace: %s", workspace_dir)

        except Exception as e:
            logger.error("Error processing trigger: %s", e, exc_info=True)

            # Cleanup on failure based on config
            cleanup_needed = config.cleanup_on_failure or (
                not DEBUG and config.cleanup_on_success
            )
            if workspace_dir and workspace_dir.exists() and cleanup_needed:
                shutil.rmtree(workspace_dir)
                logger.info("Cleaned up workspace after error: %s", workspace_dir)
            elif workspace_dir and workspace_dir.exists():
                logger.info("Workspace kept for debugging: %s", workspace_dir)

    return spawned_count
