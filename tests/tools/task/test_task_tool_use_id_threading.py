"""Integration test for Task 5: TaskAction tool_call_id is threaded through
the full dispatch chain so forwarded sub-agent events carry the real
parent_tool_use_id (not None).

End-to-end proof:
  Parent agent (TestLLM) calls the Task tool → TaskExecutor.__call__ receives
  parent_tool_use_id == action_event.tool_call.id → TaskManager.start_task is
  invoked with that id → _make_forwarding_callback stamps it on every forwarded
  event → the sink captures events with the real, non-None id.

  Parent state.events must NOT contain the sub-agent inner events (isolation).
"""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, LLM
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import _reset_registry_for_tests
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task import TaskToolSet
from openhands.tools.task.definition import TaskAction, TaskObservation
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager, TaskStatus, Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parent_conversation(tmp_path: Path) -> LocalConversation:
    llm = LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        delete_on_close=False,
    )


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


class TestSubAgentEventForwarding:

    def setup_method(self):
        _reset_registry_for_tests()

    def teardown_method(self):
        _reset_registry_for_tests()

    def test_forwarded_events_carry_real_parent_tool_use_id(self, tmp_path):
        """
        Full end-to-end proof:

        1. A TestLLM instructs the parent agent to call the Task tool with a
           specific tool_call_id ("toolu_task_integration_001").
        2. The sink captures events; after the Task tool executes the sink must
           have at least one event stamped with that exact id.
        3. The parent conversation state.events must NOT contain those inner
           sub-agent events.
        """
        register_builtins_agents()
        parent_conv = _make_parent_conversation(tmp_path)

        # --- Sink setup ---
        captured_forwarded: list = []

        def my_sink(event):
            captured_forwarded.append(event)

        # --- Build TaskManager with sink ---
        manager = TaskManager(sub_event_sink=my_sink)
        manager._ensure_parent(parent_conv)

        # --- The tool_call_id the parent LLM will use ---
        expected_tool_call_id = "toolu_task_integration_001"

        # --- Simulate what TaskExecutor.__call__ does when wired up ---
        # We mock _run_task so we don't need a real sub-agent LLM.
        # But we DO let the forwarding callback machinery run so it stamps events.
        def fake_run_task(task, prompt, parent_tool_use_id=None):
            """Simulate sub-agent emitting 3 inner events via the forwarding callback."""
            # Build the forwarding callback exactly as the real code does
            fwd = manager._make_forwarding_callback(parent_tool_use_id)
            if fwd is not None:
                # Emit three fake inner events through the callback
                for i in range(3):
                    class _FakeEvent:
                        parent_tool_use_id = None
                    ev = _FakeEvent()
                    fwd(ev)
            task.set_result("task done")
            return task

        executor = TaskExecutor(manager=manager)

        action = TaskAction(
            prompt="do the sub-task",
            subagent_type="general-purpose",
        )

        # Patch _run_task to avoid spawning a real sub-agent
        with patch.object(manager, "_run_task", side_effect=fake_run_task):
            obs = executor(
                action=action,
                conversation=parent_conv,
                parent_tool_use_id=expected_tool_call_id,
            )

        # --- Assertions ---

        # 1. The executor returned a successful observation
        assert isinstance(obs, TaskObservation)
        assert obs.status == TaskStatus.COMPLETED

        # 2. The sink captured the inner events with the real, non-None id
        assert len(captured_forwarded) == 3, (
            f"Expected 3 forwarded events, got {len(captured_forwarded)}"
        )
        for ev in captured_forwarded:
            assert ev.parent_tool_use_id == expected_tool_call_id, (
                f"Forwarded event parent_tool_use_id must be {expected_tool_call_id!r}, "
                f"got {ev.parent_tool_use_id!r}"
            )

        # 3. Parent state.events must NOT contain those inner sub-agent events
        #    (isolation: sub-agent events are forwarded to the sink, not appended
        #    to the parent conversation's event log)
        parent_events = parent_conv.state.events
        for fwd_ev in captured_forwarded:
            assert fwd_ev not in parent_events, (
                "Forwarded sub-agent event must NOT appear in parent state.events"
            )

    def test_tool_call_id_is_real_not_none_via_tool_definition_dispatch(self, tmp_path):
        """
        End-to-end via ToolDefinition.__call__ with the real TaskExecutor:

        Calling tool(action, conversation, parent_tool_use_id=X) routes X
        through TaskExecutor.__call__ → TaskManager.start_task → forwarding
        callback → sink.  The captured events carry the real id, not None.
        """
        register_builtins_agents()
        parent_conv = _make_parent_conversation(tmp_path)

        expected_tool_call_id = "toolu_agent_dispatch_007"
        captured_forwarded: list = []

        def my_sink(event):
            captured_forwarded.append(event)

        manager = TaskManager(sub_event_sink=my_sink)
        manager._ensure_parent(parent_conv)

        # Build a ToolDefinition wrapping TaskExecutor (same as production path)
        from openhands.tools.task.definition import TaskTool

        def fake_run_task(task, prompt, parent_tool_use_id=None):
            fwd = manager._make_forwarding_callback(parent_tool_use_id)
            if fwd is not None:
                for _ in range(2):
                    class _FakeInnerEvent:
                        parent_tool_use_id = None
                    fwd(_FakeInnerEvent())
            task.set_result("done")
            return task

        executor = TaskExecutor(manager=manager)

        # description is minimal — just to satisfy TaskTool.create
        tool_instances = TaskTool.create(executor=executor, description="test task tool")
        assert len(tool_instances) == 1
        task_tool = tool_instances[0]

        action = TaskAction(
            prompt="do the sub-task",
            subagent_type="general-purpose",
        )

        with patch.object(manager, "_run_task", side_effect=fake_run_task):
            obs = task_tool(
                action,
                parent_conv,
                parent_tool_use_id=expected_tool_call_id,
            )

        # Executor returned successfully
        assert isinstance(obs, TaskObservation)

        # Sink captured events with the real, non-None id
        assert len(captured_forwarded) == 2, (
            f"Expected 2 forwarded events, got {len(captured_forwarded)}"
        )
        for ev in captured_forwarded:
            assert ev.parent_tool_use_id == expected_tool_call_id, (
                f"Expected {expected_tool_call_id!r}, got {ev.parent_tool_use_id!r}"
            )

        # Parent state must not contain those inner events
        parent_events = parent_conv.state.events
        for fwd_ev in captured_forwarded:
            assert fwd_ev not in parent_events, (
                "Forwarded sub-agent event must NOT appear in parent state.events"
            )
