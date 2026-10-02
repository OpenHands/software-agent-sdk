"""Executor for the start_child_conversation tool."""

import json
import os
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from pydantic import ValidationError

from openhands.sdk.conversation.request import (
    StartChildConversationRequest,
    StartChildConversationResponse,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.child_conversation.definition import (
    StartChildConversationAction,
    StartChildConversationObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


logger = get_logger(__name__)

# Same seam ``LookupSecret`` uses to reach the agent-server that runs this tool.
INTERNAL_SERVER_URL_ENV = "OH_INTERNAL_SERVER_URL"
DEFAULT_INTERNAL_SERVER_URL = "http://127.0.0.1:8000"
# v1 and v0 names of the agent-server session key, as exported by the server.
SESSION_API_KEY_ENVS = ("OH_SESSION_API_KEYS_0", "SESSION_API_KEY")
SESSION_API_KEY_HEADER = "X-Session-API-Key"
# A launch may include worktree or sandbox provisioning; mirror the MCP tool
# timeout rather than httpx's 5 second default.
CHILD_LAUNCH_TIMEOUT_SECONDS = 300.0


def default_launch_url(parent_conversation_id: UUID) -> str:
    """Child-launch endpoint of the agent-server running this tool."""
    base = os.getenv(INTERNAL_SERVER_URL_ENV, DEFAULT_INTERNAL_SERVER_URL).rstrip("/")
    return f"{base}/api/conversations/{parent_conversation_id}/children"


def session_api_key_headers() -> dict[str, str]:
    """Auth header for the launcher, taken from the agent-server's own env."""
    for env_name in SESSION_API_KEY_ENVS:
        value = os.getenv(env_name)
        if value:
            return {SESSION_API_KEY_HEADER: value}
    return {}


def _response_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, str) and detail:
        return detail
    if detail is not None:
        return json.dumps(detail)
    return response.text[:500]


class StartChildConversationExecutor(
    ToolExecutor[StartChildConversationAction, StartChildConversationObservation]
):
    """POST the launch request to the child-launch endpoint and report back.

    The executor is deliberately backend-agnostic: it only knows the wire
    contract (``StartChildConversationRequest`` in,
    ``StartChildConversationResponse`` out) and where to send it. By default it
    targets the agent-server that runs this tool, authenticated with that
    server's own session API key from the environment.
    """

    def __init__(
        self,
        parent_conversation_id: UUID,
        launch_url: str | None = None,
        timeout: float = CHILD_LAUNCH_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self.parent_conversation_id = parent_conversation_id
        self.launch_url = launch_url
        self.timeout = timeout
        self._transport = transport

    def __call__(
        self,
        action: StartChildConversationAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> StartChildConversationObservation:
        url = self.launch_url or default_launch_url(self.parent_conversation_id)
        try:
            payload = StartChildConversationRequest(
                task=action.task, title=action.title, isolation=action.isolation
            ).model_dump(mode="json")
        except ValidationError as exc:
            return self._error(f"Invalid child conversation request: {exc}")

        try:
            with httpx.Client(
                timeout=self.timeout, transport=self._transport
            ) as client:
                response = client.post(
                    url, json=payload, headers=session_api_key_headers()
                )
            response.raise_for_status()
            result = StartChildConversationResponse.model_validate(response.json())
        except httpx.HTTPStatusError as exc:
            return self._error(
                "Child conversation launch failed "
                f"({exc.response.status_code}): {_response_detail(exc.response)}"
            )
        except httpx.TimeoutException:
            return self._error(
                "Child conversation launch timed out after "
                f"{self.timeout:.0f} seconds. The child may still have been "
                "created; check with the user before retrying."
            )
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("start_child_conversation failed against %s: %s", url, exc)
            return self._error(
                f"Child conversation launch failed: {type(exc).__name__}: {exc}"
            )

        summary = (
            f"Started child conversation {result.conversation_id} "
            f"(status: {result.status}).\n"
            f"{json.dumps(result.model_dump(mode='json'), indent=2)}"
        )
        return StartChildConversationObservation.from_text(
            text=summary,
            conversation_id=str(result.conversation_id),
            parent_conversation_id=str(result.parent_conversation_id),
            status=result.status,
            title=result.title,
            url=result.url,
            workspace=result.workspace,
            isolation=result.isolation,
        )

    @staticmethod
    def _error(message: str) -> StartChildConversationObservation:
        return StartChildConversationObservation.from_text(text=message, is_error=True)
