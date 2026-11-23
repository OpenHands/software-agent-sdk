import asyncio
import logging
import sqlite3

import httpx

from openhands.agent_server.persistence.trajectory_batches import batch_after
from openhands.sdk.observability.trajectory_capture import AdmissionTicket
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
    client: httpx.AsyncClient, url: str, journal: Journal, *, batches: int = 4
) -> None:
    after = _ack(await client.get(url))
    for _ in range(batches):
        data, through = await asyncio.to_thread(batch_after, journal, after)
        if not data:
            return
        response = await client.post(
            url,
            params={"first": after + 1},
            content=data,
            headers={"Content-Type": "application/x-ndjson"},
        )
        if _ack(response) != through:
            raise ValueError("Cloud acknowledged a different trajectory range")
        after = through


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
    try:
        async with httpx.AsyncClient(headers=headers, timeout=2) as client:
            response = await client.post(url, json={"origin": ticket.capture.origin})
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Cloud trajectory admission failed for %s (%s)",
            journal.conversation_id,
            type(exc).__name__,
        )


async def publish(
    ticket: AdmissionTicket, base_url: str, headers: dict[str, str], done: asyncio.Event
) -> None:
    capture = ticket.capture
    if capture.journal is None:
        return
    journal = capture.journal
    url = f"{base_url}/trajectories/{journal.conversation_id}/{journal.instance_id}"
    streams = [("journal", journal)]
    if capture.witness is not None:
        streams.append(("witness", capture.witness))

    async with httpx.AsyncClient(headers=headers, timeout=3) as client:

        async def drain_stream(name: str, source: Journal, final: bool) -> None:
            try:
                await _drain(
                    client, f"{url}/{name}", source, batches=16 if final else 4
                )
            except (
                httpx.HTTPError,
                sqlite3.Error,
                ValueError,
                OSError,
                KeyError,
            ) as exc:
                logger.warning(
                    "Cloud trajectory %s upload failed for %s (%s)",
                    name,
                    journal.conversation_id,
                    type(exc).__name__,
                )

        while True:
            final = done.is_set()
            await asyncio.gather(
                *(drain_stream(name, source, final) for name, source in streams)
            )
            if final:
                return
            try:
                await asyncio.wait_for(done.wait(), _INTERVAL)
            except TimeoutError:
                pass
