"""Executors for ``find_conversations`` and ``read_conversation``.

Both read the agent-server that runs the tool over its own REST API, the seam
``start_child_conversation`` uses, so they work the same in a local server and
in a runtime's server. They only use read routes.
"""

import os
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx

from openhands.sdk.conversation.types import (
    CONVERSATION_MENTION_SCHEME,
    conversation_mention,
)
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.child_conversation.impl import (
    DEFAULT_INTERNAL_SERVER_URL,
    INTERNAL_SERVER_URL_ENV,
    session_api_key_headers,
)
from openhands.tools.conversation_mentions.definition import (
    ConversationMentionsObservation,
    FindConversationsAction,
    ReadConversationAction,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100
"""Conversations per search page (the route's maximum)."""
SEARCH_PAGES = 5
"""Most pages ``find_conversations`` reads, newest activity first."""
MESSAGE_TEXT_MAX = 2000
"""Longest message text ``read_conversation`` shows; longer ones are cut."""

_MENTION_OR_ID = re.compile(
    rf"(?:{re.escape(CONVERSATION_MENTION_SCHEME)})?"
    r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})"
)


class _ToolError(Exception):
    pass


class _NotFound(_ToolError):
    pass


class _ServerClient:
    """A small client for the agent-server that runs this tool."""

    def __init__(
        self,
        server_url: str | None,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ):
        self._server_url = server_url
        self._timeout = timeout
        self._transport = transport

    def get(self, path: str, **params: Any) -> Any:
        base = (
            self._server_url
            or os.getenv(INTERNAL_SERVER_URL_ENV, DEFAULT_INTERNAL_SERVER_URL)
        ).rstrip("/")
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.get(
                    f"{base}{path}",
                    params={k: v for k, v in params.items() if v is not None},
                    headers=session_api_key_headers(),
                )
        except httpx.HTTPError as exc:
            raise _ToolError(
                f"Could not reach the server: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code == 404:
            raise _NotFound("not found")
        if response.status_code >= 400:
            raise _ToolError(
                f"The server answered {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise _ToolError(f"Unexpected server response: {exc}") from exc


def _error(text: str) -> ConversationMentionsObservation:
    return ConversationMentionsObservation.from_text(text=text, is_error=True)


class FindConversationsExecutor(
    ToolExecutor[FindConversationsAction, ConversationMentionsObservation]
):
    """List the user's conversations whose titles hold every query word."""

    def __init__(
        self,
        own_conversation_id: UUID | None = None,
        server_url: str | None = None,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._own_id = own_conversation_id
        self._client = _ServerClient(server_url, timeout, transport)

    def __call__(
        self,
        action: FindConversationsAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> ConversationMentionsObservation:
        words = action.query.lower().split()
        found: list[dict[str, Any]] = []
        page_id: str | None = None
        try:
            for _ in range(SEARCH_PAGES):
                page = self._client.get(
                    "/api/conversations/search",
                    limit=PAGE_SIZE,
                    sort_order="UPDATED_AT_DESC",
                    page_id=page_id,
                )
                for item in page.get("items", []):
                    if self._own_id is not None and _same_id(
                        item.get("id"), self._own_id
                    ):
                        continue
                    title = (item.get("title") or "").lower()
                    if all(word in title for word in words):
                        found.append(item)
                page_id = page.get("next_page_id")
                if len(found) >= action.limit or not page_id:
                    break
        except (_ToolError, AttributeError, TypeError) as exc:
            return _error(f"Could not list conversations: {exc}")

        found = found[: action.limit]
        query = action.query.strip()
        if not found:
            return ConversationMentionsObservation.from_text(
                f"No conversation title matches {query!r}."
                if query
                else "There are no other conversations."
            )
        heading = (
            f"Conversations whose title matches {query!r}"
            if query
            else "Latest conversations"
        )
        lines = [_conversation_line(item) for item in found]
        return ConversationMentionsObservation.from_text(
            f"{heading}, newest activity first:\n" + "\n".join(lines)
        )


class ReadConversationExecutor(
    ToolExecutor[ReadConversationAction, ConversationMentionsObservation]
):
    """Read a conversation's latest messages and final answers."""

    def __init__(
        self,
        server_url: str | None = None,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = _ServerClient(server_url, timeout, transport)

    def __call__(
        self,
        action: ReadConversationAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> ConversationMentionsObservation:
        match = _MENTION_OR_ID.search(action.conversation)
        if match is None:
            return _error(
                f"{action.conversation!r} is not a conversation mention or id."
            )
        hex_id = UUID(match.group(1)).hex
        try:
            info = self._client.get(f"/api/conversations/{hex_id}")
            messages = self._messages(hex_id, action.limit)
        except _NotFound:
            return _error(f"There is no conversation {hex_id}.")
        except _ToolError as exc:
            return _error(f"Could not read conversation {hex_id}: {exc}")
        except (AttributeError, TypeError, KeyError) as exc:
            return _error(f"Unexpected server response: {exc}")

        mention = conversation_mention(info.get("title"), hex_id)
        status = info.get("execution_status") or "unknown"
        if not messages:
            return ConversationMentionsObservation.from_text(
                f"{mention} ({status}) has no messages yet."
            )
        return ConversationMentionsObservation.from_text(
            f"Latest {len(messages)} messages of {mention} ({status}), "
            "oldest first:\n\n" + "\n\n".join(messages)
        )

    def _messages(self, hex_id: str, limit: int) -> list[str]:
        path = f"/api/conversations/{hex_id}/events/search"
        events = [
            *self._client.get(
                path, kind="MessageEvent", sort_order="TIMESTAMP_DESC", limit=limit
            )["items"],
            *self._client.get(
                path,
                kind="ActionEvent",
                source="agent",
                sort_order="TIMESTAMP_DESC",
                limit=100,
            )["items"],
        ]
        lines = [
            (event["timestamp"], line)
            for event in events
            if (line := _message_line(event)) is not None
        ]
        lines.sort(key=lambda pair: pair[0])
        return [line for _, line in lines[-limit:]]


def _same_id(value: Any, own_id: UUID) -> bool:
    try:
        return UUID(str(value)) == own_id
    except ValueError:
        return False


def _conversation_line(item: dict[str, Any]) -> str:
    mention = conversation_mention(item.get("title"), item["id"])
    status = item.get("execution_status") or "unknown"
    updated = str(item.get("updated_at") or "")[:16].replace("T", " ")
    return f"- {mention}: {status}, last active {updated} UTC"


def _message_line(event: dict[str, Any]) -> str | None:
    """One transcript line for a message or a ``finish``; None for the rest."""
    kind = event.get("kind")
    if kind == "MessageEvent":
        message = event.get("llm_message") or {}
        text = "".join(
            part.get("text", "")
            for part in message.get("content") or []
            if part.get("type") == "text"
        )
        source = event.get("source")
        sender = event.get("sender")
        who = (
            f"{source}, from {sender}"
            if sender
            else "the user"
            if source == "user"
            else str(source)
        )
    elif (
        kind == "ActionEvent"
        and (event.get("action") or {}).get("kind") == "FinishAction"
    ):
        text = event["action"].get("message") or ""
        who = "agent (finish)"
    else:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > MESSAGE_TEXT_MAX:
        text = text[:MESSAGE_TEXT_MAX].rstrip() + " [... cut]"
    return f"[{who}] {text}"
