"""Cancellable, same-backend coordination tools for child conversations."""

import asyncio
import json
import os
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar, Self
from uuid import UUID

import httpx

from openhands.sdk.conversation.child_wait import (
    WaitForChildrenRequest,
    WaitForChildrenResponse,
)
from openhands.sdk.event.mailbox import MailboxSendRequest, MailboxSendResponse
from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.tool import Action, Observation, ToolDefinition, ToolExecutor
from openhands.tools.child_conversation.impl import (
    DEFAULT_INTERNAL_SERVER_URL,
    INTERNAL_SERVER_URL_ENV,
    session_api_key_headers,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import ConversationState, LocalConversation


class WaitForChildrenAction(WaitForChildrenRequest, Action):
    """Await existing direct children and receive results through the mailbox."""


class WaitForChildrenObservation(Observation):
    result: WaitForChildrenResponse | None = None


class SendMailboxMessageAction(Action):
    """Send typed agent input within this conversation's parent/child tree."""

    message: MailboxSendRequest


class SendMailboxMessageObservation(Observation):
    result: MailboxSendResponse | None = None


class _CoordinationExecutor(ToolExecutor):
    endpoint: ClassVar[str]
    observation_type: ClassVar[type[Observation]]
    response_type: ClassVar[type[WaitForChildrenResponse] | type[MailboxSendResponse]]

    def __init__(
        self,
        conversation_id: UUID,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.conversation_id = conversation_id
        self.transport = transport
        self._lock = threading.Lock()
        self._tasks: dict[asyncio.Task, asyncio.AbstractEventLoop] = {}

    def __call__(self, action: Action, conversation=None) -> Observation:
        if not isinstance(action, (WaitForChildrenAction, SendMailboxMessageAction)):
            raise TypeError("Unsupported conversation coordination action")
        try:
            return asyncio.run(self._call(action, conversation))
        except asyncio.CancelledError:
            return self.observation_type.from_text(
                text="Conversation coordination was interrupted.", is_error=True
            )

    async def _call(
        self,
        action: WaitForChildrenAction | SendMailboxMessageAction,
        conversation: "LocalConversation | None",
    ) -> Observation:
        task = asyncio.current_task()
        assert task is not None
        with self._lock:
            self._tasks[task] = asyncio.get_running_loop()
        try:
            if conversation is not None:
                token = conversation.cancel_token
                if token is not None and token.is_cancelled:
                    raise asyncio.CancelledError
            timeout = (
                action.timeout_seconds + 30
                if isinstance(action, WaitForChildrenAction)
                else 30
            )
            base = os.getenv(
                INTERNAL_SERVER_URL_ENV, DEFAULT_INTERNAL_SERVER_URL
            ).rstrip("/")
            url = f"{base}/api/conversations/{self.conversation_id}/{self.endpoint}"
            async with httpx.AsyncClient(
                timeout=timeout, transport=self.transport, trust_env=False
            ) as client:
                response = await client.post(
                    url,
                    json=WaitForChildrenRequest.model_validate(
                        action.model_dump(
                            include=set(WaitForChildrenRequest.model_fields)
                        )
                    ).model_dump(mode="json")
                    if isinstance(action, WaitForChildrenAction)
                    else action.message.model_dump(mode="json"),
                    headers=session_api_key_headers(),
                )
                response.raise_for_status()
                result = self.response_type.model_validate(response.json())
            return self.observation_type.from_text(
                text=json.dumps(result.model_dump(mode="json")), result=result
            )
        except httpx.HTTPStatusError as exc:
            return self.observation_type.from_text(
                text=(
                    "Conversation coordination request rejected "
                    f"(HTTP {exc.response.status_code})."
                ),
                is_error=True,
            )
        except (httpx.HTTPError, ValueError):
            return self.observation_type.from_text(
                text=(
                    "Conversation coordination request failed; "
                    "retrying with the same IDs is safe."
                ),
                is_error=True,
            )
        finally:
            with self._lock:
                self._tasks.pop(task, None)

    def interrupt(self) -> None:
        with self._lock:
            for task, loop in self._tasks.items():
                if not loop.is_closed():
                    loop.call_soon_threadsafe(task.cancel)


class WaitForChildrenExecutor(_CoordinationExecutor):
    endpoint = "children/wait"
    observation_type = WaitForChildrenObservation
    response_type = WaitForChildrenResponse


class SendMailboxMessageExecutor(_CoordinationExecutor):
    endpoint = "mailbox"
    observation_type = SendMailboxMessageObservation
    response_type = MailboxSendResponse


class WaitForChildrenTool(
    ToolDefinition[WaitForChildrenAction, WaitForChildrenObservation]
):
    user_selectable: ClassVar[bool] = False
    releases_state_lock: ClassVar[bool] = True

    @classmethod
    def create(cls, conv_state: "ConversationState", **params) -> Sequence[Self]:
        if params:
            raise ValueError("WaitForChildrenTool does not accept parameters")
        return [
            cls(
                description=(
                    "Wait once for 1-64 existing direct child conversation IDs "
                    "on this backend. The wait releases this conversation's state "
                    "lock and is bounded by timeout_seconds (0-300, default 60). "
                    "Timeout returns current status and does not fail the turn. "
                    "Settled child results arrive as typed mailbox messages; the "
                    "tool returns their message IDs. Choose whether completed "
                    "children are deleted and timed-out children are retained, "
                    "stopped or deleted. Retain children if their history will be "
                    "needed. This does not launch children."
                ),
                action_type=WaitForChildrenAction,
                observation_type=WaitForChildrenObservation,
                executor=WaitForChildrenExecutor(conv_state.id),
            )
        ]


class SendMailboxMessageTool(
    ToolDefinition[SendMailboxMessageAction, SendMailboxMessageObservation]
):
    user_selectable: ClassVar[bool] = False
    releases_state_lock: ClassVar[bool] = True

    @classmethod
    def create(cls, conv_state: "ConversationState", **params) -> Sequence[Self]:
        if params:
            raise ValueError("SendMailboxMessageTool does not accept parameters")
        return [
            cls(
                description=(
                    "Send a durable typed message to a parent, child or sibling "
                    "conversation on the same backend, without a browser. Choose "
                    "question, proposal, review-request, blocker or decision; "
                    "proposal/review-request/blocker require evidence. Use one "
                    "stable idempotency_key per intended message and reuse it on "
                    "retries. Default steer preserves in-flight work and active "
                    "goals. Choose interrupt only to explicitly stop the "
                    "recipient's run and goal; include the reason in body. "
                    "Sending does not start an idle recipient."
                ),
                action_type=SendMailboxMessageAction,
                observation_type=SendMailboxMessageObservation,
                executor=SendMailboxMessageExecutor(conv_state.id),
            )
        ]


register_tool(WaitForChildrenTool.name, WaitForChildrenTool)
register_tool(SendMailboxMessageTool.name, SendMailboxMessageTool)
