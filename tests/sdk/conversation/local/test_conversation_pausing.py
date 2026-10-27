"""A pause request is distinct from a completed pause at a step boundary."""

import asyncio
from contextlib import closing

import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import PauseEvent


@pytest.mark.parametrize(
    ("fail_turn", "queued_message"),
    [(False, True), (False, False), (True, True)],
)
async def test_acp_pause_waits_for_turn_and_preserves_completed_prompt(
    tmp_path, monkeypatch, fail_turn, queued_message
):
    entered = asyncio.Event()
    release = asyncio.Event()
    prompts = []

    def init_state(self, state, on_event):
        pass

    async def astep(self, conversation, on_event, on_token=None, prompt_message=None):
        assert prompt_message is not None
        self._last_prompt_completed = False
        prompts.append(prompt_message.llm_message.content[0].text)
        entered.set()
        await release.wait()
        if fail_turn:
            raise RuntimeError("turn failed while pausing")
        # Mirror ACPAgent._finalize_successful_turn: a completed prompt keeps
        # PAUSING so the loop decides the outcome at the step boundary.
        with conversation._state:
            self._last_prompt_completed = True
            if (
                conversation._state.execution_status
                != ConversationExecutionStatus.PAUSING
            ):
                conversation._state.execution_status = (
                    ConversationExecutionStatus.FINISHED
                )

    monkeypatch.setattr(ACPAgent, "init_state", init_state)
    monkeypatch.setattr(ACPAgent, "astep", astep)
    with closing(
        LocalConversation(
            agent=ACPAgent(acp_command=["unused"]),
            workspace=str(tmp_path),
            stuck_detection=False,
            visualizer=None,
        )
    ) as conversation:
        conversation.send_message("first")
        if queued_message:
            conversation.send_message("second")
        task = asyncio.create_task(conversation.arun())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.to_thread(conversation.pause)
            await asyncio.to_thread(conversation.pause)
            assert conversation.state.execution_status.value == "pausing"
            assert not any(isinstance(e, PauseEvent) for e in conversation.state.events)
            assert not task.done()
        finally:
            release.set()
            if fail_turn:
                with pytest.raises(ConversationRunError, match="turn failed"):
                    await asyncio.wait_for(task, timeout=5)
            else:
                await asyncio.wait_for(task, timeout=5)

        pause_events = [
            e for e in conversation.state.events if isinstance(e, PauseEvent)
        ]
        if fail_turn:
            assert (
                conversation.state.execution_status == ConversationExecutionStatus.ERROR
            )
            assert pause_events == []
            return

        assert prompts == ["first"]
        if not queued_message:
            # The prompt completed and nothing is queued: the pause has nothing
            # left to stop, so the turn is reported as finished, not paused.
            assert (
                conversation.state.execution_status
                == ConversationExecutionStatus.FINISHED
            )
            assert pause_events == []
            await asyncio.wait_for(conversation.arun(), timeout=5)
            assert prompts == ["first"]
            return

        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
        assert len(pause_events) == 1
        await asyncio.wait_for(conversation.arun(), timeout=5)
        assert prompts == ["first", "second"]
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )


def test_pause_without_run_loop_completes_immediately(tmp_path):
    """No run loop will reach a step boundary, so PAUSING would never resolve
    (and would be reported as ``running`` on the wire). Pause lands at once,
    as it did before PAUSING existed."""
    with closing(
        LocalConversation(
            agent=Agent(llm=LLM(model="test-model", usage_id="test-llm"), tools=[]),
            workspace=str(tmp_path),
            visualizer=None,
        )
    ) as conversation:
        with conversation._state:
            conversation._state.execution_status = ConversationExecutionStatus.RUNNING

        conversation.pause()

        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
        assert sum(isinstance(e, PauseEvent) for e in conversation.state.events) == 1

        # Idempotent: a second request neither re-emits nor changes state.
        conversation.pause()
        assert sum(isinstance(e, PauseEvent) for e in conversation.state.events) == 1


def test_run_recovers_from_stale_pausing_state(tmp_path):
    """A conversation persisted mid-pause reloads as PAUSING with no request to
    consume. run() must treat it like any other resumable state."""
    statuses_during_step = []
    box: list[LocalConversation] = []

    class _LLM(LLM):
        def uses_responses_api(self) -> bool:
            return False

        def completion(self, messages, tools=None, **kwargs):  # type: ignore[override]
            statuses_during_step.append(box[0].state.execution_status)
            raise RuntimeError("stop here")

    with closing(
        LocalConversation(
            agent=Agent(llm=_LLM(model="test-model", usage_id="test-llm"), tools=[]),
            workspace=str(tmp_path),
            visualizer=None,
        )
    ) as conversation:
        box.append(conversation)
        conversation.send_message("hello")
        with conversation._state:
            conversation._state.execution_status = ConversationExecutionStatus.PAUSING

        with pytest.raises(ConversationRunError, match="stop here"):
            conversation.run()

        assert statuses_during_step == [ConversationExecutionStatus.RUNNING]
        assert conversation.state.execution_status == ConversationExecutionStatus.ERROR
