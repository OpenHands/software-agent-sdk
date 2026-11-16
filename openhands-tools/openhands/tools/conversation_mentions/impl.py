"""Executors for ``find_conversations`` and ``read_conversation``.

Both read the agent-server that runs the tool over its own REST API, as
``start_child_conversation`` does (same URL and session key), so they work the
same in a local server and in a runtime's server. They only use read routes.
"""

import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx

from openhands.sdk.conversation.types import (
    CONVERSATION_MENTION_SCHEME,
    conversation_mention,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.child_conversation.impl import (
    internal_server_url,
    response_detail,
    session_api_key_headers,
)
from openhands.tools.conversation_mentions.definition import (
    ConversationMentionsObservation,
    FindConversationsAction,
    ReadConversationAction,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


logger = get_logger(__name__)

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


def _get(client: httpx.Client, path: str, **params: Any) -> Any:
    response = client.get(
        path, params={k: v for k, v in params.items() if v is not None}
    )
    response.raise_for_status()
    return response.json()


def _failure(exc: Exception, timeout: float) -> str:
    """Why a request to the server failed, for the tool's error text."""
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            f"the server answered {exc.response.status_code}: "
            f"{response_detail(exc.response)}"
        )
    if isinstance(exc, httpx.TimeoutException):
        return f"the server did not answer within {timeout:.0f} seconds"
    return f"{type(exc).__name__}: {exc}"


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
        self.own_conversation_id = own_conversation_id
        self.server_url = server_url
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.server_url or internal_server_url(),
            headers=session_api_key_headers(),
            timeout=self.timeout,
            transport=self._transport,
        )

    def __call__(
        self,
        action: FindConversationsAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> ConversationMentionsObservation:
        words = action.query.lower().split()
        found: list[dict[str, Any]] = []
        page_id: str | None = None
        try:
            with self._client() as client:
                for _ in range(SEARCH_PAGES):
                    page = _get(
                        client,
                        "/api/conversations/search",
                        limit=PAGE_SIZE,
                        sort_order="UPDATED_AT_DESC",
                        page_id=page_id,
                    )
                    for item in page["items"]:
                        if self.own_conversation_id is not None and _same_id(
                            item.get("id"), self.own_conversation_id
                        ):
                            continue
                        title = (item.get("title") or "").lower()
                        if all(word in title for word in words):
                            found.append(item)
                    page_id = page.get("next_page_id")
                    if len(found) >= action.limit or not page_id:
                        break
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("find_conversations failed: %s", exc)
            return _error(
                f"Could not list conversations: {_failure(exc, self.timeout)}"
            )

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
        self.server_url = server_url
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.server_url or internal_server_url(),
            headers=session_api_key_headers(),
            timeout=self.timeout,
            transport=self._transport,
        )

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
            with self._client() as client:
                info = _get(client, f"/api/conversations/{hex_id}")
                messages = _messages(client, hex_id, action.limit)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return _error(f"There is no conversation {hex_id}.")
            return _error(
                f"Could not read conversation {hex_id}: {_failure(exc, self.timeout)}"
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("read_conversation failed for %s: %s", hex_id, exc)
            return _error(
                f"Could not read conversation {hex_id}: {_failure(exc, self.timeout)}"
            )

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


def _messages(client: httpx.Client, hex_id: str, limit: int) -> list[str]:
    """The latest messages and ``finish`` texts, as transcript lines."""
    path = f"/api/conversations/{hex_id}/events/search"
    events = [
        *_get(
            client, path, kind="MessageEvent", sort_order="TIMESTAMP_DESC", limit=limit
        )["items"],
        *_get(
            client,
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
