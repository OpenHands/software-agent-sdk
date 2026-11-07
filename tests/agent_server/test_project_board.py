"""Tests for the thread-outcome tags a project board groups by."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.project_board import (
    ThreadOutcomeSubscriber,
    classify_turn,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.types import THREAD_STATE_TAG_KEYS
from openhands.sdk.critic.result import CriticResult
from openhands.sdk.event import (
    ActionEvent,
    ConversationStateUpdateEvent,
    MessageEvent,
)
from openhands.sdk.event.conversation_state import FULL_STATE_KEY
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.workspace import LocalWorkspace


# classify_turn


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Done. The tests pass.", "answered"),
        ("Should I also update the docs?", "asked"),
        ("I fixed it.\n\nWant me to open a PR? Let me know.", "asked"),
        ("Which do you prefer: **A** or **B?**", "asked"),
        ("Did that work? Then I'm done.\n\nAll tests pass.", "answered"),
        ("See https://example.com/search?q=1 for details.", "answered"),
        ("Here is the fix:\n\n```py\nx = a if b else c?\n```", "answered"),
        ("", "answered"),
        (None, "answered"),
    ],
)
def test_classify_turn(text, expected):
    assert classify_turn(text) == expected


# ThreadOutcomeSubscriber, with a recording set_tag


class _Tags:
    def __init__(self, tags: dict[str, str]):
        self.tags = tags
        self.calls: list[tuple[str, str | None, bool]] = []

    async def set_tag(
        self,
        conversation_id: UUID,
        key: str,
        value: str | None,
        *,
        only_if_absent: bool = False,
    ) -> dict[str, str]:
        self.calls.append((key, value, only_if_absent))
        if only_if_absent and key in self.tags:
            return self.tags
        if value is None:
            self.tags.pop(key, None)
        else:
            self.tags[key] = value
        return self.tags


def _subscriber(store: _Tags) -> ThreadOutcomeSubscriber:
    return ThreadOutcomeSubscriber(
        conversation_id=uuid4(), tags=lambda: store.tags, set_tag=store.set_tag
    )


def _message(source: str, text: str, score: float | None = None) -> MessageEvent:
    role = "user" if source == "user" else "assistant"
    return MessageEvent(
        source=source,  # type: ignore[arg-type]
        llm_message=Message(role=role, content=[TextContent(text=text)]),
        critic_result=None
        if score is None
        else CriticResult(score=score, message=None),
    )


def _finish(text: str, score: float | None = None) -> ActionEvent:
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="")],
        action=FinishAction(message=text),
        tool_name="finish",
        tool_call_id="finish-1",
        tool_call=MessageToolCall(
            id="finish-1",
            name="finish",
            arguments=json.dumps({"message": text}),
            origin="completion",
        ),
        llm_response_id="resp-1",
        critic_result=None
        if score is None
        else CriticResult(score=score, message=None),
    )


def _status(value: str, *, full: bool = False) -> ConversationStateUpdateEvent:
    if full:
        return ConversationStateUpdateEvent(
            key=FULL_STATE_KEY, value={"execution_status": value}
        )
    return ConversationStateUpdateEvent(key="execution_status", value=value)


async def _feed(subscriber: ThreadOutcomeSubscriber, *events) -> None:
    for event in events:
        await subscriber(event)
    await subscriber.drain()


@pytest.mark.asyncio
async def test_finished_run_records_a_question():
    store = _Tags({"project": "p1"})
    subscriber = _subscriber(store)

    await _feed(
        subscriber,
        _message("user", "Fix the bug"),
        _status("running"),
        _message("agent", "Fixed. Should I also add a test?"),
        _status("finished"),
    )

    assert store.tags == {"project": "p1", "turn": "asked"}


@pytest.mark.asyncio
async def test_confident_critic_resolves_without_overriding_the_person():
    store = _Tags({"project": "p1"})
    subscriber = _subscriber(store)

    await _feed(subscriber, _finish("All done.", score=0.95), _status("finished"))
    assert store.tags == {"project": "p1", "turn": "answered", "resolved": "auto"}

    store.tags = {"project": "p1", "resolved": "user"}
    await _feed(subscriber, _finish("All done.", score=0.95), _status("finished"))
    assert store.tags["resolved"] == "user"
    assert ("resolved", "auto", True) in store.calls


@pytest.mark.asyncio
async def test_unsure_critic_does_not_resolve():
    store = _Tags({"project": "p1"})

    await _feed(
        _subscriber(store), _finish("Done, I think.", score=0.89), _status("finished")
    )

    assert store.tags == {"project": "p1", "turn": "answered"}


@pytest.mark.asyncio
async def test_new_user_message_reopens_the_thread():
    store = _Tags({"project": "p1", "turn": "asked", "resolved": "user"})

    await _feed(_subscriber(store), _message("user", "Yes, please add it"))

    assert store.tags == {"project": "p1"}


@pytest.mark.asyncio
async def test_initial_state_push_of_a_finished_thread_changes_nothing():
    store = _Tags({"project": "p1"})

    await _feed(_subscriber(store), _status("finished", full=True))

    assert store.calls == []


@pytest.mark.asyncio
async def test_status_updates_after_the_first_finish_change_nothing():
    store = _Tags({"project": "p1"})
    subscriber = _subscriber(store)
    await _feed(subscriber, _message("agent", "Done."), _status("finished"))
    calls = list(store.calls)

    await _feed(subscriber, _status("finished"), _status("finished", full=True))

    assert store.calls == calls


@pytest.mark.asyncio
async def test_conversations_outside_projects_are_left_alone():
    store = _Tags({"other": "x"})

    await _feed(
        _subscriber(store),
        _message("user", "Hi"),
        _message("agent", "Hello! What do you need?"),
        _status("finished"),
    )

    assert store.calls == []
    assert store.tags == {"other": "x"}


@pytest.mark.asyncio
async def test_a_failing_tag_write_does_not_break_publishing():
    async def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    subscriber = ThreadOutcomeSubscriber(
        conversation_id=uuid4(), tags=lambda: {"project": "p1"}, set_tag=broken
    )

    await _feed(subscriber, _message("agent", "Done."), _status("finished"))


# ConversationService integration


@pytest.fixture
async def service(tmp_path) -> AsyncIterator[ConversationService]:
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        yield service


async def _start(
    service: ConversationService, tmp_path: Path, tags: dict[str, str]
) -> UUID:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            tags=tags,
        )
    )
    return info.id


@pytest.mark.asyncio
async def test_set_tag_only_if_absent(service, tmp_path):
    cid = await _start(service, tmp_path, {"project": "p1", "resolved": "user"})

    kept = await service.set_conversation_tag(
        cid, "resolved", "auto", only_if_absent=True
    )
    added = await service.set_conversation_tag(
        cid, "turn", "asked", only_if_absent=True
    )

    assert kept is not None and kept["resolved"] == "user"
    assert added is not None and added["turn"] == "asked"


@pytest.mark.asyncio
async def test_finished_run_tags_a_live_conversation(service, tmp_path):
    cid = await _start(service, tmp_path, {"project": "p1"})
    event_service = await service.get_event_service(cid)
    assert event_service is not None
    subscriber = ThreadOutcomeSubscriber(
        conversation_id=cid,
        tags=lambda: event_service.stored.tags,
        set_tag=service.set_conversation_tag,
    )

    await _feed(subscriber, _message("agent", "Shall I merge it?"), _status("finished"))

    info = await service.get_conversation(cid)
    assert info is not None
    assert info.tags == {"project": "p1", "turn": "asked"}


def test_thread_state_tags_are_the_board_tags():
    assert THREAD_STATE_TAG_KEYS == {"resolved", "turn"}


@pytest.mark.asyncio
async def test_service_registers_the_subscriber_for_every_conversation(
    service, tmp_path
):
    cid = await _start(service, tmp_path, {"project": "p1", "turn": "asked"})
    event_service = await service.get_event_service(cid)
    assert event_service is not None

    # A user message published through the conversation's own pub/sub reaches
    # the registered subscriber, which reopens the thread.
    await event_service._pub_sub(_message("user", "One more thing"))

    async def turn_cleared() -> bool:
        info = await service.get_conversation(cid)
        return info is not None and "turn" not in info.tags

    for _ in range(100):
        if await turn_cleared():
            break
        await asyncio.sleep(0.01)
    assert await turn_cleared()
