"""The conversation state lock must not be held across the LLM network call.

Regression: ``arun()`` holds the conversation state lock across the whole agent
step. Holding it during the LLM round-trip blocked ``send_message()`` and state
snapshots for the full provider response time, so the agent-server appeared to
"stop responding" while waiting for the model. ``Agent.astep`` now releases the
lock for just the network call via
``LocalConversation._released_state_lock_during_io``.
"""

import asyncio
import threading

import pytest
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM
from openhands.sdk.llm.exceptions import LLMContentPolicyViolationError


class _LockProbingLLM(LLM):
    """Records the conversation-lock state observed mid-``acompletion``.

    Raises a *recoverable* content-policy error so ``astep`` returns cleanly
    without us having to synthesize a full ``LLMResponse``.
    """

    _convo_box: list = PrivateAttr(default_factory=list)
    _probe: dict = PrivateAttr(default_factory=dict)

    def __init__(self):
        super().__init__(model="test-model", usage_id="test-llm")

    def uses_responses_api(self) -> bool:  # override gating
        return False

    async def acompletion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
        convo = self._convo_box[0]
        lock = convo._state._lock
        # 1) No thread should own the state lock during the network call.
        self._probe["locked_during_call"] = lock.locked()
        # 2) The run-loop flag must reflect reality (lock is free right now).
        self._probe["step_flag_during_call"] = convo._step_holds_state_lock

        # 3) A different thread (as send_message runs, via run_in_executor)
        #    must be able to actually grab the lock while the call is in flight.
        acquired: dict = {}

        def _try_acquire():
            got = lock.acquire(blocking=True, timeout=2.0)
            acquired["ok"] = got
            if got:
                lock.release()

        t = threading.Thread(target=_try_acquire)
        t.start()
        await asyncio.to_thread(t.join)
        self._probe["worker_acquired_during_call"] = acquired.get("ok", False)

        raise LLMContentPolicyViolationError()


@pytest.mark.asyncio
async def test_astep_releases_state_lock_during_llm_call():
    llm = _LockProbingLLM()
    agent = Agent(llm=llm, tools=[])
    convo = Conversation(agent=agent)
    assert isinstance(convo, LocalConversation)
    convo._ensure_agent_ready()
    llm._convo_box.append(convo)
    convo.send_message("hello")

    # Mimic arun()'s per-step critical section: hold the state lock across the
    # step and set the flag arun sets before calling astep.
    convo._step_holds_state_lock = True
    with convo._state:
        assert convo._state._lock.locked()
        await agent.astep(convo, on_event=[].append)
        # The context manager restored the lock (same thread) and the flag.
        assert convo._state._lock.owned()
        assert convo._step_holds_state_lock is True

    # The lock was NOT held during the provider round-trip...
    assert llm._probe["locked_during_call"] is False
    # ...another thread could take it mid-call...
    assert llm._probe["worker_acquired_during_call"] is True
    # ...and the run-loop flag told the truth while it was released.
    assert llm._probe["step_flag_during_call"] is False


@pytest.mark.asyncio
async def test_direct_astep_without_lock_is_unaffected():
    """A direct astep() (no run loop, lock not held) must still work: the
    context manager is a no-op when the current thread doesn't own the lock."""
    llm = _LockProbingLLM()
    agent = Agent(llm=llm, tools=[])
    convo = Conversation(agent=agent)
    assert isinstance(convo, LocalConversation)
    convo._ensure_agent_ready()
    llm._convo_box.append(convo)
    convo.send_message("hello")

    # No `with convo._state:` here — the lock is not held on entry.
    await agent.astep(convo, on_event=[].append)

    assert llm._probe["locked_during_call"] is False
    assert llm._probe["worker_acquired_during_call"] is True
