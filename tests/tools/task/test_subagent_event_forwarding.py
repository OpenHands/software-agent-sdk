"""Unit and integration tests for sub-agent inner event forwarding (#3907)."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import PrivateAttr

from openhands.sdk import Agent, Event, LocalConversation, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.call_context import LLMCallContext
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.subagent import register_file_agents
from openhands.sdk.subagent.registry import _reset_registry_for_tests
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.preset import register_builtins_agents
from openhands.tools.preset.default import register_default_tools
from openhands.tools.task import TaskToolSet
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager, TaskStatus


class PromptRecordingLLM(TestLLM):
    """TestLLM that records serialized message payloads sent in each LLM call."""

    _call_messages: list[list[dict[str, Any]]] = PrivateAttr(default_factory=list)

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._call_messages.append([m.model_dump(mode="json") for m in messages])
        return super().completion(
            messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )

    @property
    def call_messages(self) -> list[list[dict[str, Any]]]:
        return self._call_messages


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    register_default_tools(enable_browser=False)
    register_tool("TaskToolSet", TaskToolSet)
    register_builtins_agents(enable_browser=False)
    yield
    _reset_registry_for_tests()


def _tool_call_msg(call_id: str, name: str, **arguments: Any) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=call_id,
                name=name,
                arguments=json.dumps(arguments),
                origin="completion",
            )
        ],
    )


def _text_msg(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def test_task_toolset_wires_sink_from_conv_state(tmp_path: Path):
    llm = TestLLM.from_messages([_text_msg("ok")])
    conv = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    try:
        captured: list[Event] = []
        conv.state.set_sub_event_sink(captured.append)

        tools = TaskToolSet.create(conv_state=conv.state)
        assert len(tools) == 1
        assert isinstance(tools[0].executor, TaskExecutor)
        assert tools[0].executor._manager._sub_event_sink == captured.append
    finally:
        conv.close()


def test_builtin_subagent_forwards_tagged_events_without_polluting_parent_context(
    tmp_path: Path,
):
    """Built-in sub-agent forwards inner events tagged with parent_tool_use_id;
    parent state.events and main-agent prompt stay byte-for-byte identical.
    """
    sample = tmp_path / "notes.txt"
    sample.write_text("alpha=42\n", encoding="utf-8")

    def build_script() -> list[Message | Exception]:
        return [
            _tool_call_msg(
                "toolu_parent_task_001",
                "task",
                prompt="Inspect notes.txt and report alpha.",
                subagent_type="general-purpose",
                description="Read notes.txt",
            ),
            _tool_call_msg(
                "toolu_sub_view_001",
                "file_editor",
                command="view",
                path=str(sample),
            ),
            _text_msg("alpha is 42"),
            _text_msg("Confirmed: alpha is 42."),
        ]

    llm_off = PromptRecordingLLM.from_messages(build_script())
    conv_off = LocalConversation(
        agent=Agent(
            llm=llm_off,
            tools=[Tool(name="TaskToolSet"), Tool(name=FileEditorTool.name)],
        ),
        workspace=str(tmp_path),
        visualizer=None,
    )
    conv_off.send_message("Find alpha in notes.txt")
    conv_off.run()
    parent_events_off = list(conv_off.state.events)
    parent_turn2_prompt_off = llm_off.call_messages[-1]
    conv_off.close()

    _reset_registry_for_tests()
    register_builtins_agents(enable_browser=False)

    forwarded: list[Event] = []
    parent_live_stream: list[Event] = []

    llm_on = PromptRecordingLLM.from_messages(build_script())
    conv_on = LocalConversation(
        agent=Agent(
            llm=llm_on,
            tools=[Tool(name="TaskToolSet"), Tool(name=FileEditorTool.name)],
        ),
        workspace=str(tmp_path),
        visualizer=None,
        callbacks=[parent_live_stream.append],
    )
    conv_on.state.set_sub_event_sink(
        lambda ev: (forwarded.append(ev), parent_live_stream.append(ev))
    )
    conv_on.send_message("Find alpha in notes.txt")
    conv_on.run()
    parent_events_on = list(conv_on.state.events)
    parent_turn2_prompt_on = llm_on.call_messages[-1]
    conv_on.close()

    assert len(forwarded) >= 3
    assert all(ev.parent_tool_use_id == "toolu_parent_task_001" for ev in forwarded)
    assert any(
        isinstance(ev, ActionEvent) and ev.tool_name == "file_editor"
        for ev in forwarded
    )
    assert any(
        isinstance(ev, ObservationEvent) and ev.tool_name == "file_editor"
        for ev in forwarded
    )

    task_action_idx = next(
        i
        for i, ev in enumerate(parent_live_stream)
        if isinstance(ev, ActionEvent) and ev.tool_name == "task"
    )
    task_obs_idx = next(
        i
        for i, ev in enumerate(parent_live_stream)
        if isinstance(ev, ObservationEvent) and ev.tool_name == "task"
    )
    fwd_indices = [
        i
        for i, ev in enumerate(parent_live_stream)
        if ev.parent_tool_use_id == "toolu_parent_task_001"
    ]
    assert fwd_indices
    assert all(task_action_idx < idx < task_obs_idx for idx in fwd_indices)

    assert all(ev.parent_tool_use_id is None for ev in parent_events_on)
    assert [type(e).__name__ for e in parent_events_on] == [
        type(e).__name__ for e in parent_events_off
    ]
    assert json.dumps(parent_turn2_prompt_on, sort_keys=True) == json.dumps(
        parent_turn2_prompt_off, sort_keys=True
    )


def test_file_defined_plugin_subagent_and_resume_forwarding(tmp_path: Path):
    """Plugin/file-defined sub-agent loaded via register_file_agents forwards events,
    and resuming the task updates parent_tool_use_id to the new TaskAction tool_call_id.
    """
    agents_dir = tmp_path / ".agents" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "custom-reviewer.md").write_text(
        "---\n"
        "name: custom-reviewer\n"
        "description: Custom plugin sub-agent for review\n"
        "tools:\n"
        "  - file_editor\n"
        "---\n\n"
        "You are a custom reviewer sub-agent.\n",
        encoding="utf-8",
    )
    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    with patch("openhands.sdk.subagent.load.Path.home", return_value=fake_home):
        registered = register_file_agents(tmp_path)
    assert "custom-reviewer" in registered

    script: list[Message | Exception] = [
        _tool_call_msg(
            "toolu_plugin_call_1",
            "task",
            prompt="Review module.py",
            subagent_type="custom-reviewer",
        ),
        _tool_call_msg(
            "toolu_plugin_sub_1",
            "file_editor",
            command="view",
            path=str(target),
        ),
        _text_msg("Initial review complete."),
        _tool_call_msg(
            "toolu_plugin_call_2",
            "task",
            prompt="Double check module.py",
            subagent_type="custom-reviewer",
            resume="task_00000001",
        ),
        _text_msg("Second review complete."),
        _text_msg("All reviews complete."),
    ]

    forwarded: list[Event] = []
    llm = TestLLM.from_messages(script)
    conv = LocalConversation(
        agent=Agent(
            llm=llm,
            tools=[Tool(name="TaskToolSet"), Tool(name=FileEditorTool.name)],
        ),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "persist"),
        visualizer=None,
    )
    conv.state.set_sub_event_sink(forwarded.append)
    with patch("openhands.sdk.subagent.load.Path.home", return_value=fake_home):
        conv.send_message("Run custom review twice")
        conv.run()
    conv.close()

    call1_events = [
        e for e in forwarded if e.parent_tool_use_id == "toolu_plugin_call_1"
    ]
    call2_events = [
        e for e in forwarded if e.parent_tool_use_id == "toolu_plugin_call_2"
    ]
    assert len(call1_events) >= 3
    assert len(call2_events) >= 2


def test_forwarded_events_are_secret_masked_like_parent_stream(tmp_path: Path):
    """Secret values registered on parent are masked in forwarded sub-agent events."""
    secret_val = "sk-ultra-secret-token-778899"
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(f"TOKEN={secret_val}\n", encoding="utf-8")

    script: list[Message | Exception] = [
        _tool_call_msg(
            "toolu_secret_task_1",
            "task",
            prompt="Read secret.txt",
            subagent_type="code-explorer",
        ),
        _tool_call_msg(
            "toolu_sub_secret_view",
            "file_editor",
            command="view",
            path=str(secret_file),
        ),
        _text_msg(f"Found {secret_val}"),
        _text_msg("Done"),
    ]

    forwarded: list[Event] = []
    llm = TestLLM.from_messages(script)
    conv = LocalConversation(
        agent=Agent(
            llm=llm,
            tools=[Tool(name="TaskToolSet"), Tool(name=FileEditorTool.name)],
        ),
        workspace=str(tmp_path),
        visualizer=None,
    )
    conv.update_secrets({"API_SECRET": secret_val})
    conv.state.set_sub_event_sink(forwarded.append)
    conv.send_message("Check file")
    conv.run()
    conv.close()

    assert forwarded
    for ev in forwarded:
        dumped = ev.model_dump_json()
        assert secret_val not in dumped, (
            f"Unmasked secret leaked in {ev.kind}: {dumped}"
        )
        assert ev.parent_tool_use_id == "toolu_secret_task_1"
    assert any("<secret-hidden>" in ev.model_dump_json() for ev in forwarded)


@pytest.mark.parametrize("failure_mode", ["success", "error", "cancel"])
def test_forwarding_cleaned_up_on_success_error_and_cancel(
    tmp_path: Path, failure_mode: str
):
    """No events forward after _run_task ends, and callbacks release references."""
    forwarded: list[Event] = []
    llm = TestLLM.from_messages([_text_msg("done")])
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    parent.state.set_sub_event_sink(forwarded.append)
    manager = TaskManager()
    manager._ensure_parent(parent)

    task = manager._create_task(
        subagent_type="general-purpose",
        description="cleanup check",
        parent_tool_use_id="toolu_cleanup_01",
    )
    fwd = task.forwarding_callback
    sub_conv = task.conversation
    assert fwd is not None
    assert sub_conv is not None
    assert not fwd.closed

    def fake_run_until_finished(_task_id: str, conv: LocalConversation) -> None:
        if failure_mode == "success":
            conv._on_event(
                MessageEvent(
                    source="agent",
                    llm_message=Message(
                        role="assistant", content=[TextContent(text="ok")]
                    ),
                )
            )
            conv.state.execution_status = ConversationExecutionStatus.FINISHED
        elif failure_mode == "error":
            raise RuntimeError("boom inside sub-agent")
        else:
            conv.pause()
            conv.state.execution_status = ConversationExecutionStatus.PAUSED

    with patch.object(
        manager, "_run_until_finished", side_effect=fake_run_until_finished
    ):
        finished_task = manager._run_task(task, "run it")

    if failure_mode == "success":
        assert finished_task.status == TaskStatus.COMPLETED
    else:
        assert finished_task.status == TaskStatus.ERROR

    assert fwd.closed is True
    assert fwd._sink is None
    assert manager._tasks[task.id].forwarding_callback is None
    assert manager._tasks[task.id].conversation is None

    count_before = len(forwarded)
    fwd(
        MessageEvent(
            source="agent",
            llm_message=Message(role="assistant", content=[TextContent(text="late")]),
        )
    )
    assert len(forwarded) == count_before
    if failure_mode == "success":
        assert all(ev.kind != "PauseEvent" for ev in forwarded)
    parent.close()
