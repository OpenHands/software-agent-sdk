"""Completed tool results survive cancellation, persistence and resume."""

import asyncio
import json
import threading
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar, Literal, Self

import pytest
from pydantic import PrivateAttr

from openhands.sdk import Action, Agent, Observation, Tool, ToolDefinition
from openhands.sdk.agent import parallel_executor
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import AgentErrorEvent, ObservationEvent
from openhands.sdk.llm import (
    LLMCallContext,
    LLMResponse,
    Message,
    MessageToolCall,
    TextContent,
)
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolExecutor, register_tool
from openhands.sdk.tool.tool import DeclaredResources


class ReceiptAction(Action):
    operation: Literal["write", "wait"]
    path: str


class ReceiptObservation(Observation):
    pass


class ReceiptExecutor(ToolExecutor[ReceiptAction, ReceiptObservation]):
    def __init__(self) -> None:
        self.cooperative = True
        self.wait_started = threading.Event()
        self.release = threading.Event()
        self.wait_finished = threading.Event()

    def __call__(self, action: ReceiptAction, conversation=None) -> ReceiptObservation:
        if action.operation == "write":
            Path(action.path).write_text("receipt committed")
            return ReceiptObservation.from_text("Receipt committed successfully")
        self.wait_started.set()
        if not self.release.wait(5):
            raise TimeoutError("test did not release the waiting tool")
        self.wait_finished.set()
        return ReceiptObservation.from_text("Check completed")

    def interrupt(self) -> None:
        if self.cooperative:
            self.release.set()


class ReceiptTool(ToolDefinition[ReceiptAction, ReceiptObservation]):
    executors: ClassVar[dict[str, ReceiptExecutor]] = {}

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[Self]:
        executor = ReceiptExecutor()
        cls.executors[str(conv_state.id)] = executor
        return [
            cls(
                description="Write a receipt or wait for a check.",
                action_type=ReceiptAction,
                observation_type=ReceiptObservation,
                executor=executor,
            )
        ]

    def declared_resources(self, action: Action) -> DeclaredResources:
        return DeclaredResources(keys=(), declared=True)


class RecordingLLM(TestLLM):
    _inputs: list[list[Message]] = PrivateAttr(default_factory=list)

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._inputs.append(messages)
        return super().completion(
            messages,
            tools,
            add_security_risk_prediction,
            on_token,
            call_context,
            **kwargs,
        )

    @property
    def inputs(self) -> list[list[Message]]:
        return self._inputs


@pytest.fixture
def completed_pool_calls(monkeypatch):
    """Observe actual worker completion without timing guesses or fake events."""
    calls: list[Future] = []

    class RecordingPool(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            calls.append(future)
            return future

    monkeypatch.setattr(parallel_executor, "ThreadPoolExecutor", RecordingPool)
    return calls


@pytest.mark.parametrize(
    "workers, order, interrupt, cooperative",
    [
        (1, ("write", "wait"), False, True),
        (1, ("write", "wait"), True, True),
        (2, ("wait", "write"), False, True),
        (2, ("wait", "write"), True, True),
        (1, ("wait", "write"), True, True),
        (2, ("wait", "write"), True, False),
    ],
    ids=[
        "serial-control",
        "serial-interrupt",
        "parallel-control",
        "parallel-interrupt-out-of-order",
        "interrupt-before-write",
        "parallel-late-result",
    ],
)
async def test_completed_results_survive_interrupt_and_resume(
    tmp_path,
    completed_pool_calls,
    workers,
    order,
    interrupt,
    cooperative,
):
    register_tool(ReceiptTool.name, ReceiptTool)
    receipt = tmp_path / "receipt.txt"
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Run both tools.")],
                tool_calls=[
                    MessageToolCall(
                        id=op,
                        name=ReceiptTool.name,
                        arguments=json.dumps({"operation": op, "path": str(receipt)}),
                        origin="completion",
                    )
                    for op in order
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done.")]),
        ]
    )
    conversation = LocalConversation(
        agent=Agent(
            llm=llm, tools=[Tool(name=ReceiptTool.name)], tool_concurrency_limit=workers
        ),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "state"),
        visualizer=None,
        delete_on_close=False,
    )
    resumed = None
    run = None
    conversation.send_message("Write a receipt and check it.")
    executor = ReceiptTool.executors[str(conversation.state.id)]
    executor.cooperative = cooperative
    try:
        run = asyncio.create_task(conversation.arun())
        assert await asyncio.to_thread(executor.wait_started.wait, 5)
        write_completed = workers > 1 or order[0] == "write"
        if workers > 1:
            # The second submitted call is the write. Await its real thread
            # future, not a flag set inside the tool before it returns.
            await asyncio.wait_for(asyncio.wrap_future(completed_pool_calls[1]), 5)
        if write_completed:
            assert receipt.read_text() == "receipt committed"
        else:
            assert not receipt.exists()
        if interrupt:
            conversation.interrupt()
            conversation.interrupt()
        else:
            executor.release.set()
        await asyncio.wait_for(run, 1)
        if not cooperative:
            assert not executor.wait_finished.is_set()
            executor.release.set()
        assert await asyncio.to_thread(executor.wait_finished.wait, 5)
        conversation_id = conversation.state.id
        conversation.close()
        resumed_llm = RecordingLLM.from_messages(
            [
                Message(role="assistant", content=[TextContent(text="Continue.")]),
            ]
        )
        assert isinstance(resumed_llm, RecordingLLM)
        resumed = LocalConversation(
            agent=Agent(
                llm=resumed_llm,
                tools=[Tool(name=ReceiptTool.name)],
                tool_concurrency_limit=workers,
            ),
            workspace=str(tmp_path),
            persistence_dir=str(tmp_path / "state"),
            conversation_id=conversation_id,
            visualizer=None,
            delete_on_close=False,
        )
        results = [
            e
            for e in resumed.state.events
            if isinstance(e, (ObservationEvent, AgentErrorEvent))
        ]
        assert sorted(e.tool_call_id for e in results) == ["wait", "write"]
        write_result = next(e for e in results if e.tool_call_id == "write")
        if write_completed:
            assert isinstance(write_result, ObservationEvent)
            assert list(write_result.observation.to_llm_content) == [
                TextContent(text="Receipt committed successfully")
            ]
        else:
            assert isinstance(write_result, AgentErrorEvent)
            assert not receipt.exists()
        if not interrupt:
            assert [e.tool_call_id for e in results] == list(order)
            assert all(isinstance(e, ObservationEvent) for e in results)
        resumed.send_message("Continue using the previous tool outcomes.")
        await resumed.arun()
        tool_inputs = [m for m in resumed_llm.inputs[0] if m.role == "tool"]
        assert len(tool_inputs) == 2
        assert {m.tool_call_id for m in tool_inputs} == {"wait", "write"}
        if write_completed:
            actual = next(m for m in tool_inputs if m.tool_call_id == "write")
            assert actual.content == [
                TextContent(text="Receipt committed successfully")
            ]
    finally:
        executor.release.set()
        if run is not None:
            await run
        if resumed is not None:
            resumed.close()
        conversation.close()
        ReceiptTool.executors.clear()
