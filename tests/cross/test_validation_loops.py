"""Validation-loop regressions through the real agent and conversation loop."""

import asyncio
import json
import uuid
from collections.abc import Sequence
from typing import ClassVar

import pytest
from pydantic import field_validator

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.stuck_detector import StuckDetector
from openhands.sdk.event import ActionEvent, AgentErrorEvent, Event, MessageEvent
from openhands.sdk.event.llm_convertible.observation import ToolValidationError
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.tools.task_tracker import TaskTrackerTool


class LevelAction(Action):
    level: int

    @field_validator("level")
    @classmethod
    def check_level(cls, value: int) -> int:
        if not 1 <= value <= 5:
            raise ValueError("level must be between 1 and 5")
        return value


class TitledLevelAction(LevelAction):
    summary: str


class LevelObservation(Observation):
    @property
    def to_llm_content(self) -> Sequence[TextContent]:
        return [TextContent(text="ok")]


class LevelExecutor(ToolExecutor[LevelAction, LevelObservation]):
    def __call__(self, action, conversation=None):
        return LevelObservation()


class LevelTool(ToolDefinition[LevelAction, LevelObservation]):
    name: ClassVar[str] = "validation_loop_level"
    action: ClassVar[type[LevelAction]] = LevelAction

    @classmethod
    def create(cls, conv_state=None, **params):
        return [
            cls(
                description="Sets a level",
                action_type=cls.action,
                observation_type=LevelObservation,
                executor=LevelExecutor(),
            )
        ]


class TitledLevelTool(LevelTool):
    name: ClassVar[str] = "validation_loop_titled_level"
    action: ClassVar[type[LevelAction]] = TitledLevelAction


register_tool(LevelTool.name, LevelTool)
register_tool(TitledLevelTool.name, TitledLevelTool)


def plan_message(index, tasks, thought="Updating the plan."):
    return Message(
        role="assistant",
        content=[TextContent(text=thought)],
        tool_calls=[
            MessageToolCall(
                id=f"call_{index}",
                name=TaskTrackerTool.name,
                arguments=json.dumps({"command": "plan", "task_list": tasks}),
                origin="completion",
            )
        ],
    )


def call_message(calls, thought="Updating the plan."):
    return Message(
        role="assistant",
        content=[TextContent(text=thought)],
        tool_calls=[
            MessageToolCall(
                id=call_id, name=name, arguments=json.dumps(args), origin="completion"
            )
            for call_id, name, args in calls
        ],
    )


def finished():
    return Message(role="assistant", content=[TextContent(text="Done.")])


@pytest.fixture
def make_conversation(tmp_path):
    conversations = []

    def create(messages, tools=(TaskTrackerTool.name,), **kwargs):
        llm = TestLLM.from_messages(messages)
        agent = Agent(llm=llm, tools=[Tool(name=name) for name in tools])
        conv = Conversation(
            agent=agent,
            workspace=str(tmp_path),
            visualizer=None,
            max_iteration_per_run=12,
            **kwargs,
        )
        assert isinstance(conv, LocalConversation)
        conv.send_message("Maintain the task plan.")
        conversations.append(conv)
        return conv

    yield create
    for conv in conversations:
        conv.close()


def run(conv, asynchronous):
    if asynchronous:
        asyncio.run(conv.arun())
    else:
        conv.run()


def errors(conv):
    return [e for e in conv.state.events if isinstance(e, AgentErrorEvent)]


def nudges(conv):
    return [
        e
        for e in conv.state.events
        if isinstance(e, MessageEvent) and e.source == "environment"
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "mode", ["identical", "thought", "irrelevant", "index", "alternate"]
)
def test_repeated_validation_nudges_then_stops(make_conversation, mode, asynchronous):
    messages = []
    for i in range(12):
        tasks = [{"title": "Example", "status": "blocked"}]
        if mode == "irrelevant":
            tasks[0]["title"] = f"New description {i}"
        if mode == "index":
            tasks = [{"title": "Valid", "status": "todo"}] * i + tasks
        if mode == "alternate":
            tasks[0]["status"] = "blocked" if i % 2 else "cancelled"
        thought = f"Trying {i}" if mode == "thought" else "Updating the plan."
        messages.append(plan_message(i, tasks, thought))
    conv = make_conversation(messages)
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.STUCK
    assert len(errors(conv)) == 4
    assert len(nudges(conv)) == 1
    events = list(conv.state.events)
    nudge_index = events.index(nudges(conv)[0])
    assert sum(isinstance(e, AgentErrorEvent) for e in events[:nudge_index]) == 3


@pytest.mark.parametrize("asynchronous", [False, True])
def test_incremental_repairs_finish_without_nudge(make_conversation, asynchronous):
    messages = [
        plan_message(
            i,
            [
                {"title": f"Task {j}", "status": "todo"}
                if j < i
                else {"status": "todo"}
                for j in range(6)
            ],
        )
        for i in range(7)
    ]
    conv = make_conversation([*messages, finished()])
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert [len(e.validation_errors or []) for e in errors(conv)] == [6, 5, 4, 3, 2, 1]
    assert not nudges(conv)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_successful_repair_resets_streak(make_conversation, asynchronous):
    tasks = [{"title": "Example", "status": "blocked"}]
    valid = [{"title": "Example", "status": "todo"}]
    conv = make_conversation(
        [plan_message(i, valid if i == 2 else tasks) for i in range(5)] + [finished()]
    )
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert len(errors(conv)) == 4
    assert not nudges(conv)


def test_new_user_message_resets_validation_streak(make_conversation):
    tasks = [{"title": "Example", "status": "blocked"}]
    conv = make_conversation([plan_message(i, tasks) for i in range(6)] + [finished()])
    conv.run()
    assert conv.state.execution_status == ConversationExecutionStatus.STUCK
    assert len(errors(conv)) == 4
    conv.send_message("Try again, then stop.")
    conv.run()
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert len(errors(conv)) == 6
    assert len(nudges(conv)) == 1


def test_validation_details_round_trip_and_old_events_load(make_conversation):
    conv = make_conversation(
        [
            plan_message(0, [{"title": "sensitive argument", "status": "blocked"}]),
            finished(),
        ]
    )
    conv.run()
    error = errors(conv)[0]
    data = error.model_dump(mode="json")
    assert data["validation_errors"] == [
        {"type": "literal_error", "loc": ["task_list", 0, "status"]}
    ]
    assert "sensitive argument" not in json.dumps(data["validation_errors"])
    assert AgentErrorEvent.model_validate_json(error.model_dump_json()) == error
    data.pop("validation_errors")
    assert AgentErrorEvent.model_validate(data).validation_errors is None


def test_persisted_validation_streak_replays(make_conversation, tmp_path):
    tasks = [{"title": "Example", "status": "blocked"}]
    conv = make_conversation(
        [plan_message(i, tasks, f"Trying {i}") for i in range(4)],
        persistence_dir=str(tmp_path / "history"),
    )
    conv.run()
    conversation_id = conv.id
    agent = conv.agent
    conv.close()
    resumed = Conversation(
        agent=agent,
        conversation_id=conversation_id,
        persistence_dir=str(tmp_path / "history"),
        workspace=str(tmp_path),
        visualizer=None,
    )
    try:
        assert isinstance(resumed, LocalConversation)
        assert len(errors(resumed)) == 4
        assert all(e.validation_errors for e in errors(resumed))
        assert StuckDetector(resumed.state).is_stuck()
    finally:
        resumed.close()


def test_matching_errors_survive_reordered_parallel_results(make_conversation):
    tasks = [{"title": "Example", "status": "blocked"}]
    conv = make_conversation([plan_message(i, tasks) for i in range(4)])
    conv.run()
    actions = [e for e in conv.state.events if isinstance(e, ActionEvent)]
    error_events: list[Event] = list(errors(conv))
    detector = StuckDetector(conv.state)
    # The pending newest action must not borrow an older call's error.
    pending = actions[-1].model_copy(update={"tool_call_id": "pending"})
    assert (
        detector._action_error_streak([pending, *reversed(actions)], error_events) == 0
    )
    assert detector._action_error_streak(list(reversed(actions)), error_events) == 4
    assert (
        detector._action_error_streak(
            list(reversed(actions)), list(reversed(error_events))
        )
        == 4
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_changed_failing_field_resets_streak(make_conversation, asynchronous):
    conv = make_conversation(
        [
            plan_message(
                i,
                [{"title": "Example", "status": "blocked"}]
                if i < 2
                else [{"status": "todo"}],
            )
            for i in range(4)
        ]
        + [finished()]
    )
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert len(errors(conv)) == 4
    assert not nudges(conv)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_parallel_validation_errors_keep_their_call_association(
    make_conversation, asynchronous
):
    messages = []
    for i in range(4):
        first = plan_message(
            2 * i, [{"title": "Example", "status": "blocked"}] * (5 - i), f"Try {i}"
        )
        second = plan_message(2 * i + 1, [{"status": "todo"}] * (6 - i))
        assert first.tool_calls and second.tool_calls
        messages.append(
            first.model_copy(
                update={"tool_calls": [*first.tool_calls, *second.tool_calls]}
            )
        )
    conv = make_conversation([*messages, finished()])
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert not nudges(conv)
    assert len(errors(conv)) == 8
    by_call = {e.tool_call_id: e for e in errors(conv)}
    for i in range(4):
        status_errors = by_call[f"call_{2 * i}"].validation_errors
        title_errors = by_call[f"call_{2 * i + 1}"].validation_errors
        assert status_errors and title_errors
        assert len(status_errors) == 5 - i
        assert len(title_errors) == 6 - i
        assert status_errors[0].type == "literal_error"
        assert title_errors[0].type == "missing"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("repair", [False, True])
def test_legacy_and_custom_validation_use_arguments(make_conversation, legacy, repair):
    messages = [
        plan_message(
            i,
            ([{"status": "todo"}] * (6 - i))
            if repair
            else [{"title": "Example", "status": "blocked"}],
            f"Thought {i}",
        )
        for i in range(4)
    ]
    conv = make_conversation([*messages, finished()])
    conv.run()
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=conv.agent,
        workspace=conv.state.workspace,
    )
    seen_errors = 0
    for event in conv.state.events:
        if isinstance(event, AgentErrorEvent):
            event = event.model_copy(
                update={
                    "validation_errors": None
                    if legacy
                    else [ToolValidationError(type="value_error", loc=())]
                }
            )
            seen_errors += 1
        state.events.append(event)
        if seen_errors == 4:
            break
    assert StuckDetector(state).is_stuck() is (not repair)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("thought", ["Setting the level.", ""])
def test_reworded_summary_does_not_hide_custom_validator_loop(
    make_conversation, asynchronous, thought
):
    # value_error falls back to argument comparison; the SDK's summary and
    # security_risk fields are not part of the validated call.
    messages = [
        call_message(
            [
                (
                    f"call_{i}",
                    LevelTool.name,
                    {"level": 9, "summary": f"Attempt {i}", "security_risk": "LOW"},
                )
            ],
            thought,
        )
        for i in range(12)
    ]
    conv = make_conversation(messages, tools=(LevelTool.name,))
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.STUCK
    assert len(errors(conv)) == 4
    assert len(nudges(conv)) == 1


def test_tool_owned_summary_changes_are_repairs(make_conversation):
    messages = [
        call_message(
            [(f"call_{i}", TitledLevelTool.name, {"level": 9, "summary": f"T{i}"})]
        )
        for i in range(5)
    ]
    conv = make_conversation([*messages, finished()], tools=(TitledLevelTool.name,))
    conv.run()
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert len(errors(conv)) == 5
    assert not nudges(conv)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("width", [2, 4, 6])
def test_parallel_failures_in_one_response_do_not_stop_the_run(
    make_conversation, asynchronous, width
):
    tasks = [{"title": "Example", "status": "blocked"}]
    calls = [
        (f"bad_{k}", TaskTrackerTool.name, {"command": "plan", "task_list": tasks})
        for k in range(width)
    ]
    fixed = [
        (
            f"ok_{k}",
            TaskTrackerTool.name,
            {"command": "plan", "task_list": [{"title": f"T{k}", "status": "todo"}]},
        )
        for k in range(width)
    ]
    conv = make_conversation([call_message(calls), call_message(fixed), finished()])
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert len(errors(conv)) == width
    assert not nudges(conv)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("width", [2, 3, 8])
def test_parallel_failure_loop_counts_responses(make_conversation, asynchronous, width):
    messages = [
        call_message(
            [
                (
                    f"call_{i}_{k}",
                    TaskTrackerTool.name,
                    {
                        "command": "plan",
                        "task_list": [{"title": f"T{i}{k}", "status": "blocked"}],
                    },
                )
                for k in range(width)
            ]
        )
        for i in range(12)
    ]
    conv = make_conversation(messages)
    run(conv, asynchronous)
    assert conv.state.execution_status == ConversationExecutionStatus.STUCK
    assert len(errors(conv)) == 4 * width
    assert len(nudges(conv)) == 1
    events = list(conv.state.events)
    nudge_index = events.index(nudges(conv)[0])
    assert sum(isinstance(e, AgentErrorEvent) for e in events[:nudge_index]) == (
        3 * width
    )


def test_alternating_validation_failures_still_stop(make_conversation):
    messages = [
        plan_message(
            i,
            [{"status": "todo"}]
            if i % 2
            else [{"title": "Example", "status": "blocked"}],
            f"Thought {i}",
        )
        for i in range(12)
    ]
    conv = make_conversation(messages)
    conv.run()
    assert conv.state.execution_status == ConversationExecutionStatus.STUCK
    assert len(errors(conv)) == 6
