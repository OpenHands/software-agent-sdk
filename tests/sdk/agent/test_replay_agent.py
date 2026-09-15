"""Tests for SnapshotReplayAgent."""

import json
import os
from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import MagicMock

from pydantic import SecretStr

from openhands.sdk.agent.replay_agent import SnapshotReplayAgent
from openhands.sdk.conversation import ConversationState
from openhands.sdk.event import ActionEvent, Event, MessageEvent, ObservationEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.tool import Action, Observation, ToolDefinition
from openhands.sdk.tool.tool import ToolExecutor


class DummyAction(Action):
    value: str


class DummyObservation(Observation):
    result: str


class DummyTool(ToolDefinition[DummyAction, DummyObservation]):
    name: ClassVar[str] = "dummy"
    description: str = "Dummy tool for testing"
    action_type: type[Action] = DummyAction
    observation_type: type[Observation] | None = DummyObservation  # noqa: E501

    @classmethod
    def create(cls, *args, **kwargs) -> Sequence["DummyTool"]:
        class DummyExecutor(ToolExecutor[DummyAction, DummyObservation]):
            def __call__(
                self, action: DummyAction, conversation=None
            ) -> DummyObservation:
                return DummyObservation(result=f"Done {action.value}")

        return [cls(executor=DummyExecutor())]


def _make_events() -> tuple[ActionEvent, ObservationEvent]:
    """Build a matching ActionEvent + ObservationEvent pair for use in tests."""
    tc = MessageToolCall(
        id="tc1",
        name="dummy",
        arguments='{"value": "hello"}',
        origin="completion",
    )
    action_event = ActionEvent(
        source="agent",
        tool_name="dummy",
        tool_call_id="tc1",
        tool_call=tc,
        action=DummyAction(value="hello"),
        thought=[],
        llm_response_id="response1",
    )
    obs_event = ObservationEvent(
        source="environment",
        action_id=action_event.id,
        tool_name="dummy",
        tool_call_id="tc1",
        observation=DummyObservation(result="Done hello"),
    )
    return action_event, obs_event


def _make_conv_mock(events: list) -> MagicMock:
    """Build a minimal conversation mock with a state that holds the event list."""
    state = MagicMock()
    state.events = events
    state.pop_blocked_action.return_value = None
    conv = MagicMock()
    conv.state = state
    return conv


def test_replay_agent_initialization():
    """Replay mode is disabled when the snapshot is empty, enabled otherwise."""
    llm = LLM(model="test", api_key=SecretStr("test"))

    agent_empty = SnapshotReplayAgent(llm=llm, replay_mode=True, replay_snapshot=[])
    assert agent_empty._actual_replay_mode is False

    events: list[Event] = [  # type: ignore[assignment]
        MessageEvent(
            source="agent",
            llm_message=Message(role="assistant", content=[TextContent(text="Hello")]),
        )
    ]
    agent_loaded = SnapshotReplayAgent(
        llm=llm, replay_mode=True, replay_snapshot=events
    )
    assert agent_loaded._actual_replay_mode is True


def test_replay_agent_step_no_drift(tmp_path):
    """When the live observation matches the snapshot, drift log shows 'No drift'."""
    drift_log = str(tmp_path / "drift.log")

    action_event, obs_event = _make_events()
    events = [action_event, obs_event]

    llm = LLM(model="test", api_key=SecretStr("test"))

    # Patch the class-level helper used inside step()
    ConversationState.get_unmatched_actions = staticmethod(lambda x: [])  # type: ignore[method-assign]

    conv = _make_conv_mock(events)
    agent = SnapshotReplayAgent(
        llm=llm,
        tools=[],
        replay_mode=True,
        replay_snapshot=events,
        drift_log_path=drift_log,
    )
    agent._initialize(conv.state)
    tools = DummyTool.create()
    agent._tools = {tool.name: tool for tool in tools}

    emitted: list = []
    agent.step(conv, on_event=lambda e: emitted.append(e))

    assert len(emitted) == 1
    assert isinstance(emitted[0], ObservationEvent)

    assert os.path.exists(drift_log)
    with open(drift_log) as f:
        log_data = json.loads(f.read().strip())
    assert log_data["Drift from expected observation"] == "No drift"


def test_replay_agent_step_with_drift(tmp_path):
    """When the live observation differs from the snapshot, drift is logged."""

    class DriftingDummyTool(DummyTool):
        name: ClassVar[str] = "dummy"

        @classmethod
        def create(cls, *args, **kwargs) -> Sequence[DummyTool]:  # type: ignore[override]
            class DriftingExecutor(ToolExecutor[DummyAction, DummyObservation]):
                def __call__(
                    self, action: DummyAction, conversation=None
                ) -> DummyObservation:
                    return DummyObservation(result="Unexpected result")

            return [cls(executor=DriftingExecutor())]

    drift_log = str(tmp_path / "drift.log")
    action_event, obs_event = _make_events()
    events = [action_event, obs_event]

    llm = LLM(model="test", api_key=SecretStr("test"))
    ConversationState.get_unmatched_actions = staticmethod(lambda x: [])  # type: ignore[method-assign]

    conv = _make_conv_mock(events)
    agent = SnapshotReplayAgent(
        llm=llm,
        tools=[],
        replay_mode=True,
        replay_snapshot=events,
        drift_log_path=drift_log,
    )
    agent._initialize(conv.state)
    tools = DriftingDummyTool.create()
    agent._tools = {tool.name: tool for tool in tools}

    emitted: list = []
    agent.step(conv, on_event=lambda e: emitted.append(e))

    assert os.path.exists(drift_log)
    with open(drift_log) as f:
        log_data = json.loads(f.read().strip())
    drift_data = log_data["Drift from expected observation"]
    assert isinstance(drift_data, dict)
    assert drift_data["Drift Present"] is True
