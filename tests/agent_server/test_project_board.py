"""Tests for the conversation-outcome tags a project board groups by."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.project_board import (
    ConversationOutcomeSubscriber,
    classify_turn,
    extract_ask,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.types import BOARD_TAG_KEYS
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


# ConversationOutcomeSubscriber, with a recording set_tag


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


def _subscriber(store: _Tags) -> ConversationOutcomeSubscriber:
    return ConversationOutcomeSubscriber(
        conversation_id=uuid4(), tags=lambda: store.tags, set_tag=store.set_tag
    )


def _message(
    source: str, text: str, score: float | None = None, sender: str | None = None
) -> MessageEvent:
    role = "user" if source == "user" else "assistant"
    return MessageEvent(
        source=source,  # type: ignore[arg-type]
        sender=sender,
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


async def _feed(subscriber: ConversationOutcomeSubscriber, *events) -> None:
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

    assert store.tags == {
        "project": "p1",
        "turn": "asked",
        "ask": "Should I also add a test?",
    }


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
async def test_new_user_message_reopens_the_conversation():
    store = _Tags(
        {"project": "p1", "turn": "asked", "ask": "Add a test?", "resolved": "user"}
    )

    await _feed(_subscriber(store), _message("user", "Yes, please add it"))

    assert store.tags == {"project": "p1"}


@pytest.mark.asyncio
async def test_initial_state_push_of_a_finished_conversation_changes_nothing():
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

    subscriber = ConversationOutcomeSubscriber(
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
    subscriber = ConversationOutcomeSubscriber(
        conversation_id=cid,
        tags=lambda: event_service.stored.tags,
        set_tag=service.set_conversation_tag,
    )

    await _feed(subscriber, _message("agent", "Shall I merge it?"), _status("finished"))

    info = await service.get_conversation(cid)
    assert info is not None
    assert info.tags == {"project": "p1", "turn": "asked", "ask": "Shall I merge it?"}


def test_board_tag_keys():
    assert BOARD_TAG_KEYS == {"resolved", "turn", "ask"}


@pytest.mark.asyncio
async def test_service_registers_the_subscriber_for_every_conversation(
    service, tmp_path
):
    cid = await _start(service, tmp_path, {"project": "p1", "turn": "asked"})
    event_service = await service.get_event_service(cid)
    assert event_service is not None

    # A user message published through the conversation's own pub/sub reaches
    # the registered subscriber, which reopens the conversation.
    await event_service._pub_sub(_message("user", "One more thing"))

    async def turn_cleared() -> bool:
        info = await service.get_conversation(cid)
        return info is not None and "turn" not in info.tags

    for _ in range(100):
        if await turn_cleared():
            break
        await asyncio.sleep(0.01)
    assert await turn_cleared()


def _stop_hook_feedback() -> MessageEvent:
    return MessageEvent(
        source="environment",
        llm_message=Message(
            role="user",
            content=[TextContent(text="[Stop hook feedback] Call finish.")],
        ),
    )


@pytest.mark.asyncio
async def test_a_question_bounced_by_a_stop_hook_stays_asked():
    store = _Tags({"project": "p1"})

    # The hook may refuse the stop before or after a FINISHED is published.
    await _feed(
        _subscriber(store),
        _message("agent", "Which database should I use?"),
        _stop_hook_feedback(),
        _finish("Waiting for your choice."),
        _status("finished"),
    )

    assert store.tags["turn"] == "asked"
    assert store.tags["ask"] == "Which database should I use?"


@pytest.mark.asyncio
async def test_a_question_then_a_published_bounce_stays_asked():
    store = _Tags({"project": "p1"})
    subscriber = _subscriber(store)

    await _feed(
        subscriber,
        _message("agent", "Which database should I use?"),
        _status("finished"),
        _stop_hook_feedback(),
        _status("running"),
        _finish("Waiting for your choice."),
        _status("finished"),
    )
    assert store.tags["turn"] == "asked"

    await _feed(
        subscriber,
        _message("user", "Postgres"),
        _finish("Done, using Postgres."),
        _status("finished"),
    )
    assert store.tags["turn"] == "answered"


@pytest.mark.asyncio
async def test_a_rhetorical_question_mid_run_does_not_stick():
    store = _Tags({"project": "p1"})

    await _feed(
        _subscriber(store),
        _message("agent", "Why does this fail? Let me check the logs."),
        _message("agent", "Is it the cache?"),
        _finish("Fixed: the cache key was wrong."),
        _status("finished"),
    )

    assert store.tags["turn"] == "answered"


@pytest.mark.asyncio
async def test_a_user_message_reopens_a_resolved_conversation_outside_projects():
    store = _Tags({"resolved": "user", "other": "x"})

    await _feed(_subscriber(store), _message("user", "One more thing"))

    assert store.tags == {"other": "x"}


@pytest.mark.asyncio
async def test_a_user_message_before_the_outcome_is_saved_still_reopens():
    """The outcome of a finished run is written in the background; a message
    that arrives before that write lands must still win."""
    store = _Tags({"project": "p1"})
    gate = asyncio.Event()

    async def slow_set_tag(*args, **kwargs):
        await gate.wait()
        return await store.set_tag(*args, **kwargs)

    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(), tags=lambda: store.tags, set_tag=slow_set_tag
    )

    await subscriber(_finish("All done.", score=0.95))
    await subscriber(_status("finished"))
    # The outcome write is still pending, so no board tag is visible yet.
    await subscriber(_message("user", "Wait, one more thing"))
    gate.set()
    await subscriber.drain()

    assert store.tags == {"project": "p1"}


@pytest.mark.asyncio
async def test_a_finished_member_reports_its_final_message():
    store = _Tags({"project": "p1"})
    reports: list[tuple[str, str, str | None, str | None]] = []

    async def on_finished(project, turn, text, sender):
        reports.append((project, turn, text, sender))

    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(),
        tags=lambda: store.tags,
        set_tag=store.set_tag,
        on_finished=on_finished,
    )

    await _feed(
        subscriber,
        _message("user", "Pick a database", sender="conversation:cat"),
        _finish("Which database?"),
        _status("finished"),
        _message("user", "Postgres"),
        _finish("Done."),
        _status("finished"),
    )

    assert reports == [
        ("p1", "asked", "Which database?", "conversation:cat"),
        ("p1", "answered", "Done.", None),
    ]


@pytest.mark.asyncio
async def test_a_slow_report_does_not_hold_up_the_board():
    store = _Tags({"project": "p1"})
    gate = asyncio.Event()

    async def on_finished(*_args):
        await gate.wait()

    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(),
        tags=lambda: store.tags,
        set_tag=store.set_tag,
        on_finished=on_finished,
    )
    await subscriber(_finish("Done."))
    await subscriber(_status("finished"))
    await subscriber(_message("user", "Thanks, one more thing"))
    assert subscriber._tail is not None
    await subscriber._tail

    assert store.tags == {"project": "p1"}
    gate.set()
    await subscriber.drain()


@pytest.mark.asyncio
async def test_conversations_outside_projects_report_nothing():
    reports: list = []

    async def on_finished(*args):
        reports.append(args)

    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(),
        tags=lambda: {},
        set_tag=_Tags({}).set_tag,
        on_finished=on_finished,
    )
    await _feed(subscriber, _finish("Done."), _status("finished"))

    assert reports == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Done. Which colour do you prefer?", "Which colour do you prefer?"),
        (
            "Fixed.\n\nShould I also:\n- **update the docs?**",
            "Should I also: update the docs?",
        ),
        ("All done.", None),
        ("See https://example.com/?q=1 for details.", None),
        ("Ok?\n\n```py\nprint('x?')\n```", "Ok?"),
        (None, None),
    ],
)
def test_extract_ask(text, expected):
    assert extract_ask(text) == expected


def test_a_long_ask_is_cut():
    ask = extract_ask("Should I " + "really " * 60 + "do it?")
    assert ask is not None and len(ask) == 200 and ask.endswith("…")


@pytest.mark.asyncio
async def test_an_answered_turn_drops_a_stale_ask():
    store = _Tags({"project": "p1", "ask": "Old question?"})

    await _feed(_subscriber(store), _finish("All done."), _status("finished"))

    assert store.tags == {"project": "p1", "turn": "answered"}


@pytest.mark.asyncio
async def test_the_persons_messages_are_passed_on_in_order():
    store = _Tags({"project": "p1"})
    calls: list[tuple] = []

    async def on_person_message(project, text):
        calls.append(("person", project, text))

    async def on_finished(project, turn, text, sender):
        calls.append(("finished", project, text, sender))

    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(),
        tags=lambda: store.tags,
        set_tag=store.set_tag,
        on_finished=on_finished,
        on_person_message=on_person_message,
    )

    await _feed(
        subscriber,
        _message("user", "Use Postgres"),
        _finish("Done."),
        _status("finished"),
        _message("user", "From the Cat", sender="conversation:cat"),
    )

    assert calls == [
        ("person", "p1", "Use Postgres"),
        ("finished", "p1", "Done.", None),
    ]


@pytest.mark.asyncio
async def test_an_awareness_copy_neither_reopens_nor_starts_a_turn():
    store = _Tags({"project": "p1", "turn": "asked", "ask": "Ship it?"})
    finished: list = []

    async def on_finished(*args):
        finished.append(args)

    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(),
        tags=lambda: store.tags,
        set_tag=store.set_tag,
        on_finished=on_finished,
    )
    note = _message("user", "[Message from the person in x]", sender="conversation:abc")

    await _feed(subscriber, note)

    assert store.tags == {"project": "p1", "turn": "asked", "ask": "Ship it?"}
    assert finished == []
