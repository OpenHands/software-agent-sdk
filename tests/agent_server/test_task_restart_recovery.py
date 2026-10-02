"""Real process death and native recovery of concurrent TaskTool children.

Run the credential-free demo from a development checkout:
    uv run --frozen --all-packages python \
        tests/agent_server/test_task_restart_recovery.py demo --evidence /tmp/tasks
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import PrivateAttr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent, Tool
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.subagent.registry import _reset_registry_for_tests, register_agent
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.task import TaskToolSet
from openhands.tools.task.definition import TaskObservation
from openhands.tools.task.recovery import TaskStore


class BlockingTaskLLM(TestLLM):
    """Expose entry into the worker LLM, then wait for process termination."""

    _marker: str = PrivateAttr(default="")

    def __init__(self, *, marker: str = "", **data: Any) -> None:
        super().__init__(**data)
        self._marker = marker

    def completion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        Path(self._marker).write_text(str(os.getpid()))
        while True:
            time.sleep(1)


def _text(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def _register_worker(markers: Path) -> None:
    markers.mkdir(parents=True, exist_ok=True)
    _reset_registry_for_tests()
    register_agent(
        name="restart-worker",
        factory_func=lambda llm: Agent(
            llm=BlockingTaskLLM(marker=str(markers / uuid4().hex), model="test-model"),
            tools=[],
        ),
        description="Deterministic restart worker",
    )


def _task_message() -> Message:
    return Message(
        role="assistant",
        content=[],
        tool_calls=[
            MessageToolCall(
                id=f"task-call-{index}",
                name="task",
                origin="completion",
                arguments=json.dumps(
                    {"prompt": "Wait", "subagent_type": "restart-worker"}
                ),
            )
            for index in range(2)
        ],
    )


async def _phase_one(evidence: Path) -> None:
    _register_worker(evidence / "active-workers")
    workspace = evidence / "workspace"
    workspace.mkdir(exist_ok=True)
    service = ConversationService(conversations_dir=evidence / "conversations")
    await service.__aenter__()
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(
                llm=LLM(model="openai/gpt-4o"),
                tools=[Tool(name=TaskToolSet.name)],
                tool_concurrency_limit=2,
            ),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            autotitle=False,
        )
    )
    event_service = await service.get_event_service(info.id)
    assert event_service is not None
    conversation = event_service.get_conversation()
    conversation.switch_llm(TestLLM.from_messages([_task_message()]))
    await event_service.send_message(
        Message(role="user", content=[TextContent(text="Delegate two tasks")]),
        run=False,
    )
    await event_service.run()
    while len(list((evidence / "active-workers").iterdir())) < 2:
        if event_service._run_task is not None and event_service._run_task.done():
            raise RuntimeError("Parent run ended before both workers started")
        await asyncio.sleep(0.02)
    (evidence / "ready.json").write_text(
        json.dumps({"parent_id": str(info.id), "pid": os.getpid()})
    )
    # The controller kills this exact process without graceful cleanup.
    await asyncio.Event().wait()


def _task_results(events) -> list[ObservationEvent]:
    return [
        event
        for event in events
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, TaskObservation)
    ]


async def _phase_two(evidence: Path) -> None:
    _register_worker(evidence / "unexpected-replays")
    parent_id = UUID(json.loads((evidence / "ready.json").read_text())["parent_id"])
    parent_dir = evidence / "conversations" / parent_id.hex
    async with ConversationService(
        conversations_dir=evidence / "conversations"
    ) as service:
        restored = await service.get_event_service(parent_id)
        assert restored is not None
        conversation = restored.get_conversation()
        before = _task_results(conversation.state.events)
        assert len(before) == 2
        assert {event.tool_call_id for event in before} == {
            "task-call-0",
            "task-call-1",
        }
        assert all(event.observation.is_error for event in before)
        conversation.switch_llm(TestLLM.from_messages([_text("Recovered natively")]))
        await restored.run()
        assert restored._run_task is not None
        await asyncio.wait_for(asyncio.shield(restored._run_task), timeout=30)
        assert conversation.state.execution_status.value == "finished"
        assert len(_task_results(conversation.state.active_branch())) == 2
        records = TaskStore(
            LocalFileStore(str(parent_dir / "subagents")),
            parent_conversation_id=parent_id,
            write_guard=nullcontext,
        ).load_all()
        assert len(records) == 2
        assert all(record.status == "error" for record in records)
        for record in records:
            child_dir = parent_dir / "subagents" / record.conversation_id.hex
            assert (
                json.loads((child_dir / "base_state.json").read_text())[
                    "execution_status"
                ]
                == "error"
            )
            assert any(
                isinstance(event, ConversationErrorEvent)
                and event.code == "task_interrupted"
                for event in EventLog(LocalFileStore(str(child_dir)))
            )
    # A second native hydration must not publish the same result twice.
    async with ConversationService(
        conversations_dir=evidence / "conversations"
    ) as service:
        restored = await service.get_event_service(parent_id)
        assert restored is not None
        assert len(_task_results(restored.get_conversation().state.events)) == 2
    assert not list((evidence / "unexpected-replays").iterdir())
    (evidence / "result.json").write_text(
        json.dumps(
            {
                "parent_status": "finished",
                "children_status": [record.status for record in records],
                "task_ids": [record.task_id for record in records],
                "parent_task_results": 2,
                "replayed_workers": 0,
                "resume_calls": 1,
                "recovery_pid": os.getpid(),
            },
            indent=2,
        )
    )


def run_restart_demo(evidence: Path, *, lagging_head: bool = False) -> dict:
    evidence.mkdir(parents=True, exist_ok=True)
    if any(evidence.iterdir()):
        raise ValueError("Evidence directory must be empty")
    script = str(Path(__file__).resolve())
    with (evidence / "before.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, script, "phase1", "--evidence", str(evidence)],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while not (evidence / "ready.json").exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Workers did not start; see {evidence / 'before.log'}"
                    )
                time.sleep(0.05)
            process.kill()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    ready = json.loads((evidence / "ready.json").read_text())
    parent_dir = evidence / "conversations" / UUID(ready["parent_id"]).hex
    if lagging_head:
        base_path = parent_dir / "base_state.json"
        base = json.loads(base_path.read_text())
        actions = [
            event
            for event in EventLog(LocalFileStore(str(parent_dir)))
            if isinstance(event, ActionEvent)
        ]
        base["leaf_event_id"] = actions[0].parent_id
        base_path.write_text(json.dumps(base))
    with (evidence / "after.log").open("w") as log:
        completed = subprocess.run(
            [sys.executable, script, "phase2", "--evidence", str(evidence)],
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    if completed.returncode:
        raise RuntimeError(f"Native recovery failed; see {evidence / 'after.log'}")
    result = json.loads((evidence / "result.json").read_text())
    assert result["recovery_pid"] != ready["pid"]
    return result


@pytest.mark.parametrize("lagging_head", [False, True])
def test_native_task_recovery_after_process_death(
    tmp_path: Path, lagging_head: bool
) -> None:
    result = run_restart_demo(tmp_path / "evidence", lagging_head=lagging_head)
    assert result["children_status"] == ["error", "error"]
    assert len(set(result["task_ids"])) == 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["demo", "phase1", "phase2"])
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    if args.mode == "phase1":
        asyncio.run(_phase_one(args.evidence))
    elif args.mode == "phase2":
        asyncio.run(_phase_two(args.evidence))
    else:
        print(json.dumps(run_restart_demo(args.evidence.resolve()), indent=2))
