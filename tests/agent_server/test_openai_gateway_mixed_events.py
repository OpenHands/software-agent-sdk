"""A direct event must not change a gateway turn's reply or token usage."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_router import (
    event_router,
    run_gateway_message,
    send_message,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.openai import service as gateway
from openhands.agent_server.openai.service import OpenAIAgentRunResult
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.message_request import SendMessageRequest
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm.message import TextContent
from openhands.sdk.llm.utils.metrics import Metrics, TokenUsage


class _InterleavedEventService:
    def __init__(self) -> None:
        self._gateway_run_lock = asyncio.Lock()
        self._active_gateway_runs = 0
        self._run_task: object | None = None
        self.gateway_sent = asyncio.Event()
        self.release_gateway_tail = asyncio.Event()
        self.sent: list[str] = []

    async def get_state(self):
        if not self.gateway_sent.is_set():
            count, prompt, completion = 2, 5, 1
            status = ConversationExecutionStatus.FINISHED
        elif not self.release_gateway_tail.is_set():
            count, prompt, completion = 3, 5, 1
            status = ConversationExecutionStatus.RUNNING
        elif "direct" in self.sent:
            count, prompt, completion = 6, 45, 7
            status = ConversationExecutionStatus.FINISHED
        else:
            count, prompt, completion = 4, 16, 3
            status = ConversationExecutionStatus.FINISHED
        stats = ConversationStats(
            usage_to_metrics={
                "default": Metrics(
                    accumulated_token_usage=TokenUsage(
                        prompt_tokens=prompt, completion_tokens=completion
                    )
                )
            }
        )
        return SimpleNamespace(
            events=[None] * count, stats=stats, execution_status=status
        )

    async def reserve_run_slot(self):
        return None

    async def send_message(self, message, run=False, run_slot=None):
        content = message.content[0].text
        self.sent.append(content)
        if content == "gateway":
            self.gateway_sent.set()

    async def wait_for_run_completion(self, timeout=None):
        await asyncio.wait_for(self.release_gateway_tail.wait(), timeout=timeout)
        return ConversationExecutionStatus.FINISHED

    async def get_agent_final_response(self):
        return "direct" if "direct" in self.sent else "gateway"


@pytest.mark.parametrize("gateway_path", ["local", "docker-inner"])
async def test_direct_event_cannot_change_gateway_reply_and_usage(
    gateway_path: str,
) -> None:
    fake = _InterleavedEventService()
    event_service = cast(EventService, fake)

    async def get_event_service(_conversation_id):
        return event_service

    app = FastAPI()
    app.state.config = Config()
    conversation_service = cast(
        ConversationService, SimpleNamespace(get_event_service=get_event_service)
    )
    app.state.conversation_service = conversation_service
    app.include_router(event_router, prefix="/api")
    conversation_id = uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if gateway_path == "local":
            gateway_call = gateway._run_agent(
                start_request=cast(StartConversationRequest, None),
                user_content=[TextContent(text="gateway")],
                conversation_service=conversation_service,
                reusable_conversation_id=conversation_id,
                config=Config(),
                registry=None,
            )
        else:
            gateway_call = run_gateway_message(
                SendMessageRequest(
                    role="user", content=[TextContent(text="gateway")], run=True
                ),
                event_service,
            )
        gateway_task = asyncio.create_task(gateway_call)
        try:
            await asyncio.wait_for(fake.gateway_sent.wait(), timeout=5)
        except TimeoutError:
            if gateway_task.done():
                pytest.fail(f"Gateway did not start: {gateway_task.result()}")
            raise
        direct_task = asyncio.create_task(
            client.post(
                f"/api/conversations/{conversation_id}/events",
                json={
                    "role": "user",
                    "content": [{"type": "text", "text": "direct"}],
                    "run": True,
                },
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(direct_task), timeout=0.2)
            direct_finished_during_gateway = True
        except TimeoutError:
            direct_finished_during_gateway = False
        finally:
            fake.release_gateway_tail.set()

        gateway_result = await asyncio.wait_for(gateway_task, timeout=5)
        direct_response = await asyncio.wait_for(direct_task, timeout=5)

        if gateway_path == "local":
            local_result = cast(OpenAIAgentRunResult, gateway_result)
            assert direct_response.status_code == 409, direct_response.text
            assert local_result.final_response == "gateway"
            assert local_result.usage is not None
            assert local_result.usage.prompt_tokens == 16
            assert local_result.usage.completion_tokens == 3
            assert fake.sent == ["gateway"]
        else:
            docker_result = cast(dict[str, Any], gateway_result)
            assert not direct_finished_during_gateway
            assert direct_response.status_code == 200, direct_response.text
            assert docker_result["final_response"] == "gateway"
            assert docker_result["usage"]["prompt_tokens"] == 11
            assert docker_result["usage"]["completion_tokens"] == 2
            assert fake.sent == ["gateway", "direct"]

    if gateway_path == "local":
        await send_message(
            SendMessageRequest(
                role="user", content=[TextContent(text="direct")], run=True
            ),
            event_service,
        )
        assert fake.sent == ["gateway", "direct"]


async def test_cancelled_local_gateway_allows_next_direct_event() -> None:
    fake = _InterleavedEventService()
    event_service = cast(EventService, fake)

    async def get_event_service(_conversation_id):
        return event_service

    gateway_task = asyncio.create_task(
        gateway._run_agent(
            start_request=cast(StartConversationRequest, None),
            user_content=[TextContent(text="gateway")],
            conversation_service=cast(
                ConversationService,
                SimpleNamespace(get_event_service=get_event_service),
            ),
            reusable_conversation_id=uuid4(),
            config=Config(),
            registry=None,
        )
    )
    await asyncio.wait_for(fake.gateway_sent.wait(), timeout=5)
    gateway_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gateway_task

    await send_message(
        SendMessageRequest(role="user", content=[TextContent(text="direct")], run=True),
        event_service,
    )
    assert fake.sent == ["gateway", "direct"]


async def test_local_gateway_rejects_active_direct_run_before_saving() -> None:
    fake = _InterleavedEventService()
    event_service = cast(EventService, fake)

    async def get_event_service(_conversation_id):
        return event_service

    async def run_gateway() -> OpenAIAgentRunResult:
        return await gateway._run_agent(
            start_request=cast(StartConversationRequest, None),
            user_content=[TextContent(text="gateway")],
            conversation_service=cast(
                ConversationService,
                SimpleNamespace(get_event_service=get_event_service),
            ),
            reusable_conversation_id=uuid4(),
            config=Config(),
            registry=None,
        )

    direct_run = asyncio.create_task(asyncio.Event().wait())
    fake._run_task = direct_run
    fake.release_gateway_tail.set()
    with pytest.raises(HTTPException) as exc:
        await run_gateway()
    assert exc.value.status_code == 409
    assert fake.sent == []

    direct_run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await direct_run
    result = await run_gateway()
    assert result.final_response == "gateway"
    assert fake.sent == ["gateway"]


async def test_docker_gateway_waits_for_rearmed_direct_run() -> None:
    class RearmedDirectRunService(_InterleavedEventService):
        def __init__(self) -> None:
            super().__init__()
            self._run_task = object()
            self.wait_count = 0
            self.release_gateway_tail.set()

        async def wait_for_run_completion(self, timeout=None):
            self.wait_count += 1
            self._run_task = object() if self.wait_count == 1 else None
            return ConversationExecutionStatus.FINISHED

        async def send_message(self, message, run=False, run_slot=None):
            assert self._run_task is None, "Gateway saved during a direct run"
            await super().send_message(message, run, run_slot)

    fake = RearmedDirectRunService()
    result = await run_gateway_message(
        SendMessageRequest(
            role="user", content=[TextContent(text="gateway")], run=True
        ),
        cast(EventService, fake),
    )
    assert result["final_response"] == "gateway"
    assert fake.wait_count == 3
