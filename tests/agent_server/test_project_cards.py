"""A member's ``ask_user`` card, passed on through its project's coordinator.

A member's turn that ends on a card is reported to the coordinator with the
question; the coordinator asks the user with its own card naming the member
(``for_conversation``), and the user's answer there is recorded on the
member's card as the user's.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server.api import add_domain_error_handler
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import ServerStores
from openhands.agent_server.project_board import ConversationOutcomeSubscriber
from openhands.agent_server.project_turns import card_text
from openhands.agent_server.project_wake import notify_coordinator, pass_on_answer
from openhands.agent_server.projects_router import projects_router
from openhands.sdk import LLM, Agent
from openhands.sdk.event import ActionEvent, Event, MessageEvent, ObservationEvent
from openhands.sdk.event.ask_user import AskUserAnswerEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool import Tool
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.ask_user import (
    AskUserAction,
    AskUserForConversationAction,
    AskUserObservation,
    AskUserOption,
    AskUserTool,
)
from tests.agent_server.conftest import install_test_context, update_test_context


pytestmark = pytest.mark.projects_on


def _ask(action: AskUserAction) -> ActionEvent:
    call_id = f"ask-{uuid4().hex[:6]}"
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="")],
        action=action,
        tool_name="ask_user",
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id,
            name="ask_user",
            arguments=action.model_dump_json(exclude={"kind"}),
            origin="completion",
        ),
        llm_response_id="resp-1",
    )


def _shown(ask: ActionEvent) -> ObservationEvent:
    return ObservationEvent(
        source="environment",
        observation=AskUserObservation.from_text("shown"),
        action_id=ask.id,
        tool_name="ask_user",
        tool_call_id=ask.tool_call_id,
    )


_DATABASE = AskUserAction(
    question="Which database should config.yaml use?",
    options=[
        AskUserOption(label="Postgres", consequence="A server to pay for."),
        AskUserOption(label="SQLite", consequence="A file on disk."),
    ],
    recommended=1,
)


# The tool


def test_only_the_coordinators_ask_user_names_a_conversation():
    (plain,) = AskUserTool.create()
    (coordinator,) = AskUserTool.create(for_conversations=True)

    assert plain.action_type is AskUserAction
    assert "for_conversation" not in plain.description
    assert coordinator.action_type is AskUserForConversationAction
    assert "for_conversation" in coordinator.description
    schema = coordinator.to_openai_tool()["function"]["parameters"]  # type: ignore[index]
    assert "for_conversation" in schema["properties"]


# The board: a turn that ends on a card


class _Tags:
    def __init__(self, tags: dict[str, str]):
        self.tags = tags

    async def set_tag(self, conversation_id, key, value, *, only_if_absent=False):
        if value is None:
            self.tags.pop(key, None)
        else:
            self.tags[key] = value
        return self.tags


def _user(text: str, sender: str | None = None) -> MessageEvent:
    return MessageEvent(
        source="user",
        sender=sender,
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


def _finished() -> ConversationStateUpdateEvent:
    return ConversationStateUpdateEvent(key="execution_status", value="finished")


async def _feed(subscriber: ConversationOutcomeSubscriber, *events: Event) -> None:
    for event in events:
        await subscriber(event)
    await subscriber.drain()


def test_card_text_lists_the_options():
    question, text = card_text(_ask(_DATABASE))  # type: ignore[misc]

    assert question == "Which database should config.yaml use?"
    assert text == (
        "Question: Which database should config.yaml use?\n"
        "1. Postgres: A server to pay for.\n"
        "2. SQLite (recommended): A file on disk."
    )


@pytest.mark.asyncio
async def test_a_turn_ending_on_a_card_is_asked_and_reported():
    store = _Tags({"project": "p1"})
    finished: list[tuple] = []
    judged: list = []

    async def on_finished(*args):
        finished.append(args)

    async def judge(state):
        judged.append(state)
        return None

    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(),
        tags=lambda: store.tags,
        set_tag=store.set_tag,
        on_finished=on_finished,
        judge=judge,
    )
    ask = _ask(_DATABASE)
    cat = f"conversation:{uuid4().hex}"

    await _feed(subscriber, _user("Write config.yaml", cat), ask, _finished())

    assert store.tags == {
        "project": "p1",
        "turn": "asking",
        "ask": "Which database should config.yaml use?",
    }
    # Asked on a card: the user owes the answer even in a turn the
    # coordinator started, so the judge has nothing to decide.
    assert judged == []
    ((project, turn, text, sender),) = finished
    assert (project, turn, sender) == ("p1", "asking", cat)
    assert text is not None and text.startswith("Question: Which database")


@pytest.mark.asyncio
async def test_a_failed_card_then_a_finish_is_not_asked():
    store = _Tags({"project": "p1"})
    subscriber = ConversationOutcomeSubscriber(
        conversation_id=uuid4(), tags=lambda: store.tags, set_tag=store.set_tag
    )
    finish = ActionEvent(
        source="agent",
        thought=[TextContent(text="")],
        action=FinishAction(message="Wrote config.yaml with SQLite."),
        tool_name="finish",
        tool_call_id="finish-1",
        tool_call=MessageToolCall(
            id="finish-1",
            name="finish",
            arguments=json.dumps({"message": "Wrote config.yaml with SQLite."}),
            origin="completion",
        ),
        llm_response_id="resp-2",
    )

    await _feed(subscriber, _user("Write it"), _ask(_DATABASE), finish, _finished())

    assert store.tags.get("turn") == "finished"
    assert "ask" not in store.tags


# The coordinator and its members, on a real service


@pytest.fixture
def persistence_dir(tmp_path, monkeypatch) -> Iterator[Path]:
    path = tmp_path / "persist"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(path))
    yield path


@pytest.fixture
def stores(persistence_dir) -> ServerStores:
    return ServerStores.from_config(Config())


@pytest.fixture
async def service(tmp_path, stores) -> AsyncIterator[ConversationService]:
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", stores=stores
    ) as service:
        yield service


@pytest.fixture
async def client(service, stores) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    add_domain_error_handler(app)
    install_test_context(
        app,
        Config(static_files_path=None, session_api_keys=[], secret_key=None),
        conversation_service=service,
    )
    update_test_context(app, stores=stores)
    app.include_router(projects_router, prefix="/api")
    app.include_router(event_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def save_without_running(monkeypatch) -> None:
    original = EventService.send_message

    async def save_only(self, message, run=False, **kwargs):
        kwargs.pop("refuse_pending_confirmation", None)
        await original(self, message, False, **kwargs)
        return "started"

    monkeypatch.setattr(EventService, "send_message", save_only)


async def _start(service: ConversationService, tmp_path: Path) -> UUID:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4o", usage_id="test-llm"),
                tools=[Tool(name="ask_user")],
            ),
            workspace=LocalWorkspace(working_dir=str(workspace)),
        )
    )
    return info.id


async def _project(client, service, tmp_path) -> tuple[str, UUID, UUID]:
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    # As the coordinator's start does.
    await service.projects.set_conversation_tag(worker, "project", project["id"])
    return project["id"], cat, worker


async def _record(service, conversation_id: UUID, *events: Event) -> None:
    event_service = await service.get_event_service(conversation_id)
    assert event_service is not None
    conversation = event_service.get_conversation()
    with conversation.state:
        for event in events:
            conversation._on_event(event)


async def _answers(service, conversation_id: UUID) -> list[AskUserAnswerEvent]:
    return [
        e
        for e in await _events(service, conversation_id)
        if isinstance(e, AskUserAnswerEvent)
    ]


async def _passed_on(service, coordinator: UUID, member: UUID) -> None:
    """Wait for the coordinator's board to pass the user's answer to the member.

    Recorded events reach the board on later turns of the loop, and the board
    then passes the answer on in the background.
    """
    event_service = await service.get_event_service(coordinator)
    assert event_service is not None
    for _ in range(200):
        if await _answers(service, member):
            break
        await asyncio.sleep(0.025)
    for subscriber in list(event_service._pub_sub._subscribers.values()):
        if isinstance(subscriber, ConversationOutcomeSubscriber):
            await subscriber.drain()


async def _events(service, conversation_id: UUID) -> list[Event]:
    event_service = await service.get_event_service(conversation_id)
    assert event_service is not None
    return list((await event_service.get_state()).events)


async def _ask_through(service, cat: UUID, worker: UUID) -> tuple[str, str]:
    """The worker asks on a card; the coordinator passes it on with its own."""
    asked = _ask(_DATABASE)
    await _record(service, worker, asked, _shown(asked))
    passed = _ask(
        AskUserForConversationAction(
            question="Pick the login service's database",
            options=[
                AskUserOption(label="Postgres", consequence="Costs more to host."),
                AskUserOption(label="SQLite, for now", consequence="Free."),
            ],
            for_conversation=str(worker),
        )
    )
    await _record(service, cat, passed, _shown(passed))
    return asked.id, passed.id


@pytest.mark.asyncio
async def test_the_coordinator_gets_the_members_card(client, service, tmp_path):
    project, cat, worker = await _project(client, service, tmp_path)
    asked = _ask(_DATABASE)
    await _record(service, worker, asked, _shown(asked))
    _, text = card_text(asked)  # type: ignore[misc]

    assert await notify_coordinator(
        service, worker, project, "asking", text, f"conversation:{cat.hex}"
    )

    (update,) = [e for e in await _events(service, cat) if isinstance(e, MessageEvent)]
    header, body = (c.text for c in update.llm_message.content)  # type: ignore[union-attr]
    assert header.startswith("[Update from ") and worker.hex in header
    assert "for_conversation" in header and "Do not answer it yourself" in header
    assert body == text


@pytest.mark.asyncio
async def test_a_tap_on_the_coordinators_card_answers_the_member(
    client, service, tmp_path
):
    project, cat, worker = await _project(client, service, tmp_path)
    asked_id, passed_id = await _ask_through(service, cat, worker)
    tap = AskUserAnswerEvent(
        ask_id=passed_id, option_index=0, option_label="Postgres", text="We grow."
    )
    # Recording the tap is enough: the coordinator's board passes it on.
    await _record(service, cat, tap)
    await _passed_on(service, cat, worker)

    events = await _events(service, worker)
    (answer,) = [e for e in events if isinstance(e, AskUserAnswerEvent)]
    assert (answer.ask_id, answer.option_index, answer.option_label) == (
        asked_id,
        0,
        "Postgres",
    )
    assert answer.text == "We grow."
    message = [e for e in events if isinstance(e, MessageEvent)][-1]
    # The coordinator started this turn, so it hears how it ends.
    assert message.sender == f"conversation:{cat.hex}"
    assert message.llm_message.content[0].text == (  # type: ignore[union-attr]
        '[Answer to your question: "Which database should config.yaml use?"]\n'
        "Option 1: Postgres\nWe grow."
    )
    # Answered: a second answer has nothing left to close.
    assert not await pass_on_answer(service, cat, project, tap.id, "Postgres", None)


def _no_whole_branch():
    return patch(
        "openhands.sdk.conversation.state.ConversationState.active_branch",
        side_effect=AssertionError("read the whole branch"),
    )


@pytest.mark.asyncio
async def test_the_pending_question_is_read_back_only_to_the_question(
    client, service, tmp_path
):
    project, cat, worker = await _project(client, service, tmp_path)
    asked_id, passed_id = await _ask_through(service, cat, worker)
    member = await service.get_event_service(worker)
    coordinator = await service.get_event_service(cat)
    assert member is not None and coordinator is not None

    with _no_whole_branch():
        waiting = member.pending_ask()
    tap = AskUserAnswerEvent(ask_id=passed_id, option_index=0, option_label="Postgres")
    await _record(service, cat, tap)
    await _passed_on(service, cat, worker)
    with _no_whole_branch():
        answered = member.pending_ask()
        closed = coordinator.pending_ask(tap.id)
        unknown = coordinator.pending_ask("not-an-event")

    assert waiting is not None and waiting.ask_id == asked_id
    assert answered is None
    # The question the tap answered, read from just before the tap.
    assert closed is not None and closed.ask_id == passed_id
    assert unknown is None


@pytest.mark.asyncio
async def test_passing_the_same_answer_twice_is_harmless(client, service, tmp_path):
    project, cat, worker = await _project(client, service, tmp_path)
    _, passed_id = await _ask_through(service, cat, worker)
    tap = AskUserAnswerEvent(ask_id=passed_id, option_index=0, option_label="Postgres")
    await _record(service, cat, tap)

    # A second pass races the board's own; neither fails, one answer lands.
    await pass_on_answer(service, cat, project, tap.id, "Postgres", None)
    await _passed_on(service, cat, worker)

    answers = [
        e for e in await _events(service, worker) if isinstance(e, AskUserAnswerEvent)
    ]
    assert len(answers) == 1


@pytest.mark.asyncio
async def test_an_option_the_coordinator_reworded_reaches_the_member_as_words(
    client, service, tmp_path
):
    project, cat, worker = await _project(client, service, tmp_path)
    _, passed_id = await _ask_through(service, cat, worker)
    tap = AskUserAnswerEvent(
        ask_id=passed_id,
        option_index=1,
        option_label="SQLite, for now",
    )
    await _record(service, cat, tap)
    await _passed_on(service, cat, worker)

    (answer,) = [
        e for e in await _events(service, worker) if isinstance(e, AskUserAnswerEvent)
    ]
    assert (answer.option_index, answer.text) == (None, "SQLite, for now")


@pytest.mark.asyncio
async def test_the_users_words_in_the_coordinator_answer_the_member(
    client, service, tmp_path
):
    project, cat, worker = await _project(client, service, tmp_path)
    await _ask_through(service, cat, worker)
    reply = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="SQLite.")]),
    )
    await _record(service, cat, reply)
    await _passed_on(service, cat, worker)

    (answer,) = [
        e for e in await _events(service, worker) if isinstance(e, AskUserAnswerEvent)
    ]
    assert (answer.option_index, answer.text) == (None, "SQLite.")


@pytest.mark.asyncio
async def test_the_users_answer_reaches_a_question_asked_without_a_card(
    client, service, tmp_path
):
    """A worker asked in its final message; the coordinator asks the user on
    a card for it, and the user's answer reaches the worker as theirs."""
    project, cat, worker = await _project(client, service, tmp_path)
    await service.projects.set_conversation_tag(worker, "turn", "asking")
    await service.projects.set_conversation_tag(worker, "ask", "Which name?")
    passed = _ask(
        AskUserForConversationAction(
            question="Which name for the cat?",
            options=[
                AskUserOption(label="Mochi", consequence="Short."),
                AskUserOption(label="Juniper", consequence="Longer."),
            ],
            for_conversation=str(worker),
        )
    )
    await _record(service, cat, passed, _shown(passed))
    tap = AskUserAnswerEvent(ask_id=passed.id, option_index=0, option_label="Mochi")
    await _record(service, cat, tap)

    for _ in range(200):
        if [e for e in await _events(service, worker) if isinstance(e, MessageEvent)]:
            break
        await asyncio.sleep(0.025)
    (message,) = [
        e for e in await _events(service, worker) if isinstance(e, MessageEvent)
    ]
    assert message.sender == f"conversation:{cat.hex}"
    assert message.llm_message.content[0].text == (  # type: ignore[union-attr]
        '[Answer to your question: "Which name?"]\nMochi'
    )
    info = await service.get_conversation(worker)
    assert info is not None and "turn" not in info.tags
    # Answered: a second pass has nothing left to close.
    assert not await pass_on_answer(service, cat, project, tap.id, "Mochi", None)


@pytest.mark.asyncio
async def test_a_tap_on_an_old_card_does_not_answer_a_newer_question(
    client, service, tmp_path
):
    """The user answered the worker's first question in the worker, and the
    worker asked again; the coordinator's card for the first question no
    longer answers anything."""
    project, cat, worker = await _project(client, service, tmp_path)
    _, passed_id = await _ask_through(service, cat, worker)
    again = _ask(
        AskUserAction(
            question="Which port?",
            options=[
                AskUserOption(label="Postgres", consequence="5432."),
                AskUserOption(label="Other", consequence="Pick one."),
            ],
        )
    )
    await _record(service, worker, again, _shown(again))
    tap = AskUserAnswerEvent(ask_id=passed_id, option_index=0, option_label="Postgres")

    await _record(service, cat, tap)

    assert not await pass_on_answer(service, cat, project, tap.id, "Postgres", None)
    await _passed_on(service, cat, worker)
    assert await _answers(service, worker) == []


@pytest.mark.asyncio
async def test_an_old_card_does_not_answer_a_newer_open_question(
    client, service, tmp_path
):
    project, cat, worker = await _project(client, service, tmp_path)
    passed = _ask(
        AskUserForConversationAction(
            question="Which name for the cat?",
            options=[
                AskUserOption(label="Mochi", consequence="Short."),
                AskUserOption(label="Juniper", consequence="Longer."),
            ],
            for_conversation=str(worker),
        )
    )
    await _record(service, cat, passed, _shown(passed))
    # The user went on in the worker, which ended a new turn on a question.
    await _record(service, worker, _user("Call it Mochi. Which color?"))
    await service.projects.set_conversation_tag(worker, "turn", "asking")
    await service.projects.set_conversation_tag(worker, "ask", "Which color?")

    tap = AskUserAnswerEvent(ask_id=passed.id, option_index=1, option_label="Juniper")
    await _record(service, cat, tap)

    assert not await pass_on_answer(service, cat, project, tap.id, "Juniper", None)
    await _passed_on(service, cat, worker)
    messages = [
        e for e in await _events(service, worker) if isinstance(e, MessageEvent)
    ]
    assert len(messages) == 1  # the user's own; no answer was passed on


@pytest.mark.asyncio
async def test_nothing_is_passed_on_without_a_member_waiting(client, service, tmp_path):
    project, cat, worker = await _project(client, service, tmp_path)
    # The coordinator's own question names no member.
    own = _ask(_DATABASE)
    await _record(service, cat, own, _shown(own))
    tap = AskUserAnswerEvent(ask_id=own.id, option_index=0, option_label="Postgres")
    await _record(service, cat, tap)
    assert not await pass_on_answer(service, cat, project, tap.id, "Postgres", None)

    # The user answered in the member first.
    _, passed_id = await _ask_through(service, cat, worker)
    await _record(service, worker, _user("Postgres"))
    tap = AskUserAnswerEvent(ask_id=passed_id, option_index=0, option_label="Postgres")
    await _record(service, cat, tap)
    assert not await pass_on_answer(service, cat, project, tap.id, "Postgres", None)
    assert not [
        e for e in await _events(service, worker) if isinstance(e, AskUserAnswerEvent)
    ]


@pytest.mark.asyncio
async def test_only_the_coordinator_passes_answers_to_its_own_members(
    client, service, tmp_path
):
    project, cat, worker = await _project(client, service, tmp_path)
    outsider = await _start(service, tmp_path)
    asked = _ask(_DATABASE)
    await _record(service, outsider, asked, _shown(asked))
    passed = _ask(
        AskUserForConversationAction(
            **_DATABASE.model_dump(exclude={"kind"}), for_conversation=str(outsider)
        )
    )
    await _record(service, cat, passed, _shown(passed))
    tap = AskUserAnswerEvent(ask_id=passed.id, option_index=1, option_label="SQLite")
    await _record(service, cat, tap)

    # Not a member of the project.
    assert not await pass_on_answer(service, cat, project, tap.id, "SQLite", None)
    # Not the project's coordinator.
    assert not await pass_on_answer(service, worker, project, tap.id, "SQLite", None)
