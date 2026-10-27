"""A pause request is distinct from a completed pause at a step boundary."""

import asyncio
from contextlib import closing

import pytest

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import PauseEvent


@pytest.mark.parametrize("fail_turn", [False, True])
async def test_acp_pause_waits_for_turn_and_preserves_completed_prompt(
    tmp_path, monkeypatch, fail_turn
):
    entered = asyncio.Event()
    release = asyncio.Event()
    prompts = []

    def init_state(self, state, on_event):
        pass

    async def astep(self, conversation, on_event, on_token=None, prompt_message=None):
        assert prompt_message is not None
        prompts.append(prompt_message.llm_message.content[0].text)
        entered.set()
        await release.wait()
        if fail_turn:
            raise RuntimeError("turn failed while pausing")
        with conversation._state:
            conversation._state.execution_status = ConversationExecutionStatus.FINISHED

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

        if fail_turn:
            assert (
                conversation.state.execution_status == ConversationExecutionStatus.ERROR
            )
            assert not any(isinstance(e, PauseEvent) for e in conversation.state.events)
            return

        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
        assert prompts == ["first"]
        assert sum(isinstance(e, PauseEvent) for e in conversation.state.events) == 1
        await asyncio.wait_for(conversation.arun(), timeout=5)
        assert prompts == ["first", "second"]
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
