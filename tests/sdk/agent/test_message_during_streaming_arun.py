"""A user message sent while the agent streams its final response must be seen.

Regression for agent-canvas#1900: ``astep`` releases the state lock for the LLM
call, so a message can land mid-step with status still RUNNING; ``arun()`` used
to break on FINISHED without rescanning, stranding it. Sync ``run()`` holds the
lock across the step and never had the gap, and is used here as a control.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


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
