"""FastAPI router for GitHub autospawn webhooks."""

import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.github_autospawn_models import GitHubWebhookConfig
from openhands.agent_server.github_autospawn_service import (
    process_github_webhook,
    verify_github_signature,
)

logger = logging.getLogger(__name__)


def create_github_autospawn_router(
    config: GitHubWebhookConfig,
) -> APIRouter:
    """Create GitHub autospawn router with the given configuration.

    Args:
        config: GitHub webhook configuration including secret and triggers

    Returns:
        Configured APIRouter instance
    """
    router = APIRouter(prefix="/webhooks", tags=["GitHub Autospawn"])

    @router.post("/github", status_code=status.HTTP_200_OK)
    async def github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_hub_signature_256: Annotated[
            str | None, Header(alias="X-Hub-Signature-256")
        ] = None,
        x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
        conversation_service: ConversationService = Depends(get_conversation_service),
    ):
        """Handle GitHub webhook events.

        This endpoint:
        1. Verifies the HMAC signature if a secret is configured
        2. Validates the event type header
        3. Matches the event against configured triggers
        4. Spawns agents in the background for matching triggers
        5. Returns 200 OK immediately to GitHub

        Args:
            request: FastAPI request object
            background_tasks: FastAPI background tasks manager
            x_hub_signature_256: GitHub HMAC signature header
            x_github_event: GitHub event type header
            conversation_service: Conversation service dependency

        Returns:
            Success response with status message

        Raises:
            HTTPException: If signature verification fails or event type is missing
        """
        # Read raw request body for signature verification
        body = await request.body()

        # Verify signature if secret is configured
        if config.github_secret is not None:
            if not x_hub_signature_256:
                logger.warning("Missing X-Hub-Signature-256 header")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing X-Hub-Signature-256 header",
                )

            if not verify_github_signature(
                body, x_hub_signature_256, config.github_secret
            ):
                logger.warning("Invalid webhook signature")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid signature",
                )

        # Validate event type
        if not x_github_event:
            logger.warning("Missing X-GitHub-Event header")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing X-GitHub-Event header",
            )

        # Parse JSON payload
        payload = await request.json()

        # Log the webhook event
        repo = payload.get("repository", {}).get("full_name", "unknown")
        action = payload.get("action", "N/A")
        logger.info(
            "Received GitHub webhook: event=%s action=%s repo=%s",
            x_github_event,
            action,
            repo,
        )

        # Process webhook in background
        background_tasks.add_task(
            process_github_webhook,
            x_github_event,
            payload,
            config,
            conversation_service.start_conversation,
        )

        return {"status": "ok", "message": "Webhook received and processing"}

    return router
