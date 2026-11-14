"""Tests for the ``find_conversations`` and ``read_conversation`` tools."""

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.tools.conversation_mentions import (
    FindConversationsAction,
    FindConversationsTool,
    ReadConversationAction,
    ReadConversationTool,
)
from openhands.tools.conversation_mentions.definition import (
    CONVERSATION_MENTIONS_GUIDANCE,
)
from openhands.tools.conversation_mentions.impl import (
    PAGE_SIZE,
    FindConversationsExecutor,
    ReadConversationExecutor,
)


SERVER = "http://server"


def _info(title: str | None, status: str = "finished") -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "title": title,
        "execution_status": status,
        "updated_at": "2026-11-14T09:30:12.5",
    }


def _find(
    pages: list[list[dict[str, Any]]], own_id: UUID | None = None
) -> tuple[FindConversationsExecutor, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/api/conversations/search"
        index = int(request.url.params.get("page_id") or 0)
        next_page = str(index + 1) if index + 1 < len(pages) else None
        return httpx.Response(
            200, json={"items": pages[index], "next_page_id": next_page}
        )

    executor = FindConversationsExecutor(
        own_id, server_url=SERVER, transport=httpx.MockTransport(handler)
    )
    return executor, seen


def test_find_lists_matching_titles_as_mentions():
    export = _info("Fix Safari export button", "idle")
    other = _info("Write release notes")
    executor, seen = _find([[export, other]])

    result = executor(FindConversationsAction(query="EXPORT safari"))

    assert not result.is_error
    hex_id = UUID(export["id"]).hex
    assert (
        f"- [Fix Safari export button](conversation:{hex_id}): idle, "
        "last active 2026-11-14 09:30 UTC"
    ) in result.text
    assert "release notes" not in result.text
    params = seen[0].url.params
    assert params["sort_order"] == "UPDATED_AT_DESC"
    assert params["limit"] == str(PAGE_SIZE)


def test_find_skips_the_own_conversation_and_reads_more_pages():
    own = _info("Fix the export")
    first = _info("Export, part one")
    second = _info("Export, part two")
    executor, seen = _find([[own, first], [second]], own_id=UUID(own["id"]))

    result = executor(FindConversationsAction(query="export"))

    assert UUID(own["id"]).hex not in result.text
    assert "part one" in result.text and "part two" in result.text
    assert len(seen) == 2


def test_find_stops_at_the_limit():
    executor, seen = _find([[_info(f"Task {i}") for i in range(5)], [_info("x")]])

    result = executor(FindConversationsAction(limit=2))

    assert result.text.startswith("Latest conversations")
    assert result.text.count("conversation:") == 2
    assert len(seen) == 1


def test_find_says_when_nothing_matches():
    executor, _ = _find([[_info("Something else")]])

    result = executor(FindConversationsAction(query="export"))

    assert not result.is_error
    assert result.text == "No conversation title matches 'export'."


def test_find_reports_a_server_error():
    executor = FindConversationsExecutor(
        server_url=SERVER,
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="boom")),
    )

    result = executor(FindConversationsAction())

    assert result.is_error
    assert "500" in result.text


def _message(source: str, text: str, sender: str | None = None) -> dict[str, Any]:
    return MessageEvent(
        source=source,  # type: ignore[arg-type]
        llm_message=Message(
            role="user" if source == "user" else "assistant",
            content=[TextContent(text=text)],
        ),
        sender=sender,
    ).model_dump(mode="json", exclude_none=True)


def _finish(text: str) -> dict[str, Any]:
    return ActionEvent(
        source="agent",
        thought=[],
        action=FinishAction(message=text),
        tool_name="finish",
        tool_call_id="call",
        tool_call=MessageToolCall(
            id="call", name="finish", arguments="{}", origin="completion"
        ),
        llm_response_id="response",
    ).model_dump(mode="json", exclude_none=True)


def _read(
    title: str | None, events: list[dict[str, Any]], status_code: int = 200
) -> tuple[ReadConversationExecutor, UUID, list[httpx.Request]]:
    cid = uuid4()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status_code != 200:
            return httpx.Response(status_code, json={"detail": "nope"})
        if request.url.path == f"/api/conversations/{cid.hex}":
            return httpx.Response(
                200, json={"id": str(cid), "title": title, "execution_status": "idle"}
            )
        assert request.url.path == f"/api/conversations/{cid.hex}/events/search"
        kind = request.url.params["kind"]
        items = [e for e in events if e["kind"] == kind]
        return httpx.Response(200, json={"items": items, "next_page_id": None})

    executor = ReadConversationExecutor(
        server_url=SERVER, transport=httpx.MockTransport(handler)
    )
    return executor, cid, seen


def test_read_gives_messages_and_finish_oldest_first():
    events = [
        _message("user", "The export button does nothing on Safari."),
        _message("agent", "Looking at it."),
        _finish("Fixed: the click handler now runs on Safari."),
        _message("user", "Thanks!", sender="conversation:" + "a" * 32),
    ]
    executor, cid, _ = _read("Fix Safari export", list(reversed(events)))

    result = executor(
        ReadConversationAction(conversation=f"[Old title](conversation:{cid.hex})")
    )

    assert not result.is_error
    assert result.text.startswith(
        f"Latest 4 messages of [Fix Safari export](conversation:{cid.hex}) (idle)"
    )
    body = result.text.split("\n\n", 1)[1].split("\n\n")
    assert body == [
        "[the user] The export button does nothing on Safari.",
        "[agent] Looking at it.",
        "[agent (finish)] Fixed: the click handler now runs on Safari.",
        f"[user, from conversation:{'a' * 32}] Thanks!",
    ]


def test_read_keeps_the_latest_messages_within_the_limit():
    events = [_message("user", f"message {i}") for i in range(5)]
    executor, cid, _ = _read("T", events)

    result = executor(ReadConversationAction(conversation=str(cid), limit=2))

    assert "message 3" in result.text and "message 4" in result.text
    assert "message 2" not in result.text


def test_read_never_writes():
    executor, cid, seen = _read("T", [_message("user", "hi")])

    executor(ReadConversationAction(conversation=cid.hex))

    assert seen and all(r.method == "GET" for r in seen)


@pytest.mark.parametrize("text", ["", "the export one", "conversation:123"])
def test_read_needs_a_mention_or_an_id(text):
    executor, _, seen = _read("T", [])

    result = executor(ReadConversationAction(conversation=text))

    assert result.is_error
    assert "not a conversation mention or id" in result.text
    assert seen == []


def test_read_an_unknown_conversation():
    executor, cid, _ = _read("T", [], status_code=404)

    result = executor(ReadConversationAction(conversation=str(cid)))

    assert result.is_error
    assert result.text == f"There is no conversation {cid.hex}."


def test_tools_are_read_only_and_carry_the_mention_guidance():
    for tool_class in (FindConversationsTool, ReadConversationTool):
        (tool,) = tool_class.create(None)
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint
        assert tool.prompt_guidance == CONVERSATION_MENTIONS_GUIDANCE
        assert not tool_class.user_selectable
    assert FindConversationsTool.name == "find_conversations"
    assert ReadConversationTool.name == "read_conversation"
