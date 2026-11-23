import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from random import uniform

import httpx

from openhands.agent_server.persistence.trajectory_batches import batch_after
from openhands.sdk.observability.trajectory_capture import (
    AdmissionTicket,
    ShadowCapture,
)
from openhands.sdk.observability.trajectory_journal import Journal


logger = logging.getLogger(__name__)
_INTERVAL = 2


def _ack(response: httpx.Response) -> int:
    response.raise_for_status()
    data = response.json()
    value = data.get("acked_through") if isinstance(data, dict) else None
    if type(value) is not int or value < 0:
        raise ValueError("Invalid Cloud trajectory acknowledgment")
    return value


async def _drain(
    client: httpx.AsyncClient, url: str, journal: Journal, *, after: int | None = None
) -> tuple[int, bool]:
    if after is None:
        after = _ack(await client.get(url))
    for _ in range(4):
        data, through = await asyncio.to_thread(batch_after, journal, after)
        if not data:
            return after, True
        response = await client.post(
            url,
            params={"first": after + 1},
            content=data,
            headers={"Content-Type": "application/x-ndjson"},
        )
        if _ack(response) != through:
            raise ValueError("Cloud acknowledged a different trajectory range")
        after = through
    return after, False


def _retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status in (408, 429) or status >= 500
    return isinstance(error, (httpx.RequestError, sqlite3.OperationalError))


def _retry_delay(error: Exception, failures: int) -> float:
    cap = min(60, 2 ** min(failures, 6))
    delay = uniform(cap / 2, cap)
    if isinstance(error, httpx.HTTPStatusError):
        header = error.response.headers.get("Retry-After", "")
        if len(header) <= 6 and header.isascii() and header.isdecimal():
            delay = max(delay, int(header))
    return delay


@dataclass
class _Stream:
    url: str
    journal: Journal
    after: int | None = None
    failures: int = 0
    retry_at: float = 0
    caught_up: bool = False
    stopped: bool = False

    async def flush(self, client: httpx.AsyncClient, now: float) -> None:
        if self.stopped or now < self.retry_at:
            return
        try:
            self.after, self.caught_up = await _drain(
                client, self.url, self.journal, after=self.after
            )
        except (httpx.HTTPError, sqlite3.Error, ValueError, OSError) as error:
            self.after = None
            self.caught_up = False
            self.failures += 1
            self.stopped = not _retryable(error)
            self.retry_at = asyncio.get_running_loop().time() + _retry_delay(
                error, self.failures
            )
            if self.stopped or self.failures in (1, 4, 16):
                logger.warning(
                    "Cloud trajectory upload failed for %s (%s, retry=%s)",
                    self.journal.conversation_id,
                    type(error).__name__,
                    not self.stopped,
                )
        else:
            self.failures = 0
            self.retry_at = 0


async def admission(
    ticket: AdmissionTicket, base_url: str, headers: dict[str, str]
) -> None:
    journal = ticket.capture.journal
    if journal is None:
        return
    url = (
        f"{base_url}/trajectories/{journal.conversation_id}/{journal.instance_id}"
        f"/admissions/{ticket.run_id}"
    )
    async with httpx.AsyncClient(headers=headers, timeout=2) as client:
        for attempt in range(2):
            try:
                response = await client.post(
                    url, json={"origin": ticket.capture.origin}
                )
                response.raise_for_status()
                return
            except (httpx.HTTPError, ValueError) as error:
                delay = _retry_delay(error, 1)
                if attempt == 1 or not _retryable(error) or delay > 2:
                    logger.warning(
                        "Cloud trajectory admission failed for %s (%s)",
                        journal.conversation_id,
                        type(error).__name__,
                    )
                    return
                await asyncio.sleep(delay)


async def publish(
    capture: ShadowCapture, base_url: str, headers: dict[str, str], idle: asyncio.Event
) -> None:
    if capture.journal is None:
        return
    journal = capture.journal
    url = f"{base_url}/trajectories/{journal.conversation_id}/{journal.instance_id}"
    streams = [_Stream(f"{url}/journal", journal)]
    if capture.witness is not None:
        streams.append(_Stream(f"{url}/witness", capture.witness))

    loop = asyncio.get_running_loop()
    async with httpx.AsyncClient(headers=headers, timeout=12) as client:
        while True:
            was_idle = idle.is_set()
            now = loop.time()
            await asyncio.gather(*(stream.flush(client, now) for stream in streams))
            if all(stream.stopped for stream in streams):
                return
            if (
                was_idle
                and idle.is_set()
                and all(stream.caught_up or stream.stopped for stream in streams)
            ):
                return
            delay = min(
                max(_INTERVAL, stream.retry_at - loop.time())
                for stream in streams
                if not stream.stopped
            )
            if idle.is_set():
                await asyncio.sleep(delay)
            else:
                try:
                    await asyncio.wait_for(idle.wait(), delay)
                except TimeoutError:
                    pass
