"""Deterministic live smoke test for background DelegateExecutor tasks."""

import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLMResponse
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
from openhands.sdk.subagent import register_agent
from openhands.tools.delegate import (
    DelegateAction,
    DelegateExecutor,
    DelegateTaskStatus,
)


def _response(text: str, model: str) -> LLMResponse:
    return LLMResponse(
        message=Message(
            role="assistant",
            content=[TextContent(text=text)],
        ),
        metrics=MetricsSnapshot(
            model_name=model,
            accumulated_cost=0.0,
            max_budget_per_task=0.0,
            accumulated_token_usage=TokenUsage(model=model),
        ),
        raw_response=MagicMock(spec=ModelResponse, id=f"{model}-response"),
    )


class CompletingLLM(LLM):
    def __init__(self) -> None:
        super().__init__(model="smoke-completing", usage_id="smoke-completing")

    def completion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args, kwargs
        return _response("background complete", self.model)

    async def acompletion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args, kwargs
        return _response("background complete", self.model)


class BlockingLLM(LLM):
    _started: threading.Event = PrivateAttr(default_factory=threading.Event)

    def __init__(self) -> None:
        super().__init__(model="smoke-blocking", usage_id="smoke-blocking")

    def completion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args, kwargs
        raise AssertionError("background worker must use arun()")

    async def acompletion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        del args, kwargs
        self._started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


blocking_llms: list[BlockingLLM] = []


def _complete_agent(_parent_llm: LLM) -> Agent:
    return Agent(llm=CompletingLLM(), tools=[])


def _blocking_agent(_parent_llm: LLM) -> Agent:
    llm = BlockingLLM()
    blocking_llms.append(llm)
    return Agent(llm=llm, tools=[])


register_agent(
    name="smoke-complete-2047",
    factory_func=_complete_agent,
    description="Deterministic completing agent for issue 2047 smoke evidence.",
)
register_agent(
    name="smoke-block-2047",
    factory_func=_blocking_agent,
    description="Deterministic cancellable agent for issue 2047 smoke evidence.",
)


tmp_root = Path(os.environ["TMP"])
with tempfile.TemporaryDirectory(dir=tmp_root) as workspace:
    parent = LocalConversation(
        agent=Agent(llm=CompletingLLM(), tools=[]),
        workspace=workspace,
        visualizer=None,
        persistence_dir=None,
    )
    executor = DelegateExecutor(max_children=2)

    spawned = executor(
        DelegateAction(
            command="spawn",
            ids=["complete", "blocked"],
            agent_types=["smoke-complete-2047", "smoke-block-2047"],
        ),
        parent,
    )
    assert not spawned.is_error, spawned.text

    launch_start = time.perf_counter()
    started = executor(
        DelegateAction(
            command="delegate",
            tasks={
                "complete": "Return the deterministic response.",
                "blocked": "Wait until the parent stops this task.",
            },
            background=True,
        ),
        parent,
    )
    launch_ms = (time.perf_counter() - launch_start) * 1000
    assert not started.is_error, started.text
    assert started.task_ids is not None
    complete_id = started.task_ids["complete"]
    blocked_id = started.task_ids["blocked"]

    assert blocking_llms
    assert blocking_llms[0]._started.wait(timeout=5)

    deadline = time.monotonic() + 5
    while True:
        complete_status = executor(
            DelegateAction(command="status", task_id=complete_id),
            parent,
        )
        if complete_status.status == DelegateTaskStatus.COMPLETED:
            break
        assert time.monotonic() < deadline, complete_status.text
        threading.Event().wait(0.01)

    complete_output = executor(
        DelegateAction(command="output", task_id=complete_id),
        parent,
    )
    repeated_output = executor(
        DelegateAction(command="output", task_id=complete_id),
        parent,
    )
    stopped = executor(
        DelegateAction(command="stop", task_id=blocked_id),
        parent,
    )

    assert complete_output.text == "background complete"
    assert repeated_output.text == complete_output.text
    assert stopped.status == DelegateTaskStatus.CANCELLED

    task_ids = dict(started.task_ids)
    executor.close()
    parent.close()

live_workers = [
    thread.name
    for thread in threading.enumerate()
    if thread.name.startswith("Delegate-") and thread.is_alive()
]
assert live_workers == []

print(
    json.dumps(
        {
            "launch_ms": round(launch_ms, 2),
            "task_ids": task_ids,
            "completed_status": complete_status.status,
            "completed_output": complete_output.text,
            "stopped_status": stopped.status,
            "live_delegate_workers": live_workers,
        },
        indent=2,
    )
)
