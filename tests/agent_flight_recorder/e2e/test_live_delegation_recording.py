import json
from pathlib import Path

from flight_recorder.gui.main_window import MainWindow
from flight_recorder.models.database import TraceIndex
from flight_recorder.recorder import Recorder
from flight_recorder.services.bundles import BundleService
from flight_recorder.services.repository import TraceRepository
from PySide6.QtCore import Qt

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.tools.task import TaskToolSet


def test_live_delegation_is_recorded_and_navigable(tmp_path: Path, qtbot) -> None:
    _reset_registry_for_tests()
    child_llm = TestLLM.from_messages([_text_message("Implemented the helper.")])

    def create_child(llm):
        return Agent(llm=child_llm, tools=[])

    register_agent(
        name="implementer",
        factory_func=create_child,
        description="Implements a focused change.",
    )
    parent_llm = TestLLM.from_messages(
        [
            _task_message(),
            _text_message("The delegated implementation is complete."),
        ]
    )
    bundle = tmp_path / "live-delegation.afr"
    index = TraceIndex(tmp_path / "trace.db")
    recorder = Recorder(bundle, index)
    parent_events = []
    conversation = Conversation(
        agent=Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)]),
        workspace=tmp_path / "workspace",
        callbacks=[recorder, parent_events.append],
        visualizer=None,
    )
    try:
        conversation.send_message("Delegate this implementation.")
        conversation.run()
        recorder.record_metrics(conversation.conversation_stats)
    finally:
        conversation.close()
        recorder.close()
        _reset_registry_for_tests()

    archive = BundleService().export_bundle(bundle, tmp_path / "live-delegation.zip")
    replay_index = TraceIndex(tmp_path / "replay.db")
    BundleService(replay_index).import_bundle(archive)
    records = replay_index.iter_records(recorder.trace_id)
    child_start = next(
        record
        for record in records
        if record.kind == "agent.started" and record.payload.get("task_id")
    )
    child_id = child_start.span_id
    assert child_id is not None
    assert child_id != "task_00000001"
    assert any(record.kind == "delegation.started" for record in records)
    assert any(record.kind == "delegation.finished" for record in records)
    assert any(
        record.agent_span_id == child_id
        and record.payload.get("event_type") == "MessageEvent"
        for record in records
    )
    assert any(
        record.kind == "agent.finished" and record.span_id == child_id
        for record in records
    )
    child_event_ids = {
        record.openhands_event_id
        for record in records
        if record.agent_span_id == child_id and record.openhands_event_id is not None
    }
    assert child_event_ids.isdisjoint(event.id for event in parent_events)

    repository = TraceRepository(replay_index)
    relationships = repository.get_agent_relationships(recorder.trace_id)
    assert [(item.child_span_id, item.status.value) for item in relationships] == [
        (child_id, "verified")
    ]
    usage, _ = repository.get_agent_usage(recorder.trace_id, child_id)
    assert usage.prompt_tokens == 0

    window = MainWindow(repository=repository, trace_id=recorder.trace_id)
    qtbot.addWidget(window)
    window.navigate_to_agent(recorder.agent_span_id)
    assert "Parent: Root" in window.inspector.summary.text()
    window.navigate_to_agent(child_id)
    assert window.selection.current_selection().span_id == child_id
    assert window.tree.currentIndex().data(Qt.ItemDataRole.UserRole + 1) == child_id


def _task_message() -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id="delegate-call",
                name="task",
                arguments=json.dumps(
                    {
                        "description": "Implement helper",
                        "prompt": "Implement the requested helper.",
                        "subagent_type": "implementer",
                    }
                ),
                origin="completion",
            )
        ],
    )


def _text_message(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])
