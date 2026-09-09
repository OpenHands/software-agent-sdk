from datetime import datetime, timedelta

from flight_recorder.gui.inspector.widget import InspectorWidget
from flight_recorder.gui.main_window import MainWindow
from flight_recorder.gui.models.run_table import RunTableModel
from flight_recorder.gui.models.trace_tree import TraceTreeModel
from flight_recorder.gui.timeline.items import (
    HEADER_HEIGHT,
    LANE_HEIGHT,
    MIN_RECORD_WIDTH,
    TimelineRecordItem,
    TimelineSequenceArrowItem,
    TimelineSpanItem,
)
from flight_recorder.gui.timeline.scene import TimelineScene
from flight_recorder.gui.timeline.view import TimelineViewWidget
from flight_recorder.models.envelopes import Record, Span
from flight_recorder.models.view_models import RunSummary, TimelineView
from flight_recorder.services.repository import TraceRepository
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QGraphicsView,
    QSplitter,
)


def test_run_table_and_trace_tree_expose_trace_data() -> None:
    run = RunSummary(trace_id="trace", status="completed", record_count=2)
    record = Record(
        record_id="record",
        producer_id="producer",
        trace_id="trace",
        sequence=1,
        kind="tool.request",
    )

    run_model = RunTableModel([run])
    tree_model = TraceTreeModel(TimelineView(trace_id="trace", records=(record,)))

    assert run_model.rowCount() == 1
    assert run_model.data(run_model.index(0, 0)) == "trace"
    assert tree_model.rowCount() == 1
    assert tree_model.item(0).text() == "tool.request"


def test_conversation_and_llm_interaction_have_two_subrows(qtbot) -> None:
    user_message = Record(
        record_id="user",
        producer_id="producer",
        trace_id="trace",
        sequence=1,
        kind="conversation.event",
        payload={
            "event_type": "MessageEvent",
            "source": "user",
            "llm_message": {"role": "user"},
        },
    )
    assistant_message = Record(
        record_id="assistant",
        producer_id="producer",
        trace_id="trace",
        sequence=2,
        kind="conversation.event",
        payload={
            "event_type": "MessageEvent",
            "source": "agent",
            "llm_message": {"role": "assistant"},
        },
    )
    request = Record(
        record_id="request",
        producer_id="producer",
        trace_id="trace",
        sequence=3,
        kind="llm.request",
        payload={"request": {"input": []}},
    )
    response = Record(
        record_id="response",
        producer_id="producer",
        trace_id="trace",
        sequence=4,
        kind="llm.response",
        payload={"response": {"id": "response-1"}},
    )

    tree_model = TraceTreeModel(
        TimelineView(
            trace_id="trace",
            records=(user_message, assistant_message, request, response),
        )
    )
    scene = TimelineScene()
    scene.set_timeline(
        [],
        [user_message, assistant_message, request, response],
        current_sequence=4,
    )

    assert [tree_model.item(row).text() for row in range(4)] == [
        "User prompt",
        "Assistant message",
        "LLM request",
        "LLM response",
    ]
    assert scene.record_label("request") == "LLM request"
    assert scene.record_label("response") == "LLM response"
    assert scene.available_row_types == (
        "user_conversation",
        "llm_interaction",
    )
    assert scene.enabled_row_types == scene.available_row_types
    assert scene.lane_names == (
        "User Conversation · Session",
        "LLM Interaction · Session",
    )
    record_items = {
        str(getattr(item, "record_id")): item
        for item in scene.items()
        if isinstance(item, QGraphicsRectItem)
        and isinstance(getattr(item, "record_id", None), str)
    }
    request_item = record_items["request"]
    assert request_item.rect().width() == MIN_RECORD_WIDTH
    assert request_item.rect().x() > record_items["assistant"].rect().right()
    assert record_items["response"].rect().x() > request_item.rect().right()
    assert record_items["user"].rect().y() < record_items["assistant"].rect().y()
    assert request_item.rect().y() < record_items["response"].rect().y()
    assert scene.record_at(sequence=1, lane=0) == "user"
    assert scene.record_at(sequence=2, lane=0) == "assistant"
    assert scene.record_at(sequence=3, lane=1) == "request"
    assert scene.record_at(sequence=4, lane=1) == "response"
    subrow_labels = {
        item.toPlainText()
        for item in scene.items()
        if isinstance(item, QGraphicsTextItem)
    }
    assert {"User Input", "Agent Response", "Request", "Response"}.issubset(
        subrow_labels
    )
    assert "Display-aligned start" in request_item.toolTip()
    assistant_to_request = next(
        item
        for item in scene.items()
        if isinstance(item, TimelineSequenceArrowItem)
        and item.previous_record_id == "assistant"
        and item.next_record_id == "request"
    )
    assert assistant_to_request.start.x() == record_items["assistant"].rect().right()
    assert assistant_to_request.end.x() == request_item.rect().left()
    assert assistant_to_request.start.y() != assistant_to_request.end.y()


def test_conversation_messages_share_user_conversation_row(qtbot) -> None:
    system_prompt = Record(
        record_id="system",
        producer_id="producer",
        trace_id="trace",
        sequence=1,
        kind="conversation.event",
        payload={"event_type": "SystemPromptEvent", "source": "agent"},
    )
    user_message = Record(
        record_id="user",
        producer_id="producer",
        trace_id="trace",
        sequence=2,
        kind="conversation.event",
        payload={
            "event_type": "MessageEvent",
            "source": "user",
            "llm_message": {"role": "user"},
        },
    )
    assistant_message = Record(
        record_id="assistant",
        producer_id="producer",
        trace_id="trace",
        sequence=3,
        kind="conversation.event",
        payload={
            "event_type": "MessageEvent",
            "source": "agent",
            "llm_message": {"role": "assistant"},
        },
    )
    scene = TimelineScene()

    scene.set_timeline(
        [],
        [system_prompt, user_message, assistant_message],
        current_sequence=3,
    )

    assert scene.available_row_types == ("user_conversation",)
    assert scene.record_at(sequence=1, lane=0) == "system"
    assert scene.record_at(sequence=2, lane=0) == "user"
    assert scene.record_at(sequence=3, lane=0) == "assistant"
    assert scene.record_label("system") == "System prompt"
    assert scene.record_label("user") == "User prompt"
    assert scene.record_label("assistant") == "Assistant message"
    record_items = {
        str(getattr(item, "record_id")): item
        for item in scene.items()
        if isinstance(item, QGraphicsRectItem)
        and isinstance(getattr(item, "record_id", None), str)
    }
    assert record_items["system"].rect().y() == record_items["user"].rect().y()
    assert record_items["user"].rect().y() < record_items["assistant"].rect().y()


def test_timeline_hit_testing_and_zoom(qtbot) -> None:
    span = Span(
        span_id="tool-span",
        trace_id="trace",
        span_type="tool",
        name="Run tests",
        agent_span_id="agent",
        start_sequence=2,
        end_sequence=5,
        started_at=datetime(2026, 9, 1, 12),
        ended_at=datetime(2026, 9, 1, 12, 0, 3),
        status="completed",
    )
    scene = TimelineScene()
    scene.set_spans([span], current_sequence=5)
    view = TimelineViewWidget(scene)
    qtbot.addWidget(view)

    assert scene.span_at(sequence=3, lane=0) == "tool-span"
    initial_zoom = view.zoom_factor
    view.zoom_in()
    assert view.zoom_factor > initial_zoom
    view.zoom_out()
    assert view.zoom_factor == initial_zoom
    qtbot.keyClick(view, Qt.Key.Key_Plus)
    assert view.zoom_factor > initial_zoom
    assert isinstance(view.minimap, QGraphicsView)


def test_minimum_record_width_stays_fixed_until_duration_requires_growth(
    qtbot,
) -> None:
    started_at = datetime(2026, 9, 1, 12)
    records = [
        Record(
            record_id="request",
            producer_id="producer",
            trace_id="trace",
            sequence=1,
            source_timestamp=started_at,
            kind="llm.request",
        ),
        Record(
            record_id="action",
            producer_id="producer",
            trace_id="trace",
            sequence=2,
            source_timestamp=started_at,
            kind="conversation.event",
            payload={
                "event_type": "ActionEvent",
                "tool_call_id": "call-1",
            },
        ),
        Record(
            record_id="result",
            producer_id="producer",
            trace_id="trace",
            sequence=3,
            source_timestamp=started_at + timedelta(milliseconds=100),
            kind="conversation.event",
            payload={
                "event_type": "ObservationEvent",
                "tool_call_id": "call-1",
            },
        ),
    ]
    scene = TimelineScene()
    scene.set_timeline([], records, current_sequence=3)
    view = TimelineViewWidget(scene)
    qtbot.addWidget(view)

    def record_widths() -> dict[str, float]:
        return {
            str(getattr(item, "record_id")): item.rect().width()
            for item in scene.items()
            if isinstance(item, QGraphicsRectItem)
            and isinstance(getattr(item, "record_id", None), str)
        }

    assert record_widths()["request"] == MIN_RECORD_WIDTH
    assert record_widths()["action"] == MIN_RECORD_WIDTH

    view.zoom_in()

    assert record_widths()["request"] == MIN_RECORD_WIDTH
    assert record_widths()["action"] > MIN_RECORD_WIDTH

    for _ in range(8):
        view.zoom_in()

    assert record_widths()["request"] == MIN_RECORD_WIDTH


def test_timeline_shows_lane_axis_and_activity_markers(qtbot) -> None:
    span = Span(
        span_id="agent-span",
        trace_id="trace",
        span_type="agent",
        name="Implementation agent",
        agent_span_id="agent-span",
        start_sequence=1,
        end_sequence=4,
        started_at=datetime(2026, 9, 1, 12),
        ended_at=datetime(2026, 9, 1, 12, 0, 4),
        status="completed",
    )
    records = (
        Record(
            record_id="action",
            producer_id="producer",
            trace_id="trace",
            agent_span_id="agent-span",
            sequence=2,
            source_timestamp=datetime(2026, 9, 1, 12, 0, 2),
            kind="conversation.event",
            payload={
                "event_type": "ActionEvent",
                "tool_name": "file_editor",
                "tool_call_id": "call-1",
            },
        ),
        Record(
            record_id="result",
            producer_id="producer",
            trace_id="trace",
            agent_span_id="agent-span",
            sequence=3,
            source_timestamp=datetime(2026, 9, 1, 12, 0, 2) + timedelta(seconds=3.5),
            kind="conversation.event",
            payload={
                "event_type": "ObservationEvent",
                "tool_name": "file_editor",
                "tool_call_id": "call-1",
            },
        ),
    )
    scene = TimelineScene()
    scene.set_timeline([span], records, current_sequence=4)
    view = TimelineViewWidget(scene)
    qtbot.addWidget(view)

    assert scene.available_row_types == ("lifecycle", "tool")
    assert scene.enabled_row_types == scene.available_row_types
    assert scene.lane_names == (
        "Lifecycle · Implementation agent",
        "Tool · Implementation agent",
    )
    lifecycle_label = next(
        item
        for item in scene.items()
        if isinstance(item, QGraphicsTextItem)
        and item.toPlainText() == "Lifecycle\nImplementation agent"
    )
    assert "Lifecycle · Implementation agent" in lifecycle_label.toolTip()
    assert "started, remained active, and finished" in lifecycle_label.toolTip()
    assert scene.record_at(sequence=2, lane=1) == "action"
    assert scene.record_at(sequence=3, lane=1) == "result"
    assert "+0s" in scene.axis_labels
    assert "file_editor" in scene.record_label("action")
    action_interval = scene.record_interval("action")
    assert action_interval.start_seconds == 0
    assert action_interval.duration_seconds == 3.5
    record_items = {
        str(getattr(item, "record_id")): item
        for item in scene.items()
        if isinstance(item, QGraphicsRectItem)
        and isinstance(getattr(item, "record_id", None), str)
    }
    assert record_items["action"].rect().width() == 3.5 * scene.pixels_per_second
    assert record_items["action"].rect().y() < record_items["result"].rect().y()
    subrow_labels = {
        item.toPlainText()
        for item in scene.items()
        if isinstance(item, QGraphicsTextItem)
    }
    assert {"Action", "Result"}.issubset(subrow_labels)


def test_incomplete_lifecycle_covers_sequence_shifted_activity(qtbot) -> None:
    observed_at = datetime(2026, 9, 1, 12)
    span = Span(
        span_id="agent",
        trace_id="trace",
        span_type="agent",
        name="Primary agent",
        agent_span_id="agent",
        start_sequence=1,
        started_at=observed_at,
        status="incomplete",
    )
    records = [
        Record(
            record_id="agent-start",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=1,
            source_timestamp=observed_at,
            kind="agent.started",
        ),
        *[
            Record(
                record_id=f"activity-{index}",
                producer_id="producer",
                trace_id="trace",
                span_id="agent",
                agent_span_id="agent",
                sequence=index + 2,
                source_timestamp=observed_at,
                kind="conversation.event",
                payload={"event_type": "ActionEvent"},
            )
            for index in range(8)
        ],
    ]
    scene = TimelineScene()

    scene.set_timeline([span], records, current_sequence=9)

    lifecycle = next(
        item
        for item in scene.items()
        if isinstance(item, TimelineSpanItem) and item.span_id == "agent"
    )
    activity_right = max(
        item.rect().right()
        for item in scene.items()
        if isinstance(item, TimelineRecordItem)
        and item.record_id.startswith("activity-")
    )
    assert lifecycle.rect().right() >= activity_right


def test_back_to_back_tool_results_use_one_row_with_arrows(qtbot) -> None:
    started_at = datetime(2026, 9, 1, 12)
    records = [
        Record(
            record_id=f"result-{index}",
            producer_id="producer",
            trace_id="trace",
            sequence=index + 1,
            source_timestamp=started_at + timedelta(milliseconds=index),
            kind="conversation.event",
            payload={
                "event_type": "ObservationEvent",
                "tool_name": "file_editor",
                "tool_call_id": f"call-{index}",
            },
        )
        for index in range(5)
    ]
    scene = TimelineScene()

    scene.set_timeline([], records, current_sequence=5)

    result_items = {
        str(getattr(item, "record_id")): item
        for item in scene.items()
        if isinstance(item, QGraphicsRectItem)
        and isinstance(getattr(item, "record_id", None), str)
    }
    result_y_positions = [
        result_items[f"result-{index}"].rect().y() for index in range(5)
    ]
    result_x_positions = [
        result_items[f"result-{index}"].rect().x() for index in range(5)
    ]
    arrows = [
        item for item in scene.items() if isinstance(item, TimelineSequenceArrowItem)
    ]

    assert len(set(result_y_positions)) == 1
    for index in range(1, 5):
        previous = result_items[f"result-{index - 1}"].rect()
        assert result_x_positions[index] > previous.right()
    assert len(arrows) == 4
    for arrow in arrows:
        previous = result_items[arrow.previous_record_id].rect()
        following = result_items[arrow.next_record_id].rect()
        assert arrow.start.x() == previous.right()
        assert arrow.start.y() == previous.center().y()
        assert arrow.end.x() == following.left()
        assert arrow.end.y() == following.center().y()
    assert scene.lane_names == ("Tool · Session",)
    assert scene.sceneRect().height() > HEADER_HEIGHT + LANE_HEIGHT


def test_timeline_activity_click_selects_record(qtbot, trace_index) -> None:
    records = [
        Record(
            record_id="agent-start",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=1,
            kind="agent.started",
            payload={"name": "Primary agent"},
        ),
        Record(
            record_id="tool-action",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=2,
            kind="conversation.event",
            payload={"event_type": "ActionEvent", "tool_name": "terminal"},
        ),
        Record(
            record_id="agent-finish",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=3,
            kind="agent.finished",
        ),
    ]
    trace_index.add_records(records)
    window = MainWindow(repository=TraceRepository(trace_index), trace_id="trace")
    qtbot.addWidget(window)

    window.timeline.record_activated.emit("tool-action")

    assert window.selection.current_selection().record_id == "tool-action"
    assert window.timeline.timeline_scene.selected_record_id == "tool-action"
    selected_item = window.timeline.timeline_scene._record_items["tool-action"]
    assert selected_item.selected is True
    assert selected_item.pen().width() == 3
    assert "terminal" in window.inspector.summary.text()

    window.timeline.zoom_in()

    rebuilt_item = window.timeline.timeline_scene._record_items["tool-action"]
    assert rebuilt_item is not selected_item
    assert rebuilt_item.selected is True

    window.navigate_to_agent("agent")

    assert window.timeline.timeline_scene.selected_record_id is None
    assert rebuilt_item.selected is False


def test_timeline_toolbar_controls_zoom_and_fit(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    initial_zoom = window.timeline.zoom_factor

    window.timeline_zoom_in.click()
    assert window.timeline.zoom_factor > initial_zoom
    window.timeline_zoom_out.click()
    assert window.timeline.zoom_factor == initial_zoom
    window.timeline_zoom_in.click()
    window.timeline_fit.click()
    assert window.timeline.zoom_factor == 1.0


def test_timeline_mouse_wheel_controls_zoom(qtbot) -> None:
    view = TimelineViewWidget()
    qtbot.addWidget(view)
    initial_zoom = view.zoom_factor
    event = QWheelEvent(
        QPointF(20, 20),
        QPointF(20, 20),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )

    view.wheelEvent(event)

    assert view.zoom_factor > initial_zoom


def test_workspace_assembles_named_panels_and_inspector_tabs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.findChild(RunTableModel) is not None
    assert window.findChild(TimelineViewWidget, "timeline") is not None
    inspector = window.findChild(InspectorWidget, "inspector")
    assert inspector is not None
    assert [inspector.tabText(index) for index in range(inspector.count())] == [
        "Summary",
        "Explanation",
        "Input",
        "Output",
        "Metrics",
        "Changes",
        "Evidence",
    ]


def test_inspector_ctrl_f_searches_active_text_tab(qtbot) -> None:
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    inspector.summary.setText("alpha beta alpha")
    inspector.explanation.setText("explanation alpha")
    inspector.show()
    qtbot.waitExposed(inspector)
    inspector.activateWindow()
    inspector.summary.setFocus()
    qtbot.waitUntil(inspector.summary.hasFocus)

    qtbot.keyClick(
        inspector.summary,
        Qt.Key.Key_F,
        Qt.KeyboardModifier.ControlModifier,
    )
    qtbot.keyClicks(inspector.find_input, "alpha")

    assert inspector.find_bar.isVisible()
    assert inspector.find_status.text() == "1/2"
    assert inspector.summary.textCursor().selectedText() == "alpha"
    assert inspector.summary.textCursor().selectionStart() == 0

    qtbot.keyPress(inspector.find_input, Qt.Key.Key_Return)

    assert inspector.find_status.text() == "2/2"
    assert inspector.summary.textCursor().selectionStart() == 11

    qtbot.keyPress(
        inspector.find_input,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ShiftModifier,
    )

    assert inspector.find_status.text() == "1/2"
    inspector.setCurrentWidget(inspector.explanation)
    assert inspector.find_status.text() == "1/1"
    assert inspector.explanation.textCursor().selectedText() == "alpha"

    qtbot.keyPress(inspector.find_input, Qt.Key.Key_Escape)

    assert not inspector.find_bar.isVisible()


def test_run_explanation_distinguishes_trace_lifetime(qtbot) -> None:
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    inspector.show_run(
        RunSummary(
            trace_id="trace",
            status="completed",
            started_at=datetime(2026, 9, 1, 12),
            completed_at=datetime(2026, 9, 1, 13),
            record_count=42,
        )
    )

    assert "Run overview" in inspector.explanation.text()
    assert "3600.000 seconds" in inspector.explanation.text()
    assert "can include idle time" in inspector.explanation.text()


def test_workspace_restores_settings_and_detaches_on_close(tmp_path, qtbot) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    first = MainWindow()
    qtbot.addWidget(first)
    first.resize(940, 620)
    first.save_settings(settings)

    detached = []
    restored = MainWindow()
    qtbot.addWidget(restored)
    restored.restore_settings(settings)
    restored.add_detach_callback(lambda: detached.append(True))

    assert restored.size().width() == 940
    assert restored.size().height() == 620
    restored.close()
    restored.close()
    assert detached == [True]


def test_workspace_clamps_stale_oversized_window_settings(tmp_path, qtbot) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("window/width", 81_229)
    settings.setValue("window/height", 42_000)
    window = MainWindow()
    qtbot.addWidget(window)

    window.restore_settings(settings)

    screen = QApplication.primaryScreen()
    assert screen is not None
    assert window.width() <= max(1200, screen.availableGeometry().width())
    assert window.height() <= max(760, screen.availableGeometry().height())


def test_selecting_activity_shows_record_payload(qtbot, trace_index) -> None:
    record = Record(
        record_id="tool-record",
        producer_id="producer",
        trace_id="trace",
        sequence=1,
        kind="tool.request",
        payload={"command": "make test"},
    )
    trace_index.add_records([record])
    window = MainWindow(repository=TraceRepository(trace_index), trace_id="trace")
    qtbot.addWidget(window)

    window.tree.setCurrentIndex(window.tree_model.index(0, 0))
    window._tree_item_selected(window.tree.currentIndex())

    assert "tool.request" in window.inspector.summary.text()
    assert "make test" in window.inspector.summary.text()
    assert "Recorded activity" in window.inspector.explanation.text()
    assert "immutable trace record" in window.inspector.explanation.text()
    assert "command" in window.inspector.explanation.text()


def test_llm_response_summary_formats_chat_output(qtbot) -> None:
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    request = Record(
        record_id="request",
        producer_id="producer",
        trace_id="trace",
        sequence=4,
        kind="llm.request",
        llm_response_id="response-1",
        payload={
            "filename": "completion.json",
            "request": {
                "instructions": "Keep changes focused.",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Fix the test"}],
                    },
                    {
                        "type": "function_call",
                        "name": "terminal",
                        "call_id": "call-0",
                        "arguments": '{"command":"pytest"}',
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-0",
                        "output": "One test failed",
                    },
                ],
                "tools": [{"name": "terminal"}],
            },
        },
    )
    response = Record(
        record_id="response",
        producer_id="producer",
        trace_id="trace",
        sequence=5,
        kind="llm.response",
        llm_response_id="response-1",
        payload={
            "filename": "completion.json",
            "response": {
                "id": "response-1",
                "model": "gpt-5",
                "object": "chat.completion",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "I found the failing assertion.",
                            "reasoning_content": "I should inspect the fixture.",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "terminal",
                                        "arguments": '{"command":"pytest -q"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
            "usage_summary": {
                "prompt_tokens": 42,
                "completion_tokens": 12,
            },
            "cost": 0.002,
            "latency_sec": 0.25,
        },
    )

    inspector.show_record(request)

    request_summary = inspector.summary.text()
    assert request_summary.startswith("LLM request\n")
    assert "SYSTEM INSTRUCTIONS\nKeep changes focused." in request_summary
    assert "INPUT 1: USER MESSAGE\nFix the test" in request_summary
    assert "INPUT 2: ASSISTANT TOOL CALL\nTool: terminal" in request_summary
    assert "INPUT 3: TOOL RESULT\nCall ID: call-0\nOne test failed" in request_summary
    assert "AVAILABLE TOOLS (1)\n- terminal" in request_summary
    llm_input = inspector.input.text()
    assert llm_input.startswith("LLM REQUEST\n")
    assert "INPUT 1: USER MESSAGE\nFix the test" in llm_input
    assert '"role"' not in llm_input
    assert inspector.output.text() == "This activity is not an LLM response."

    inspector.show_record(response)

    summary = inspector.summary.text()
    assert summary.startswith("LLM response\n")
    assert "MODEL OUTPUT\nI found the failing assertion." in summary
    assert "MODEL REASONING\nI should inspect the fixture." in summary
    assert "TOOL CALLS (1)\n1. terminal" in summary
    assert "Command: pytest -q" in summary
    assert "Prompt Tokens: 42" in summary
    assert "Finish reason: tool_calls" in summary
    assert '"choices"' not in summary
    assert '\\"command\\"' not in summary
    assert inspector.input.text() == "This activity is not an LLM request."
    assert "MODEL OUTPUT\nI found the failing assertion." in inspector.output.text()


def test_llm_response_summary_formats_responses_api_output(qtbot) -> None:
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    record = Record(
        record_id="response",
        producer_id="producer",
        trace_id="trace",
        sequence=5,
        kind="llm.response",
        payload={
            "completion": {
                "response": {
                    "id": "response-2",
                    "model": "gpt-5-codex",
                    "object": "response",
                    "output": [
                        {
                            "type": "reasoning",
                            "summary": [
                                {"type": "summary_text", "text": "Check tests"}
                            ],
                        },
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Tests pass."}],
                        },
                        {
                            "type": "function_call",
                            "call_id": "call-2",
                            "name": "terminal",
                            "arguments": '{"command":"pytest"}',
                        },
                    ],
                    "usage": {"input_tokens": 20, "output_tokens": 8},
                }
            }
        },
    )

    inspector.show_record(record)

    summary = inspector.summary.text()
    assert "MODEL OUTPUT\nTests pass." in summary
    assert "MODEL REASONING\nCheck tests" in summary
    assert "TOOL CALLS (1)\n1. terminal" in summary
    assert "Command: pytest" in summary
    assert "Input Tokens: 20" in summary


def test_generic_summary_formats_nested_payload_without_json(qtbot) -> None:
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    record = Record(
        record_id="action",
        producer_id="producer",
        trace_id="trace",
        sequence=6,
        kind="conversation.event",
        payload={
            "event_type": "ActionEvent",
            "source": "agent",
            "tool_name": "terminal",
            "action": {
                "command": "pytest -q",
                "env": {"CI": True},
                "tags": ["focused", "fast"],
            },
        },
    )

    inspector.show_record(record)

    summary = inspector.summary.text()
    assert summary.startswith("Tool action\n")
    assert "Tool Name: terminal" in summary
    assert "Action:\n  Command: pytest -q" in summary
    assert "Env:\n    CI: Yes" in summary
    assert "Tags:\n    - focused\n    - fast" in summary
    assert '"command"' not in summary
    assert "{" not in summary


def test_message_summary_formats_text_content_and_metadata(qtbot) -> None:
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    record = Record(
        record_id="message",
        producer_id="producer",
        trace_id="trace",
        agent_span_id="agent",
        sequence=4,
        kind="conversation.event",
        payload={
            "event_type": "MessageEvent",
            "id": "message-id",
            "source": "user",
            "sender": "developer",
            "timestamp": "2026-09-01T12:00:00",
            "activated_skills": ["repository-guide"],
            "extended_content": [
                {"type": "text", "text": "Inspect carefully\nPreserve behavior"}
            ],
            "llm_message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "First paragraph\n\nSecond paragraph",
                    },
                    {"type": "image", "image_urls": ["image-data"]},
                ],
            },
        },
    )

    inspector.show_record(record)

    summary = inspector.summary.text()
    assert summary.startswith("Message\n")
    assert "Source: user" in summary
    assert "Role: user" in summary
    assert "Sender: developer" in summary
    assert "First paragraph\n\nSecond paragraph" in summary
    assert "[Image attachment: 1 image]" in summary
    assert "PROMPT EXTENSION\nInspect carefully\nPreserve behavior" in summary
    assert "Activated skills: repository-guide" in summary
    assert '"text"' not in summary
    assert "\\n" not in summary
    highlighted_sections = {
        selection.cursor.selectedText().replace(
            "\u2029", "\n"
        ): selection.format.background().color().name()
        for selection in inspector.summary.extraSelections()
    }
    user_color = next(
        color
        for text, color in highlighted_sections.items()
        if text.startswith("USER PROMPT\nFirst paragraph")
    )
    template_color = next(
        color
        for text, color in highlighted_sections.items()
        if text.startswith("OPENHANDS PROMPT EXTENSION\nInspect carefully")
    )
    assert user_color == "#e5f5eb"
    assert template_color == "#e5eff8"
    assert user_color != template_color


def test_system_prompt_summary_formats_prompt_and_dynamic_context(qtbot) -> None:
    inspector = InspectorWidget()
    qtbot.addWidget(inspector)
    record = Record(
        record_id="system-prompt",
        producer_id="producer",
        trace_id="trace",
        agent_span_id="agent",
        sequence=3,
        kind="conversation.event",
        payload={
            "event_type": "SystemPromptEvent",
            "id": "system-prompt-id",
            "source": "agent",
            "timestamp": "2026-09-01T12:00:00",
            "system_prompt": {
                "type": "text",
                "text": "Follow instructions.\n\nUse available tools carefully.",
            },
            "dynamic_context": {
                "type": "text",
                "text": "<SKILLS>\n  <name>code-review</name>\n</SKILLS>",
            },
            "tools": [
                {"name": "terminal", "description": "Run commands"},
                {"name": "file_editor", "description": "Edit files"},
            ],
        },
    )

    inspector.show_record(record)

    summary = inspector.summary.text()
    assert summary.startswith("System prompt\n")
    assert "Follow instructions.\n\nUse available tools carefully." in summary
    assert "DYNAMIC CONTEXT\n<SKILLS>\n  <name>code-review</name>" in summary
    assert "AVAILABLE TOOLS (2)\n- terminal\n- file_editor" in summary
    assert '"text"' not in summary
    assert "\\n" not in summary
    highlighted_text = "\n".join(
        selection.cursor.selectedText().replace("\u2029", "\n")
        for selection in inspector.summary.extraSelections()
    )
    assert "OPENHANDS TEMPLATE\nSYSTEM INSTRUCTIONS" in highlighted_text
    assert "OPENHANDS DYNAMIC CONTEXT\n<SKILLS>" in highlighted_text
    assert all(
        selection.format.background().color().name() == "#e5eff8"
        for selection in inspector.summary.extraSelections()
    )


def test_system_prompt_summary_includes_following_user_request(
    qtbot, trace_index
) -> None:
    system_prompt = Record(
        record_id="system-prompt",
        producer_id="producer",
        trace_id="trace",
        agent_span_id="agent",
        sequence=3,
        kind="conversation.event",
        payload={
            "event_type": "SystemPromptEvent",
            "source": "agent",
            "system_prompt": {"type": "text", "text": "OpenHands template"},
            "tools": [],
        },
    )
    user_request = Record(
        record_id="user-request",
        producer_id="producer",
        trace_id="trace",
        agent_span_id="agent",
        sequence=4,
        kind="conversation.event",
        payload={
            "event_type": "MessageEvent",
            "source": "user",
            "llm_message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Implement the original requested feature.",
                    }
                ],
            },
        },
    )
    trace_index.add_records([system_prompt, user_request])
    window = MainWindow(repository=TraceRepository(trace_index), trace_id="trace")
    qtbot.addWidget(window)

    window.navigate_to_record("system-prompt")

    summary = window.inspector.summary.text()
    assert "USER REQUEST (SEQUENCE 4)" in summary
    assert "Implement the original requested feature." in summary
    assert summary.index("USER REQUEST") < summary.index("OPENHANDS TEMPLATE")
    user_highlight = next(
        selection
        for selection in window.inspector.summary.extraSelections()
        if selection.cursor.selectedText().startswith("USER REQUEST")
    )
    assert user_highlight.format.background().color().name() == "#e5f5eb"


def test_long_inspector_payload_does_not_resize_workspace_panels(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    QApplication.processEvents()
    splitter = window.findChild(QSplitter)
    assert splitter is not None
    initial_sizes = splitter.sizes()
    record = Record(
        record_id="large-record",
        producer_id="producer",
        trace_id="trace",
        sequence=1,
        kind="conversation.event",
        payload={"content": "long payload " * 1000},
    )

    window.inspector.show_record(record)
    QApplication.processEvents()

    assert splitter.sizes() == initial_sizes


def test_workspace_panels_are_draggable_and_persisted(tmp_path, qtbot) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    QApplication.processEvents()
    splitter = window.findChild(QSplitter)
    assert splitter is not None
    initial_sizes = splitter.sizes()
    handle = splitter.handle(2)

    qtbot.mousePress(handle, Qt.MouseButton.LeftButton)
    qtbot.mouseMove(handle, handle.rect().center() + QPoint(80, 0))
    qtbot.mouseRelease(handle, Qt.MouseButton.LeftButton)
    QApplication.processEvents()

    adjusted_sizes = splitter.sizes()
    assert adjusted_sizes != initial_sizes
    window.save_settings(settings)

    restored = MainWindow()
    qtbot.addWidget(restored)
    restored.restore_settings(settings)
    restored.show()
    QApplication.processEvents()
    restored_splitter = restored.findChild(QSplitter)
    assert restored_splitter is not None
    assert restored_splitter.sizes() == adjusted_sizes


def test_timeline_row_types_are_configurable_and_persisted(
    tmp_path, qtbot, trace_index
) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    records = [
        Record(
            record_id="agent-start",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=1,
            kind="agent.started",
            payload={"name": "Primary agent"},
        ),
        Record(
            record_id="action",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=2,
            kind="conversation.event",
            payload={"event_type": "ActionEvent", "tool_name": "terminal"},
        ),
        Record(
            record_id="result",
            producer_id="producer",
            trace_id="trace",
            span_id="agent",
            agent_span_id="agent",
            sequence=3,
            kind="conversation.event",
            payload={"event_type": "ObservationEvent", "tool_name": "terminal"},
        ),
    ]
    trace_index.add_records(records)
    repository = TraceRepository(trace_index)
    window = MainWindow(repository=repository, trace_id="trace")
    qtbot.addWidget(window)

    assert window.timeline_row_actions["tool"].isChecked()
    assert not window.timeline_row_actions["errors"].isEnabled()
    assert not window.timeline_row_actions["state"].isChecked()
    assert not window.timeline_row_actions["metrics"].isChecked()
    window.timeline_row_actions["tool"].trigger()
    assert "Tool · Primary agent" not in window.timeline.timeline_scene.lane_names
    window.save_settings(settings)

    restored = MainWindow()
    qtbot.addWidget(restored)
    restored.restore_settings(settings)
    restored.set_repository(repository, "trace")
    assert not restored.timeline_row_actions["tool"].isChecked()
    assert "Tool · Primary agent" not in restored.timeline.timeline_scene.lane_names
