"""Executors for the project coordinator tools.

Both executors talk to the agent-server that runs them, through the same
internal URL and session key as ``start_child_conversation``. The project is
read from the calling conversation's ``project`` tag at call time, so a
conversation can only see and message threads of its own project.
"""

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from openhands.sdk.conversation.types import PROJECT_TAG_KEY
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.child_conversation.impl import (
    internal_server_url,
    response_detail,
    session_api_key_headers,
)
from openhands.tools.project.definition import (
    ListProjectThreadsAction,
    ListProjectThreadsObservation,
    MessageProjectThreadAction,
    MessageProjectThreadObservation,
    ProjectThreadSummary,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


logger = get_logger(__name__)

PROJECT_TOOL_TIMEOUT_SECONDS = 30.0

_NOT_IN_PROJECT = (
    "This conversation is not part of a project: it has no "
    f"'{PROJECT_TAG_KEY}' tag. Ask the person to add it to a project first."
)


class ProjectToolError(Exception):
    """A failure to report to the agent as an error observation."""


class _ProjectClient:
    def __init__(
        self,
        conv_state: "ConversationState",
        server_url: str | None,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ):
        self.conv_state = conv_state
        self.server_url = server_url
        self.timeout = timeout
        self._transport = transport

    @property
    def base_url(self) -> str:
        return (self.server_url or internal_server_url()).rstrip("/")

    def project_id(self) -> str:
        project_id = self.conv_state.tags.get(PROJECT_TAG_KEY)
        if not project_id:
            raise ProjectToolError(_NOT_IN_PROJECT)
        return project_id

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with httpx.Client(
                timeout=self.timeout, transport=self._transport
            ) as client:
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=session_api_key_headers(),
                    **kwargs,
                )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise ProjectToolError(
                f"{method} {path} failed ({exc.response.status_code}): "
                f"{response_detail(exc.response)}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProjectToolError(
                f"{method} {path} timed out after {self.timeout:.0f} seconds."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Project tool request %s %s failed: %s", method, path, exc)
            raise ProjectToolError(
                f"{method} {path} failed: {type(exc).__name__}: {exc}"
            ) from exc

    def threads(self) -> tuple[str, list[ProjectThreadSummary]]:
        project_id = self.project_id()
        response = self.request("GET", f"/api/projects/{project_id}/threads")
        try:
            body = response.json()
            threads = [
                ProjectThreadSummary.model_validate(item)
                for item in body.get("threads", [])
            ]
        except (ValueError, AttributeError, ValidationError) as exc:
            raise ProjectToolError(
                f"Unexpected project threads response: {exc}"
            ) from exc
        # The caller is never its own thread; leave it out when the caller is a
        # thread rather than the coordinator.
        own_id = self.conv_state.id.hex
        return project_id, [t for t in threads if t.conversation_id != own_id]


class ListProjectThreadsExecutor(
    ToolExecutor[ListProjectThreadsAction, ListProjectThreadsObservation]
):
    """GET the caller's project threads and report them."""

    def __init__(
        self,
        conv_state: "ConversationState",
        server_url: str | None = None,
        timeout: float = PROJECT_TOOL_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = _ProjectClient(conv_state, server_url, timeout, transport)

    def __call__(
        self,
        action: ListProjectThreadsAction,  # noqa: ARG002
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> ListProjectThreadsObservation:
        try:
            project_id, threads = self._client.threads()
        except ProjectToolError as exc:
            return ListProjectThreadsObservation.from_text(text=str(exc), is_error=True)

        if threads:
            lines = [
                f"- [{t.attention}] {t.title or '(untitled)'} "
                f"(id: {t.conversation_id}, status: {t.execution_status}"
                + (
                    f", parent: {t.parent_conversation_id}"
                    if t.parent_conversation_id
                    else ""
                )
                + ")"
                for t in threads
            ]
            text = f"Project {project_id} has {len(threads)} threads:\n" + "\n".join(
                lines
            )
        else:
            text = f"Project {project_id} has no threads yet."
        return ListProjectThreadsObservation.from_text(
            text=text, project_id=project_id, threads=threads
        )


class MessageProjectThreadExecutor(
    ToolExecutor[MessageProjectThreadAction, MessageProjectThreadObservation]
):
    """Send a message to a thread of the caller's project.

    The thread must be listed by the project's threads endpoint. A thread
    waiting for confirmation is refused: running it would approve the pending
    action, which is the person's decision. A running thread gets the message
    without a new run request, so a run that reaches a confirmation in the
    meantime is never resumed by this tool.
    """

    def __init__(
        self,
        conv_state: "ConversationState",
        server_url: str | None = None,
        timeout: float = PROJECT_TOOL_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = _ProjectClient(conv_state, server_url, timeout, transport)

    def __call__(
        self,
        action: MessageProjectThreadAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> MessageProjectThreadObservation:
        try:
            try:
                target_id = UUID(action.conversation_id.strip()).hex
            except ValueError:
                raise ProjectToolError(
                    f"{action.conversation_id!r} is not a conversation id."
                )
            _, threads = self._client.threads()
            target = next((t for t in threads if t.conversation_id == target_id), None)
            if target is None:
                raise ProjectToolError(
                    f"{action.conversation_id} is not a thread of this project. "
                    "Use list_project_threads to find the right id."
                )
            if target.execution_status == "waiting_for_confirmation":
                raise ProjectToolError(
                    f"Thread {target.conversation_id} is waiting for the person "
                    "to approve or reject an action. Sending it a message now "
                    "would approve that action, so nothing was sent. Tell the "
                    "person what it is waiting on."
                )
            running = target.execution_status == "running"
            self._client.request(
                "POST",
                f"/api/conversations/{target.conversation_id}/events",
                json={
                    "role": "user",
                    "content": [{"type": "text", "text": action.message}],
                    "run": not running,
                },
            )
        except ProjectToolError as exc:
            return MessageProjectThreadObservation.from_text(
                text=str(exc), is_error=True
            )

        if running:
            outcome = (
                "It was running, so the message joins its current run. If that "
                "run ended just before the message arrived, the message waits "
                "for the thread's next run."
            )
        else:
            outcome = (
                f"It was {target.execution_status}; a run was requested to "
                "handle the message."
            )
        text = (
            f"Message saved to thread {target.conversation_id}. {outcome} "
            "Check list_project_threads before reporting what it did.\n"
            + json.dumps(
                {
                    "conversation_id": target.conversation_id,
                    "previous_status": target.execution_status,
                    "run_requested": not running,
                }
            )
        )
        return MessageProjectThreadObservation.from_text(
            text=text,
            conversation_id=target.conversation_id,
            previous_status=target.execution_status,
        )
