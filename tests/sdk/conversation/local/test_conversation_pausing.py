"""A pause request is distinct from a completed pause at a step boundary."""

import asyncio
from contextlib import closing

import pytest

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.agent import Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import PauseEvent, UserRejectObservation
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool, register_tool
from tests.sdk.conversation.local.test_confirmation_mode import ConfirmationTestTool


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


@pytest.mark.parametrize("superseding_message", [False, True])
async def test_pause_during_llm_preserves_confirmation_handling(
    tmp_path, monkeypatch, superseding_message
):
    entered, release = asyncio.Event(), asyncio.Event()
    original_completion = TestLLM.acompletion

    async def gated_completion(self, *args, **kwargs):
        entered.set()
        await release.wait()
        return await original_completion(self, *args, **kwargs)

    monkeypatch.setattr(TestLLM, "acompletion", gated_completion)
    register_tool("test_tool", ConfirmationTestTool)
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[],
                tool_calls=[
                    MessageToolCall(
                        id="needs-confirmation",
                        name="test_tool",
                        arguments='{"command": "needs approval"}',
                        origin="completion",
                    )
                ],
            ),
            Message(role="assistant", content=[TextContent(text="done")]),
        ]
    )
    with closing(
        LocalConversation(
            agent=Agent(llm=llm, tools=[Tool(name="test_tool")]),
            workspace=str(tmp_path),
            visualizer=None,
            stuck_detection=False,
        )
    ) as conversation:
        conversation.set_confirmation_policy(AlwaysConfirm())
        conversation.send_message("first")
        task = asyncio.create_task(conversation.arun())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.to_thread(conversation.pause)
            if superseding_message:
                await asyncio.to_thread(conversation.send_message, "do something else")
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=5)

        pending = ConversationState.get_unmatched_actions(conversation.state.events)
        if superseding_message:
            assert not pending
            assert any(
                isinstance(event, UserRejectObservation)
                for event in conversation.state.events
            )
            assert (
                conversation.state.execution_status
                == ConversationExecutionStatus.PAUSED
            )
        else:
            assert len(pending) == 1
            assert (
                conversation.state.execution_status
                == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            )
            assert not any(
                isinstance(event, PauseEvent) for event in conversation.state.events
            )
            conversation.reject_pending_actions("not approved")

        await asyncio.wait_for(conversation.arun(), timeout=5)
        assert (
            conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        )
