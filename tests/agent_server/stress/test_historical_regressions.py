"""Real-path canaries for #4481, #4417, and #4473.

Delay filesystem/snapshot work and the provider transport, not the server's
scheduling or locks. The same workloads must fail against each pre-fix tree.
Health probes include scheduling delay: ASGI request duration alone misses a
blocked event loop because the request cannot start until the block ends.
"""

import asyncio
import os
import threading
import time
from collections.abc import Awaitable
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from litellm.types.utils import ModelResponse

import openhands.agent_server.conversation_service as conversation_module
import openhands.sdk.llm.llm as llm_module
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import BashCommand, StartConversationRequest
from openhands.sdk import Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.workspace import LocalWorkspace
from tests.agent_server.stress.budgets import (
    EVENT_LOOP_RESPONSIVENESS,
    HISTORICAL_REGRESSIONS,
)
from tests.agent_server.stress.scripts import placeholder_llm, wait_for_terminal


pytestmark = [pytest.mark.stress, pytest.mark.timeout(30)]


async def _assert_health_during(
    client: httpx.AsyncClient,
    work: Awaitable[httpx.Response],
    scenario: str,
) -> httpx.Response:
    armed = asyncio.Event()
    stop = asyncio.Event()
    latencies: list[float] = []

    async def sample_health() -> None:
        while not stop.is_set():
            started = time.monotonic()
            armed.set()
            await asyncio.sleep(HISTORICAL_REGRESSIONS.probe_interval_s)
            response = await client.get("/health")
            assert response.status_code == 200, response.text
            latencies.append(time.monotonic() - started)

    sampler = asyncio.create_task(sample_health())
    await armed.wait()
    try:
        response = await work
    finally:
        stop.set()
        await sampler

    assert response.status_code == 200, response.text
    assert latencies
    assert max(latencies) < EVENT_LOOP_RESPONSIVENESS.health_p99_s, (
        f"{scenario} blocked the event loop: scheduled /health probe took "
        f"{max(latencies):.3f}s (budget "
        f"{EVENT_LOOP_RESPONSIVENESS.health_p99_s:.3f}s)"
    )
    return response


async def test_bash_search_keeps_health_responsive(
    bash_service: BashEventService,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = BashCommand(command="echo historical-regression")
    bash_service._save_event_to_file(command)
    original_scandir = os.scandir
    scanned = threading.Event()

    def slow_scandir(path):
        if str(path) == str(bash_service.bash_events_dir):
            scanned.set()
            time.sleep(HISTORICAL_REGRESSIONS.blocking_work_s)
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", slow_scandir)
    response = await _assert_health_during(
        client,
        client.get("/api/bash/bash_events/search"),
        "bash event search",
    )
    assert scanned.is_set(), "the request did not exercise the directory scan"
    assert [UUID(item["id"]) for item in response.json()["items"]] == [command.id]


async def test_conversation_listing_keeps_health_responsive(
    conversation_service: ConversationService,
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    info, _ = await conversation_service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=placeholder_llm("compose-canary"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            autotitle=False,
        )
    )
    original_compose = conversation_module._compose_conversation_info
    composed = threading.Event()

    def slow_compose(*args, **kwargs):
        composed.set()
        time.sleep(HISTORICAL_REGRESSIONS.blocking_work_s)
        return original_compose(*args, **kwargs)

    monkeypatch.setattr(conversation_module, "_compose_conversation_info", slow_compose)
    response = await _assert_health_during(
        client,
        client.get("/api/conversations/search"),
        "ConversationInfo composition",
    )
    assert composed.is_set(), "the request did not compose a conversation snapshot"
    assert [UUID(item["id"]) for item in response.json()["items"]] == [info.id]


async def test_real_llm_calls_overlap_across_conversations(
    conversation_service: ConversationService,
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_calls = 0
    peak_active_calls = 0
    completed_calls = 0
    counter_lock = threading.Lock()

    def enter_call() -> None:
        nonlocal active_calls, peak_active_calls
        with counter_lock:
            active_calls += 1
            peak_active_calls = max(peak_active_calls, active_calls)

    def finish_call() -> ModelResponse:
        nonlocal active_calls, completed_calls
        with counter_lock:
            active_calls -= 1
            completed_calls += 1
        return ModelResponse(
            model="gpt-4o",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "done"},
                }
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    def completion(**kwargs) -> ModelResponse:
        enter_call()
        time.sleep(HISTORICAL_REGRESSIONS.per_call_latency_s)
        return finish_call()

    async def acompletion(**kwargs) -> ModelResponse:
        enter_call()
        await asyncio.sleep(HISTORICAL_REGRESSIONS.per_call_latency_s)
        return finish_call()

    monkeypatch.setattr(llm_module, "litellm_completion", completion)
    monkeypatch.setattr(llm_module, "litellm_acompletion", acompletion)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ids: list[UUID] = []
    for i in range(HISTORICAL_REGRESSIONS.n_conversations):
        info, _ = await conversation_service.start_conversation(
            StartConversationRequest(
                agent=Agent(llm=placeholder_llm(f"transport-canary-{i}"), tools=[]),
                workspace=LocalWorkspace(working_dir=str(workspace)),
                autotitle=False,
            )
        )
        service = await conversation_service.get_event_service(info.id)
        assert service is not None
        await service.send_message(
            Message(role="user", content=[TextContent(text="hello")]), run=False
        )
        ids.append(info.id)

    responses = await asyncio.gather(
        *[client.post(f"/api/conversations/{cid.hex}/run") for cid in ids]
    )
    assert all(response.status_code == 200 for response in responses)
    statuses = await asyncio.gather(*[wait_for_terminal(client, cid) for cid in ids])
    assert all(status == ConversationExecutionStatus.FINISHED for status in statuses)
    assert completed_calls == HISTORICAL_REGRESSIONS.n_conversations
    assert peak_active_calls > 1, (
        "real LLM transports never overlapped across conversations; "
        "a process-wide config lock may be serializing provider calls"
    )
