import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from openhands.sdk import Agent, LocalConversation
from openhands.sdk.event import Condensation, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins import (
    ContextNotesAction,
    ContextNotesObservation,
    ConversationHistoryAction,
    NewContextAction,
)
from openhands.sdk.tool.builtins.context_notes import get_context_notes


def _notes_message(*actions: ContextNotesAction) -> Message:
    return Message(
        role="assistant",
        tool_calls=[
            MessageToolCall(
                id=f"call_{i}",
                name="context_notes",
                arguments=action.model_dump_json(exclude_none=True),
                origin="completion",
            )
            for i, action in enumerate(actions)
        ],
    )


@pytest.fixture
def make_conversation(
    tmp_path: Path,
) -> Iterator[Callable[[list[Message]], LocalConversation]]:
    conversations: list[LocalConversation] = []

    def create(messages: list[Message]) -> LocalConversation:
        conversation = LocalConversation(
            agent=Agent(
                llm=TestLLM.from_messages(list(messages)),
                tools=[],
                include_default_tools=[
                    "FinishTool",
                    "ContextNotesTool",
                    "ConversationHistoryTool",
                    "NewContextTool",
                ],
            ),
            workspace=tmp_path,
            persistence_dir=tmp_path / "state",
            visualizer=None,
            secrets={"TEST_TOKEN": "private-token-123456"},
        )
        conversations.append(conversation)
        return conversation

    yield create
    for conversation in conversations:
        conversation.close()


def _done() -> Message:
    return Message(role="assistant", content=[TextContent(text="Done.")])


def test_notes_batch_replays_masked_deltas_and_restores(make_conversation, tmp_path):
    first = "first private-token-123456 " + "a" * 12000
    delta = "\n" + "b" * 12000
    conversation = make_conversation(
        [
            _notes_message(
                ContextNotesAction(command="write", content=first),
                ContextNotesAction(command="append", content=delta),
            ),
            _done(),
        ]
    )
    conversation.send_message("Save progress.")
    conversation.run()
    mutations = [
        event
        for event in conversation.state.active_branch()
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, ContextNotesObservation)
    ]
    assert len(mutations) == 2
    assert isinstance(mutations[1].observation, ContextNotesObservation)
    assert mutations[1].observation.saved_content == delta
    latest, notes = get_context_notes(conversation.state.active_branch())
    assert latest is not None
    assert latest == mutations[1]
    assert notes.endswith(delta)
    assert len(notes) > 16000
    assert "private-token-123456" not in notes
    assert "a" * 100 not in mutations[0].observation.text

    page = conversation.execute_tool(
        "context_notes", ContextNotesAction(command="read", limit=5)
    )
    payload = json.loads(page.text)
    assert payload["text"] == "first"
    assert payload["version_id"] == latest.id
    assert payload["next_offset"] == 5
    old_page = conversation.execute_tool(
        "context_notes",
        ContextNotesAction(
            command="read", version_id=mutations[0].id, offset=12000, limit=8000
        ),
    )
    assert "b" not in json.loads(old_page.text)["text"]
    conversation_id = conversation.id
    agent = conversation.agent
    conversation.close()

    restored = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "state",
        conversation_id=conversation_id,
        visualizer=None,
    )
    try:
        restored_latest, restored_notes = get_context_notes(
            restored.state.active_branch()
        )
        assert restored_latest is not None
        assert restored_latest.id == latest.id
        assert restored_notes == notes
    finally:
        restored.close()


def test_notes_errors_and_reads_do_not_commit(make_conversation):
    conversation = make_conversation(
        [
            _notes_message(ContextNotesAction(command="write", content="keep")),
            _notes_message(
                ContextNotesAction(command="append", content="x" * 16001),
                ContextNotesAction(command="write"),
                ContextNotesAction(command="read"),
            ),
            _done(),
        ]
    )
    conversation.send_message("Save progress.")
    conversation.run()
    latest, notes = get_context_notes(conversation.state.active_branch())
    assert latest is not None
    assert notes == "keep"
    errors = [
        event.observation
        for event in conversation.state.active_branch()
        if isinstance(event, ObservationEvent) and event.observation.is_error
    ]
    assert len(errors) == 2
    assert "16001" in errors[0].text
    assert "16000" in errors[0].text
    assert "append" in errors[0].text
    pending = conversation.execute_tool(
        "context_notes", ContextNotesAction(command="write", content="uncommitted")
    )
    assert not pending.is_error
    assert get_context_notes(conversation.state.active_branch())[1] == "keep"


def test_notes_versions_are_branch_local(make_conversation):
    conversation = make_conversation(
        [
            _notes_message(ContextNotesAction(command="write", content="root")),
            _notes_message(ContextNotesAction(command="append", content="child")),
            _done(),
        ]
    )
    conversation.send_message("Save progress.")
    conversation.run()
    mutations = [
        event
        for event in conversation.state.active_branch()
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, ContextNotesObservation)
    ]
    fork = conversation.fork(from_event_id=mutations[0].id)
    try:
        fork.send_message("Continue in the fork.")
        assert get_context_notes(fork.state.active_branch())[1] == "root"
        assert get_context_notes(conversation.state.active_branch())[1] == "rootchild"
        unavailable = fork.execute_tool(
            "context_notes",
            ContextNotesAction(command="read", version_id=mutations[1].id),
        )
        assert unavailable.is_error
    finally:
        fork.close()
    conversation.navigate_to(mutations[0].id)
    assert get_context_notes(conversation.state.active_branch())[1] == "root"
    page = conversation.execute_tool(
        "context_notes",
        ContextNotesAction(command="read", version_id=mutations[1].id),
    )
    assert page.is_error
    assert "active branch" in page.text


def test_history_search_and_read_recover_condensed_text(make_conversation):
    conversation = make_conversation([])
    conversation.send_message("Original A.B " + "z" * 10000)
    original = conversation.state.active_branch()[-1]
    conversation.send_message("Another a.b match")
    recent = conversation.state.active_branch()[-1]
    conversation.send_message("Axb is not a literal match")
    conversation.state.append_event(
        Condensation(forgotten_event_ids={original.id}, llm_response_id="test")
    )
    result = conversation.execute_tool(
        "conversation_history",
        ConversationHistoryAction(command="search", query="a.b", limit=1),
    )
    payload = json.loads(result.text)
    assert [match["event_id"] for match in payload["matches"]] == [recent.id]
    assert payload["next_before_event_id"] == recent.id
    older = conversation.execute_tool(
        "conversation_history",
        ConversationHistoryAction(
            command="search", query="A.B", before_event_id=recent.id
        ),
    )
    match = json.loads(older.text)["matches"][0]
    assert match["event_id"] == original.id
    assert not match["in_active_view"]
    assert len(match["snippet"]) <= 400
    page = conversation.execute_tool(
        "conversation_history",
        ConversationHistoryAction(
            command="read", event_id=original.id, offset=9000, limit=8000
        ),
    )
    payload = json.loads(page.text)
    assert payload["text"] == "z" * 1013
    assert payload["next_offset"] is None
    assert not payload["in_active_view"]
    conversation.navigate_to(original.id)
    unavailable = conversation.execute_tool(
        "conversation_history",
        ConversationHistoryAction(command="read", event_id=recent.id),
    )
    assert unavailable.is_error


def test_history_omits_notes_and_provider_reasoning(make_conversation):
    conversation = make_conversation(
        [
            _notes_message(ContextNotesAction(command="write", content="notes-only")),
            _done(),
        ]
    )
    conversation.send_message("Save progress.")
    conversation.run()
    conversation.state.append_event(
        MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text="Visible answer")],
                reasoning_content="private-reasoning-only",
            ),
        )
    )
    for query in ["notes-only", "private-reasoning-only"]:
        result = conversation.execute_tool(
            "conversation_history",
            ConversationHistoryAction(command="search", query=query),
        )
        assert json.loads(result.text)["matches"] == []


@pytest.mark.parametrize(
    "action",
    [
        ConversationHistoryAction(command="search", query=" "),
        ConversationHistoryAction(command="search", query="a", limit=11),
        ConversationHistoryAction(
            command="search", query="a", before_event_id="missing"
        ),
        ConversationHistoryAction(command="read", event_id="missing"),
        ConversationHistoryAction(command="read", query="a"),
    ],
)
def test_history_rejects_invalid_requests(make_conversation, action):
    conversation = make_conversation([])
    result = conversation.execute_tool("conversation_history", action)
    assert result.is_error


def test_new_context_requires_capable_condenser(make_conversation):
    conversation = make_conversation([])
    result = conversation.execute_tool("new_context", NewContextAction())
    assert result.is_error
    assert "condenser" in result.text
