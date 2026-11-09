"""A curated local MCP interface to the agent-server conversation service."""

from hmac import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openhands.agent_server.agent_profiles_router import active_agent_profile
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk import Message, TextContent
from openhands.sdk.conversation.request import (
    SendMessageRequest,
    StartConversationRequest,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.utils.redact import redact_text_secrets
from openhands.sdk.workspace import LocalWorkspace


class LocalSessionAuth:
    """Use the agent server's existing session key, never a public OAuth token."""

    def __init__(self, app: ASGIApp, parent: FastAPI):
        self.app = app
        self.parent = parent

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        config: Config = self.parent.state.config
        if not config.session_api_keys:
            response = JSONResponse({"detail": "MCP requires a session key"}, 503)
        else:
            headers = Headers(scope=scope)
            authorization = headers.get("authorization", "")
            bearer = (
                authorization[7:] if authorization.lower().startswith("bearer ") else ""
            )
            key = bearer or headers.get("x-session-api-key", "")
            if any(
                compare_digest(key, expected) for expected in config.session_api_keys
            ):
                init = getattr(self.parent.state, "init_service", None)
                if init is None or init.state == "ready":
                    await self.app(scope, receive, send)
                    return
                response = JSONResponse({"detail": "Server is initializing"}, 503)
            else:
                response = JSONResponse(
                    {"detail": "Unauthorized"},
                    401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        await response(scope, receive, send)


def create_conversation_mcp(parent: FastAPI) -> tuple[FastMCP, ASGIApp]:
    """Mount-ready MCP app sharing the parent's conversation service and config."""
    mcp = FastMCP("OpenHands", mask_error_details=True)

    def service() -> ConversationService:
        result: ConversationService | None = getattr(
            parent.state, "conversation_service", None
        )
        if result is None:
            raise RuntimeError("Conversation service is not ready")
        return result

    async def resolve(handle: str):
        try:
            conversation_id = UUID(handle)
        except ValueError:
            raise ValueError("Invalid conversation handle") from None
        conversation = await service().get_conversation(conversation_id)
        if conversation is None:
            raise ValueError("Conversation not found")
        return conversation

    @mcp.tool(
        annotations=ToolAnnotations.model_validate(
            {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}
        )
    )
    async def create_conversation(
        prompt: Annotated[str, Field(min_length=1, max_length=4000)],
    ) -> dict[str, str]:
        """Start a coding task; agent actions require approval in OpenHands."""
        config: Config = parent.state.config
        profile = active_agent_profile(config, config.cipher)
        if not isinstance(profile, OpenHandsAgentProfile):
            raise ValueError("An active OpenHands agent profile is required")
        settings = get_settings_store(config).load() or PersistedSettings()
        request = settings.conversation_settings.create_request(
            StartConversationRequest,
            agent_profile_id=profile.id,
            workspace=LocalWorkspace(working_dir=config.workspace_path),
            initial_message=SendMessageRequest(
                role="user", content=[TextContent(text=prompt)], run=True
            ),
            confirmation_policy=AlwaysConfirm(),
            max_iterations=50,
            max_budget_per_run=5.0,
        )
        conversation, _ = await service().start_conversation(request)
        return {"handle": str(conversation.id)}

    @mcp.tool(
        annotations=ToolAnnotations.model_validate(
            {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        )
    )
    async def get_conversation(handle: str) -> dict[str, str | None]:
        """Get task state and final answer without exposing agent settings."""
        conversation = await resolve(handle)
        execution = conversation.execution_status
        states = {
            ConversationExecutionStatus.IDLE: "starting",
            ConversationExecutionStatus.RUNNING: "running",
            ConversationExecutionStatus.WAITING_FOR_CONFIRMATION: (
                "waiting_for_approval"
            ),
            ConversationExecutionStatus.FINISHED: "finished",
            ConversationExecutionStatus.ERROR: "error",
            ConversationExecutionStatus.STUCK: "error",
            ConversationExecutionStatus.PAUSED: "running",
        }
        state = states.get(execution, "running")
        answer = None
        if state == "finished":
            events = await service().get_event_service(conversation.id)
            if events is not None:
                answer = redact_text_secrets(await events.get_agent_final_response())[
                    :4000
                ]
        config: Config = parent.state.config
        url = (
            f"{config.web_url.rstrip('/')}/conversations/{conversation.id}"
            if config.web_url
            else None
        )
        return {"handle": handle, "state": state, "response": answer, "url": url}

    @mcp.tool(
        annotations=ToolAnnotations.model_validate(
            {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}
        )
    )
    async def send_message(
        handle: str, message: Annotated[str, Field(min_length=1, max_length=4000)]
    ) -> dict[str, bool]:
        """Send a follow-up to an existing idle or finished conversation."""
        conversation = await resolve(handle)
        if not isinstance(conversation.confirmation_policy, AlwaysConfirm):
            raise ValueError("Follow-ups require action approval")
        if conversation.execution_status not in (
            ConversationExecutionStatus.FINISHED,
            ConversationExecutionStatus.IDLE,
        ):
            raise ValueError("Conversation is not ready for a follow-up")
        events = await service().get_event_service(conversation.id)
        if events is None:
            raise ValueError("Conversation is not available")
        await events.send_message(
            Message(role="user", content=[TextContent(text=message)]), run=True
        )
        return {"accepted": True}

    app = mcp.http_app(
        path="/",
        stateless_http=True,
        json_response=True,
        middleware=[Middleware(LocalSessionAuth, parent=parent)],
    )
    return mcp, app
