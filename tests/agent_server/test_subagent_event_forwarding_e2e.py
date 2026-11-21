"""Agent-server config, subscriber isolation, and E2E live stream tests for #3907."""

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config, load_config
from openhands.agent_server.conversation_service import (
    AutoTitleSubscriber,
    ConversationService,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.pub_sub import Subscriber
from openhands.agent_server.telemetry.subscriber import TelemetrySubscriber
from openhands.sdk import LLM, Agent, Event, Message, TextContent, Tool
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.secret import StaticSecret
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.subagent.registry import _reset_registry_for_tests
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import register_tool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.preset import register_builtins_agents
from openhands.tools.preset.default import register_default_tools
from openhands.tools.task import TaskToolSet
from openhands.tools.task.manager import TaskManager


def test_oh_forward_subagent_events_config_and_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """OH_FORWARD_SUBAGENT_EVENTS defaults to False and parses True from env."""
    cfg_path = tmp_path / "nonexistent_config.json"
    monkeypatch.delenv("OH_FORWARD_SUBAGENT_EVENTS", raising=False)
    assert Config().forward_subagent_events is False
    assert load_config(cfg_path).forward_subagent_events is False

    monkeypatch.setenv("OH_FORWARD_SUBAGENT_EVENTS", "true")
    loaded = load_config(cfg_path)
    assert loaded.forward_subagent_events is True

    svc = ConversationService(
        conversations_dir=tmp_path / "convs",
        forward_subagent_events=loaded.forward_subagent_events,
    )
    assert svc.forward_subagent_events is True


@pytest.mark.asyncio
async def test_sink_delivers_from_worker_thread_without_persisting(tmp_path: Path):
    """Worker-thread forwarding reaches _pub_sub without touching state.events."""
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(tmp_path / "ws")),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    svc = EventService(
        stored=stored,
        conversations_dir=tmp_path / "convs",
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        forward_subagent_events=True,
    )
    svc._main_loop = asyncio.get_running_loop()

    received: list[Event] = []

    class ProbeSubscriber(Subscriber[Event]):
        async def __call__(self, event: Event) -> None:
            received.append(event)

    svc._pub_sub.subscribe(ProbeSubscriber())
    mgr = TaskManager(sub_event_sink=svc._make_subagent_event_sink())
    fwd = mgr._make_forwarding_callback(parent_tool_use_id="toolu_worker_99")
    assert fwd is not None

    original = MessageEvent(
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text="inner")]),
    )
    done = threading.Event()

    def worker() -> None:
        fwd(original)
        done.set()

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2)
    assert done.is_set()
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].parent_tool_use_id == "toolu_worker_99"
    assert original.parent_tool_use_id is None


@pytest.mark.asyncio
async def test_internal_subscribers_ignore_forwarded_subagent_events():
    """Internal parent subscribers ignore events carrying parent_tool_use_id."""
    fake_service = MagicMock()
    fake_service.stored.title = None
    auto_title = AutoTitleSubscriber(service=fake_service)

    sub_user_msg = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="subagent prompt")]),
        parent_tool_use_id="toolu_sub_prompt",
    )
    await auto_title(sub_user_msg)
    fake_service.save_meta.assert_not_called()

    sink_mock = MagicMock()
    telemetry = TelemetrySubscriber(
        conversation_id=uuid4(),
        sink=sink_mock,
        factory=MagicMock(),
        context=MagicMock(),
    )
    sub_err = AgentErrorEvent(
        error="sub failure",
        tool_name="file_editor",
        tool_call_id="call_1",
        parent_tool_use_id="toolu_sub_prompt",
    )
    await telemetry(sub_err)
    assert telemetry._event_count == 0
    sink_mock.emit.assert_not_called()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_enabled", [False, True])
async def test_event_service_live_stream_e2e_flag_off_vs_on(
    tmp_path: Path, flag_enabled: bool
):
    """End-to-end check over EventService live stream with flag disabled vs enabled:
    verifies tagging, ordering (TaskAction -> sub-events -> TaskObservation), wire
    serialization (exclude_none=True), secret masking, and state.events isolation.
    """
    _reset_registry_for_tests()
    register_default_tools(enable_browser=False)
    register_tool("TaskToolSet", TaskToolSet)
    register_builtins_agents(enable_browser=False)

    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True)
    secret_val = "sk-live-wire-secret-554433"
    data_file = ws_dir / "config.env"
    data_file.write_text(f"SERVICE_PORT=8080\nAPI_KEY={secret_val}\n", encoding="utf-8")

    script: list[Message | Exception] = [
        _tool_call_msg(
            "toolu_e2e_task_42",
            "task",
            prompt="Read config.env and report SERVICE_PORT",
            subagent_type="general-purpose",
            description="Inspect config.env",
        ),
        _tool_call_msg(
            "toolu_e2e_sub_view",
            "file_editor",
            command="view",
            path=str(data_file),
        ),
        _text_msg(f"SERVICE_PORT is 8080 (key={secret_val})"),
        _text_msg("The service port is 8080."),
    ]

    scripted_llm = TestLLM.from_messages(script)
    llm = LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="e2e-llm")
    agent = Agent(
        llm=llm,
        tools=[Tool(name="TaskToolSet"), Tool(name=FileEditorTool.name)],
    )
    stored = StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(ws_dir)),
        confirmation_policy=NeverConfirm(),
        secrets={"API_KEY": StaticSecret(value=SecretStr(secret_val))},
        initial_message=None,
        metrics=None,
        created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )

    wire_frames: list[dict[str, Any]] = []

    class WireCaptureSubscriber(Subscriber[Event]):
        async def __call__(self, event: Event) -> None:
            wire_frames.append(event.model_dump(mode="json", exclude_none=True))

    svc = EventService(
        stored=stored,
        conversations_dir=tmp_path / "conversations",
        agent=agent,
        forward_subagent_events=flag_enabled,
        lease_ttl_seconds=0.0,
    )
    from unittest.mock import patch

    def _sync_completion(_self_llm, messages, **kwargs):
        return scripted_llm.completion(messages, **kwargs)

    async def _async_completion(_self_llm, messages, **kwargs):
        return scripted_llm.completion(messages, **kwargs)

    with (
        patch.object(LLM, "completion", new=_sync_completion),
        patch.object(LLM, "acompletion", new=_async_completion),
    ):
        await svc.start()
        try:
            await svc.subscribe_to_events(WireCaptureSubscriber())
            await svc.send_message(
                Message(
                    role="user", content=[TextContent(text="What is SERVICE_PORT?")]
                ),
                run=True,
            )
            status = await svc.wait_for_run_completion(timeout=15.0)
            assert status.value == "finished"
            if svc._callback_wrapper is not None:
                await asyncio.to_thread(svc._callback_wrapper.wait_for_pending, 5.0)
            await asyncio.sleep(0.05)

            persisted_events = list(svc.get_conversation().state.events)
        finally:
            await svc.close()
            _reset_registry_for_tests()

    assert all(ev.parent_tool_use_id is None for ev in persisted_events)
    persisted_tool_names = [
        ev.tool_name
        for ev in persisted_events
        if isinstance(ev, (ActionEvent, ObservationEvent))
    ]
    assert persisted_tool_names == ["task", "task"]

    wire_blob = json.dumps(wire_frames)
    assert secret_val not in wire_blob

    if not flag_enabled:
        assert all("parent_tool_use_id" not in frame for frame in wire_frames)
        assert not any(frame.get("tool_name") == "file_editor" for frame in wire_frames)
    else:
        tagged_indices = [
            i
            for i, f in enumerate(wire_frames)
            if f.get("parent_tool_use_id") == "toolu_e2e_task_42"
        ]
        assert len(tagged_indices) >= 3
        assert any(
            wire_frames[i].get("kind") == "ActionEvent"
            and wire_frames[i].get("tool_name") == "file_editor"
            for i in tagged_indices
        )
        assert any(
            wire_frames[i].get("kind") == "ObservationEvent"
            and wire_frames[i].get("tool_name") == "file_editor"
            for i in tagged_indices
        )

        task_action_idx = next(
            i
            for i, f in enumerate(wire_frames)
            if f.get("kind") == "ActionEvent" and f.get("tool_name") == "task"
        )
        task_obs_idx = next(
            i
            for i, f in enumerate(wire_frames)
            if f.get("kind") == "ObservationEvent" and f.get("tool_name") == "task"
        )
        assert all(task_action_idx < idx < task_obs_idx for idx in tagged_indices)

        untagged_tools = [
            f.get("tool_name")
            for f in wire_frames
            if "parent_tool_use_id" not in f
            and f.get("kind") in ("ActionEvent", "ObservationEvent")
        ]
        assert untagged_tools == ["task", "task"]
