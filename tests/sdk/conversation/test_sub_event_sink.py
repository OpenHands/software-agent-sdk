"""Tests for ConversationState sub_event_sink slot."""
from uuid import uuid4

import pytest
from openhands.sdk import Agent, LLM
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


def test_sub_event_sink_round_trip(tmp_path):
    """set_sub_event_sink/get_sub_event_sink round-trip works."""
    state = _make_state(tmp_path)

    def my_sink(event):
        pass

    assert state.get_sub_event_sink() is None
    state.set_sub_event_sink(my_sink)
    assert state.get_sub_event_sink() is my_sink


def test_sub_event_sink_not_serialized(tmp_path):
    """The sink must NOT appear in model_dump() — it's a PrivateAttr."""
    state = _make_state(tmp_path)

    def my_sink(event):
        pass

    state.set_sub_event_sink(my_sink)
    dumped = state.model_dump()
    assert "_sub_event_sink" not in dumped
    assert "sub_event_sink" not in dumped
