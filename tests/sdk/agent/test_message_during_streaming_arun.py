"""A user message sent while the agent streams its final response must be seen.

Regression for agent-canvas#1900: ``astep`` releases the state lock for the LLM
call, so a message can land mid-step with status still RUNNING; ``arun()`` used
to break on FINISHED without rescanning, stranding it. Sync ``run()`` holds the
lock across the step and never had the gap, and is used here as a control.
"""

import asyncio
import threading
import time
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.llm_convertible import UserRejectObservation
from openhands.sdk.llm import LLM, LLMResponse, Message, MessageToolCall, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import Tool, ToolDefinition, ToolExecutor, register_tool
from openhands.sdk.tool.schema import Action, Observation


MODEL = "test-model"


def _finishing_response(text: str = "done") -> LLMResponse:
    """A plain text response with NO tool calls -> agent goes FINISHED."""
    return LLMResponse(
        message=Message(role="assistant", content=[TextContent(text=text)]),
        metrics=MetricsSnapshot(
            model_name=MODEL,
            accumulated_cost=0.0,
            max_budget_per_task=0.0,
            accumulated_token_usage=TokenUsage(model=MODEL),
        ),
        raw_response=MagicMock(spec=ModelResponse, id="resp-1"),
    )


def _make_conversation(llm: LLM, tmp_path) -> LocalConversation:
    return LocalConversation(
        agent=Agent(llm=llm, tools=[]), workspace=str(tmp_path), visualizer=None
    )


def _saw(calls: list[str], marker: str) -> list[int]:
    """Indices of LLM calls whose prompt contained `marker`."""
    return [i for i, prompt in enumerate(calls) if marker in prompt]


class _InjectingAsyncLLM(LLM):
    """Sends a user message from another thread during the (async) LLM call."""

    _convo_box: list = PrivateAttr(default_factory=list)
    _calls: list = PrivateAttr(default_factory=list)
    _probe: dict = PrivateAttr(default_factory=dict)

    def __init__(self):
        super().__init__(model=MODEL, usage_id="test-llm")

    def uses_responses_api(self) -> bool:  # keep amake_llm_completion on acompletion
        return False

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        self._calls.append(" ".join(str(m) for m in messages))
        convo: LocalConversation = self._convo_box[0]

        if len(self._calls) == 1:
            lock = convo._state._lock
            self._probe["locked_during_call"] = lock.locked()
            self._probe["status_during_call"] = convo._state.execution_status

            # A message lands mid-stream from another thread (mimics run_in_executor).
            def _send():
                convo.send_message("second message")

            t = threading.Thread(target=_send)
            t.start()
            await asyncio.to_thread(t.join)

            self._probe["status_after_inject"] = convo._state.execution_status

        return _finishing_response()


class _InjectingSyncLLM(LLM):
    """Control: injects during the *sync* step, where the lock IS held."""

    _convo_box: list = PrivateAttr(default_factory=list)
    _calls: list = PrivateAttr(default_factory=list)
    _probe: dict = PrivateAttr(default_factory=dict)

    def __init__(self):
        super().__init__(model=MODEL, usage_id="test-llm-sync")

    def uses_responses_api(self) -> bool:
        return False

    def completion(self, messages, tools=None, **kwargs):  # type: ignore[override]
        self._calls.append(" ".join(str(m) for m in messages))
        convo: LocalConversation = self._convo_box[0]

        if len(self._calls) == 1:
            lock = convo._state._lock
            # The sync run loop holds the state lock across the whole step.
            self._probe["locked_during_call"] = lock.locked()

            threading.Thread(
                target=lambda: convo.send_message("second message")
            ).start()

            # Wait until the sender is queued on the FIFO lock, so the run loop
            # provably can't overtake it — deterministic, no sleep-and-hope.
            deadline = time.monotonic() + 5.0
            while not lock._waiters:
                assert time.monotonic() < deadline, "send_message never queued"
                time.sleep(0.001)
            self._probe["sender_enqueued"] = True

        return _finishing_response()


@pytest.mark.asyncio
async def test_message_during_streaming_is_acted_upon(tmp_path):
    """arun() rescans for a message that arrived while the lock was released."""
    llm = _InjectingAsyncLLM()
    convo = _make_conversation(llm, tmp_path)
    llm._convo_box.append(convo)
    convo.send_message("first message")

    await convo.arun()

    # The race happened: lock free mid-call, status still RUNNING.
    assert llm._probe["locked_during_call"] is False
    assert llm._probe["status_during_call"] == ConversationExecutionStatus.RUNNING
    assert llm._probe["status_after_inject"] == ConversationExecutionStatus.RUNNING

    user_texts = [
        str(e.llm_message)
        for e in convo.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    ]
    assert any("second message" in t for t in user_texts)

    # The agent acted on it: a second round-trip ran and carried it to the model.
    assert len(llm._calls) == 2, (
        f"expected the message to be picked up (2 LLM calls), got {len(llm._calls)}"
    )
    assert _saw(llm._calls, "second message") == [1]
    assert _saw(llm._calls, "first message") == [0, 1]
    assert convo.state.execution_status == ConversationExecutionStatus.FINISHED


def test_sync_run_absorbs_message_sent_during_step(tmp_path):
    """CONTROL: the sync run() path does NOT drop the message."""
    llm = _InjectingSyncLLM()
    convo = _make_conversation(llm, tmp_path)
    llm._convo_box.append(convo)
    convo.send_message("first message")

    convo.run()

    # Lock held across the sync step, so send_message() had to wait.
    assert llm._probe["locked_during_call"] is True
    assert llm._probe["sender_enqueued"] is True

    # It observed FINISHED, rewound to IDLE, and ran again with the message.
    assert len(llm._calls) == 2, (
        f"sync path should absorb the message (2 LLM calls), got {len(llm._calls)}"
    )
    assert _saw(llm._calls, "second message") == [1]
    assert convo.state.execution_status == ConversationExecutionStatus.FINISHED


class _ConfirmAction(Action):
    command: str


class _ConfirmObservation(Observation):
    result: str

    @property
    def to_llm_content(self):
        return [TextContent(text=self.result)]


class _ConfirmExecutor(ToolExecutor[_ConfirmAction, _ConfirmObservation]):
    def __call__(self, action: _ConfirmAction, conversation=None):
        return _ConfirmObservation(result=f"ran {action.command}")


class _ConfirmTool(ToolDefinition[_ConfirmAction, _ConfirmObservation]):
    name: ClassVar[str] = "async_confirm_tool"

    @classmethod
    def create(cls, conv_state=None, **params):
        return [
            cls(
                description="Tool requiring confirmation",
                action_type=_ConfirmAction,
                observation_type=_ConfirmObservation,
                executor=_ConfirmExecutor(),
            )
        ]


register_tool("async_confirm_tool", _ConfirmTool)


def _tool_call_response(command: str = "run-once") -> LLMResponse:
    """A tool-call response -> agent goes WAITING_FOR_CONFIRMATION."""
    return LLMResponse(
        message=Message(
            role="assistant",
            content=[TextContent(text=f"I'll {command}")],
            tool_calls=[
                MessageToolCall(
                    id="call_1",
                    name="async_confirm_tool",
                    arguments=f'{{"command": "{command}"}}',
                    origin="completion",
                )
            ],
        ),
        metrics=MetricsSnapshot(
            model_name=MODEL,
            accumulated_cost=0.0,
            max_budget_per_task=0.0,
            accumulated_token_usage=TokenUsage(model=MODEL),
        ),
        raw_response=MagicMock(spec=ModelResponse, id="resp-confirm"),
    )


class _InjectingAsyncConfirmLLM(LLM):
    """First call proposes an action needing confirmation while a message
    lands mid-call; second call (after the pending action is superseded)
    finishes normally."""

    _convo_box: list = PrivateAttr(default_factory=list)
    _calls: list = PrivateAttr(default_factory=list)

    def __init__(self):
        super().__init__(model=MODEL, usage_id="test-llm-confirm")

    def uses_responses_api(self) -> bool:
        return False

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        self._calls.append(" ".join(str(m) for m in messages))
        convo: LocalConversation = self._convo_box[0]

        if len(self._calls) == 1:

            def _send():
                convo.send_message("second message")

            t = threading.Thread(target=_send)
            t.start()
            await asyncio.to_thread(t.join)
            return _tool_call_response()

        return _finishing_response()


@pytest.mark.asyncio
async def test_message_during_confirmation_wait_supersedes_pending_action(tmp_path):
    """A message landing while astep() ends WAITING_FOR_CONFIRMATION must reject
    the stale pending action instead of letting the run loop silently execute it
    as an implicit confirmation on the next iteration."""
    llm = _InjectingAsyncConfirmLLM()
    convo = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="async_confirm_tool")]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    llm._convo_box.append(convo)
    convo.set_confirmation_policy(AlwaysConfirm())
    convo.send_message("first message")

    await convo.arun()

    assert len(llm._calls) == 2, (
        f"expected the new message to be picked up (2 LLM calls), got {len(llm._calls)}"
    )
    assert _saw(llm._calls, "second message") == [1]
    assert convo.state.execution_status == ConversationExecutionStatus.FINISHED

    reject_events = [
        e for e in convo.state.events if isinstance(e, UserRejectObservation)
    ]
    assert len(reject_events) == 1, "the stale pending action must be rejected"


@pytest.mark.asyncio
async def test_message_during_confirmation_wait_on_final_iteration_still_rejects(
    tmp_path,
):
    """Same race as above, but the step that lands in WAITING_FOR_CONFIRMATION
    is also the run's final iteration. The loop must still reject the pending
    action before going IDLE -- otherwise astep() executes it as an implicit
    confirmation on the very next run() call, the same bug the FINISHED/
    WAITING_FOR_CONFIRMATION rescan exists to prevent."""
    llm = _InjectingAsyncConfirmLLM()
    convo = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="async_confirm_tool")]),
        workspace=str(tmp_path),
        visualizer=None,
        max_iteration_per_run=1,
    )
    llm._convo_box.append(convo)
    convo.set_confirmation_policy(AlwaysConfirm())
    convo.send_message("first message")

    await convo.arun()

    assert len(llm._calls) == 1, (
        f"final iteration must not auto-continue, got {len(llm._calls)} calls"
    )
    assert convo.state.execution_status == ConversationExecutionStatus.IDLE

    reject_events = [
        e for e in convo.state.events if isinstance(e, UserRejectObservation)
    ]
    assert len(reject_events) == 1, (
        "the pending action must be rejected even when going IDLE on the "
        "final iteration, or the next run() call will silently execute it"
    )


class _InjectingAsyncBudgetLLM(LLM):
    """Finishing response with a message landing mid-call, while the run's
    accumulated cost is already over budget."""

    _convo_box: list = PrivateAttr(default_factory=list)
    _calls: list = PrivateAttr(default_factory=list)

    def __init__(self):
        super().__init__(model=MODEL, usage_id="test-llm-budget")

    def uses_responses_api(self) -> bool:
        return False

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        self._calls.append(" ".join(str(m) for m in messages))
        convo: LocalConversation = self._convo_box[0]

        if len(self._calls) == 1:

            def _send():
                convo.send_message("second message")

            t = threading.Thread(target=_send)
            t.start()
            await asyncio.to_thread(t.join)

        return _finishing_response()


@pytest.mark.asyncio
async def test_message_during_final_step_does_not_trigger_spurious_budget_error(
    tmp_path,
):
    """A message arriving during a step that finishes within its own budget must
    not turn into a MaxBudgetReached error just because the mid-step rescan
    flips status back to RUNNING in order to pick up the message."""
    llm = _InjectingAsyncBudgetLLM()
    convo = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(tmp_path),
        visualizer=None,
        max_budget_per_run=0.01,
    )
    llm._convo_box.append(convo)
    # Test-double LLMs are subclasses, and get_all_llms() only ever yields
    # objects whose type is exactly LLM, so this one is never registered by
    # the normal _ensure_agent_ready() path; wire it in directly so the
    # budget check has metrics to read.
    convo.conversation_stats.usage_to_metrics[llm.usage_id] = llm.metrics
    # Simulate accumulated spend already over budget by the time this
    # (otherwise graceful) finishing step lands.
    llm.metrics.add_cost(1.0)
    convo.send_message("first message")

    await convo.arun()

    assert len(llm._calls) == 2, (
        f"expected the new message to be picked up (2 LLM calls), got {len(llm._calls)}"
    )
    assert convo.state.execution_status == ConversationExecutionStatus.FINISHED
    assert not any(
        isinstance(e, ConversationErrorEvent) and e.code == "MaxBudgetReached"
        for e in convo.state.events
    ), "a graceful finish must not be overridden by the budget check"


class _NavigateAwayDuringStepLLM(LLM):
    """Rebases the active branch mid-call, via navigate_to(), without sending
    any new message -- proves the rescan isn't fooled by the rebase alone."""

    _convo_box: list = PrivateAttr(default_factory=list)
    _calls: list = PrivateAttr(default_factory=list)
    _rebase_to: list = PrivateAttr(default_factory=list)

    def __init__(self):
        super().__init__(model=MODEL, usage_id="test-llm-navigate")

    def uses_responses_api(self) -> bool:
        return False

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        self._calls.append(" ".join(str(m) for m in messages))
        convo: LocalConversation = self._convo_box[0]

        if len(self._calls) == 1:
            rebase_to = self._rebase_to[0]

            def _navigate():
                convo.navigate_to(rebase_to)

            t = threading.Thread(target=_navigate)
            t.start()
            await asyncio.to_thread(t.join)

        return _finishing_response()


@pytest.mark.asyncio
async def test_navigate_to_during_step_does_not_spuriously_continue(tmp_path):
    """A concurrent navigate_to() rebase mid-step must not be mistaken for a
    new user message. Rebasing the active branch changes which user message
    is "latest" on that branch even though nothing new was ever sent, which
    is exactly what the old active_branch()-scanning check couldn't tell
    apart from a real new message."""
    llm = _NavigateAwayDuringStepLLM()
    convo = _make_conversation(llm, tmp_path)
    llm._convo_box.append(convo)

    convo.send_message("first message")
    first_message_id = next(
        e.id
        for e in convo.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    )
    convo.send_message("second message")

    # Mid-step, rebase HEAD back to the first message. This drops the second
    # message from the active branch without sending anything new.
    llm._rebase_to.append(first_message_id)

    await convo.arun()

    assert len(llm._calls) == 1, (
        f"a navigate_to() rebase alone must not trigger the mid-step rescan, "
        f"got {len(llm._calls)} calls"
    )
    assert convo.state.execution_status == ConversationExecutionStatus.FINISHED
