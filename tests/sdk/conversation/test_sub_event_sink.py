"""Tests for ConversationState sub_event_sink PrivateAttr slot."""

from uuid import uuid4

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace


def _make_state(tmp_path):
    llm = LLM(model="gpt-4o", usage_id="test")
    agent = Agent(llm=llm, tools=[])
    workspace = LocalWorkspace(working_dir=str(tmp_path))
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=workspace,
        persistence_dir=str(tmp_path / "conversations"),
    )


def test_sub_event_sink_round_trip_and_clear(tmp_path):
    state = _make_state(tmp_path)

    def my_sink(event):
        pass

    assert state.get_sub_event_sink() is None
    state.set_sub_event_sink(my_sink)
    assert state.get_sub_event_sink() is my_sink
    state.set_sub_event_sink(None)
    assert state.get_sub_event_sink() is None


def test_sub_event_sink_not_serialized(tmp_path):
    state = _make_state(tmp_path)
    state.set_sub_event_sink(lambda event: None)
    dumped = state.model_dump(mode="json")
    assert "_sub_event_sink" not in dumped
    assert "sub_event_sink" not in dumped
