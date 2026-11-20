import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from openhands.agent_server.config import WebhookSpec
from openhands.agent_server.event_service import EventService
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.event import Event
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


@dataclass
class DurableWebhookSubscriber(Subscriber[Event]):
    conversation_id: UUID
    service: EventService
    spec: WebhookSpec
    session_api_key: str | None = None
    _wake: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _acked: int | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    async def __call__(self, _event: Event) -> None:
        if not self._closed:
            self._wake.set()
            if self._task is None:
                self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        async with self._client() as client:
            while True:
                self._wake.clear()
                try:
                    await self._drain(client)
                except Exception as exc:
                    self._log_failure(exc)
                    await asyncio.sleep(self.spec.retry_delay or 1)
                else:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), self.spec.flush_delay)
                        await asyncio.sleep(min(0.25, self.spec.flush_delay))

    def _client(self) -> httpx.AsyncClient:
        headers = self.spec.headers.copy()
        if self.session_api_key:
            headers["X-Session-API-Key"] = self.session_api_key
        return httpx.AsyncClient(headers=headers, timeout=30.0)

    async def _drain(self, client: httpx.AsyncClient) -> None:
        url = (
            f"{self.spec.base_url.rstrip('/')}/event-stream/{self.conversation_id.hex}"
        )
        if self._acked is None:
            self._acked = self._read_ack(await client.get(url))

        while batch := await asyncio.to_thread(
            self.service.webhook_event_batch,
            self._acked,
            min(self.spec.event_buffer_size, 1000),
            self.spec.max_batch_bytes,
        ):
            ack = self._read_ack(await client.post(url, json={"events": batch}))
            if ack != batch[-1]["seq"]:
                raise ValueError("Invalid durable webhook batch acknowledgement")
            self._acked = ack

    @staticmethod
    def _read_ack(response: httpx.Response) -> int:
        response.raise_for_status()
        data = response.json()
        ack = data.get("acked_through") if isinstance(data, dict) else None
        if type(ack) is not int or ack < -1:
            raise ValueError("Invalid durable webhook acknowledgement")
        return ack

    def _log_failure(self, exc: Exception) -> None:
        logger.warning(
            "durable_webhook_delivery_failed "
            "conversation_id=%s acked_through=%s error=%s status=%s",
            self.conversation_id.hex,
            self._acked,
            type(exc).__name__,
            exc.response.status_code
            if isinstance(exc, httpx.HTTPStatusError)
            else None,
        )

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        try:
            async with asyncio.timeout(10), self._client() as client:
                await self._drain(client)
        except Exception as exc:
            self._log_failure(exc)
