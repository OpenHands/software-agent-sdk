"""Reproduce / guard OpenHands/OpenHands#16341 activity gap during tasks.

Reporter symptom (Agent Canvas 1.10.0): when the parent agent delegates to the
built-in ``code-explorer`` subagent, the conversation hangs for several minutes
and then surfaces the EventService crash-recovery AgentErrorEvent:

  "A restart occurred while this tool was in progress..."

Root cause: while TaskExecutor blocks on a subagent, the parent conversation
event log stalls. EventService only refreshes idle timers on parent events
(or ACP heartbeats), so long delegations look idle and runtime killers restart
the process. The unmatched ``task`` ActionEvent is then crash-recovered.

Fix: TaskManager pulses ``parent.notify_activity()`` during subagent work, and
EventService wires that callback to ``update_last_execution_time`` / ``touch``.
"""

from __future__ import annotations

import json
import threading
import time

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event.llm_convertible import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.tools.task import TaskToolSet


def test_issue_16341_blocking_task_pulses_parent_activity(tmp_path):
    """Parent activity heartbeats fire while TaskExecutor is blocked."""
    _reset_registry_for_tests()

    release = threading.Event()
    saw_task_action = threading.Event()
    activity_pulses: list[float] = []
    parent_event_kinds: list[str] = []
    kinds_lock = threading.Lock()

    class SlowTestLLM(TestLLM):
        def completion(self, *args, **kwargs):
            assert release.wait(timeout=10), "release not signaled"
            return super().completion(*args, **kwargs)

    def on_parent_event(event) -> None:
        with kinds_lock:
            parent_event_kinds.append(type(event).__name__)
        if isinstance(event, ActionEvent) and event.tool_name == "task":
            saw_task_action.set()

    parent_llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="")],
                tool_calls=[
                    MessageToolCall(
                        id="call_1",
                        name="task",
                        arguments=json.dumps(
                            {
                                "prompt": "Find the files tab component",
                                "subagent_type": "slow_explorer",
                                "description": "code explore",
                            }
                        ),
                        origin="completion",
                    )
                ],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Exploration finished.")],
            ),
        ]
    )
    sub_llm = SlowTestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Found files tab.")])]
    )

    def factory(llm):
        return Agent(llm=sub_llm, tools=[])

    register_agent(
        name="slow_explorer",
        factory_func=factory,
        description="Slow explorer used to expose the parent activity gap",
    )

    agent = Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)])
    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
        callbacks=[on_parent_event],
    )
    conversation.set_on_activity(lambda: activity_pulses.append(time.monotonic()))

    def run_parent() -> None:
        conversation.send_message("Explore the files tab")
        conversation.run()

    worker = threading.Thread(target=run_parent, daemon=True)
    worker.start()

    assert saw_task_action.wait(timeout=10), "parent never emitted task ActionEvent"

    # While the subagent is still blocked, the parent event log stays without a
    # TaskObservation — but activity heartbeats must already have fired so idle
    # trackers do not treat the conversation as dead.
    deadline = time.time() + 5
    while time.time() < deadline and not activity_pulses:
        time.sleep(0.05)
    assert activity_pulses, "expected parent activity pulse during blocking task"

    with kinds_lock:
        assert ObservationEvent.__name__ not in parent_event_kinds

    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive(), "parent conversation hung after subagent released"

    with kinds_lock:
        assert ObservationEvent.__name__ in parent_event_kinds

    _reset_registry_for_tests()


def test_task_executor_interrupt_propagates_to_running_subagent():
    """TaskExecutor.interrupt() reaches the active subagent conversation."""
    import uuid
    from unittest.mock import MagicMock

    from openhands.tools.task.impl import TaskExecutor
    from openhands.tools.task.manager import Task, TaskManager, TaskStatus

    manager = TaskManager()
    parent = MagicMock()
    manager.attach_parent(parent)

    sub_conversation = MagicMock()
    task = Task.model_construct(
        id="task_1",
        status=TaskStatus.RUNNING,
        conversation_id=uuid.uuid4(),
        conversation=sub_conversation,
    )
    with manager._tasks_lock:
        manager._tasks[task.id] = task

    TaskExecutor(manager).interrupt()
    sub_conversation.interrupt.assert_called_once()
