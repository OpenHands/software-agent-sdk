"""Stress test: /server_info idle_time during a blocked real Task dispatch.

Bug class this catches (#5384):
    A parent run genuinely blocked on a real Task/Delegate child emits no
    durable event on the parent's bus for the whole wait -- the child has
    its own conversation/event stream and never touches this process's HTTP
    endpoints. Without a run-level heartbeat, /server_info's idle_time grows
    with wall time for the entire hold instead of reflecting that the run is
    actively doing (child) work, which can trip an idle-based reaper.

Why a gate instead of SlowTestLLM:
    SlowTestLLM's fixed latency races the test's own sampling point. A
    threading.Event gate lets the child's single LLM call return exactly
    when *we* release it, so "idle_time while still held" is a deterministic
    sample, not a timing guess against a sleep.

Caveat intentionally not covered here (documented in event_service.py):
    interrupt()-driven cancellation of a blocked *native-async* dispatch
    stops the heartbeat immediately, even though Task/Delegate's no-op
    interrupt() can leave the dispatch thread itself still running. Proving
    that gap needs a cancellation-focused test, which is a separate concern
    from this run's happy-path idle_time behavior.
"""

import asyncio
import json
import threading

import pytest
from pydantic import PrivateAttr

import openhands.agent_server.event_service as event_service_module
import openhands.agent_server.server_details_router as sdr
from openhands.agent_server.conversation_service import ConversationService
from openhands.sdk import Agent, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.tools.task import TaskToolSet
from tests.agent_server.stress.budgets import TASK_DISPATCH_IDLE_HEARTBEAT
from tests.agent_server.stress.scripts import (
    start_conversation_with_test_llm,
    text_message,
    wait_for_terminal,
)


pytestmark = [pytest.mark.stress, pytest.mark.timeout(30)]


@pytest.fixture(autouse=True)
def _reset_registry():
    """Sub-agent registry is module-global; isolate per test."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.fixture(autouse=True)
def _isolate_idle_clock():
    """/server_info's idle clock is a module global; don't leak it to tests
    that run later in the same ``-m stress`` session."""
    saved = sdr._last_event_time
    yield
    sdr._last_event_time = saved


@pytest.fixture
def fast_activity_signal_interval(monkeypatch: pytest.MonkeyPatch) -> float:
    """Shrink the heartbeat/activity-signal throttle so one hold only needs
    to wait out a single pulse.

    ``ACTIVITY_SIGNAL_INTERVAL`` is the pre-existing ACP/stream activity
    throttle that event_service.py already imports; the candidate patch's
    run-level heartbeat reuses it rather than adding a new constant. It
    exists on both pristine main and the patched tree, so this monkeypatch
    needs no ``raising=False`` guard and never errors at fixture setup on
    either tree.
    """
    interval = TASK_DISPATCH_IDLE_HEARTBEAT.fast_activity_signal_interval_s
    monkeypatch.setattr(event_service_module, "ACTIVITY_SIGNAL_INTERVAL", interval)
    return interval


def _task_tool_call(call_id: str, prompt: str, subagent_type: str) -> MessageToolCall:
    return MessageToolCall(
        id=call_id,
        name="task",
        arguments=json.dumps({"prompt": prompt, "subagent_type": subagent_type}),
        origin="completion",
    )


class _GatedTestLLM(TestLLM):
    """TestLLM whose single scripted call blocks on a gate until released.

    ``entered`` lets the test wait until the sub-agent's worker thread has
    actually reached the call (not merely that ``/run`` was accepted), so
    the hold is exercised deterministically rather than raced against a
    sleep.
    """

    _gate: threading.Event = PrivateAttr(default_factory=threading.Event)
    _entered: threading.Event = PrivateAttr(default_factory=threading.Event)

    def completion(self, *args, **kwargs) -> LLMResponse:
        self._entered.set()
        self._gate.wait(timeout=10.0)
        return super().completion(*args, **kwargs)


async def test_server_info_idle_time_stays_low_while_real_task_child_is_held(
    conversation_service: ConversationService,
    client,
    tmp_path,
    fast_activity_signal_interval: float,
):
    """Hold a real Task child's LLM call open; /server_info's idle_time must
    stay under budget both while held and once the run has finished.

    On pristine main (no run-level heartbeat) the mid-hold sample grows past
    the budget because nothing refreshes the idle clock while the child is
    the only thing doing work.
    """
    budget = TASK_DISPATCH_IDLE_HEARTBEAT
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    child_llm = _GatedTestLLM.from_messages([text_message("child done")])
    assert isinstance(child_llm, _GatedTestLLM)
    # The gate must never strand the child's worker thread -- release it on
    # any exit path, including a failed setup/registration or a failed
    # assertion below, not just the happy path.
    try:
        register_agent(
            name="gated_child",
            factory_func=lambda llm: Agent(llm=child_llm, tools=[]),
            description="stress test: held Task child",
        )

        parent_llm = TestLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text="delegating")],
                    tool_calls=[
                        _task_tool_call(
                            "call_1", prompt="do it", subagent_type="gated_child"
                        )
                    ],
                ),
                text_message("done"),
            ]
        )
        info = await start_conversation_with_test_llm(
            conversation_service,
            parent_llm=parent_llm,
            workspace_dir=workspace,
            usage_id="task-dispatch-idle-heartbeat",
            tools=[Tool(name=TaskToolSet.name)],
            initial_text="please delegate",
        )

        sdr.update_last_execution_time()
        run_resp = await client.post(f"/api/conversations/{info.id.hex}/run")
        assert run_resp.status_code == 200, run_resp.text

        entered = await asyncio.to_thread(child_llm._entered.wait, 5.0)
        assert entered, "Task child's LLM call never started"

        await asyncio.sleep(budget.hold_sample_s)
        during_hold = (await client.get("/server_info")).json()["idle_time"]
    finally:
        child_llm._gate.set()

    assert during_hold < budget.idle_time_threshold_s, (
        f"idle_time={during_hold}s grew past the {budget.idle_time_threshold_s}s "
        f"budget while a real Task child was still held open after "
        f"{budget.hold_sample_s}s -- the run-level heartbeat did not refresh "
        f"/server_info's idle clock during the blocked dispatch."
    )

    status = await wait_for_terminal(client, info.id, timeout_s=10.0)
    assert status == ConversationExecutionStatus.FINISHED

    # The heartbeat must actually stop once the run is genuinely done, not
    # just refresh idle_time at the moment of completion: sleep the same
    # hold window again with nothing in flight and confirm idle_time climbs
    # back up. A leaked heartbeat task (still pulsing after the run ended)
    # would keep this low forever and this assertion would never fail.
    await asyncio.sleep(budget.hold_sample_s)
    after_completion = (await client.get("/server_info")).json()["idle_time"]
    assert after_completion >= budget.idle_time_threshold_s, (
        f"idle_time={after_completion}s stayed below the "
        f"{budget.idle_time_threshold_s}s budget {budget.hold_sample_s}s after "
        f"the run finished -- the run-level heartbeat task did not stop "
        f"(leaked) after genuine completion."
    )
