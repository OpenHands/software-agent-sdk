"""Generic HTTP exporter.

POSTs batches of already-sanitized events to a configured endpoint. Intended to
front a backend that revalidates runtime auth and consent before forwarding
onward, so no vendor credentials need to live in the sandbox.

The payload contract is stable and documented in the agent-server README:

    POST <endpoint>
    Content-Type: application/json
    Authorization: Bearer <token>          # only when a token is configured

    {
      "schema_version": 1,
      "events": [
        {
          "event": "agent_server.conversation_finished",
          "distinct_id": "<user id, or anon:...>",
          "occurred_at": "2026-07-21T10:00:00Z",
          "properties": { ... allowlisted properties ... }
        }
      ]
    }

Failures propagate to ``BufferedTelemetrySink``, which owns the retry, drop and
bounded-shutdown behaviour — this class deliberately implements none of it.
"""

import httpx

from openhands.agent_server.telemetry.models import (
    TELEMETRY_SCHEMA_VERSION,
    DiagnosticEvent,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0


class HttpExporter:
    """Ships sanitized diagnostic events to an arbitrary HTTP endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = endpoint
        self._headers = {"Content-Type": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send(self, events: list[DiagnosticEvent]) -> None:
        payload = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "events": [
                {
                    "event": str(event.event_name),
                    "distinct_id": event.distinct_id,
                    "occurred_at": event.occurred_at.isoformat(),
                    "properties": event.to_payload(),
                }
                for event in events
            ],
        }
        response = await self._client.post(
            self._endpoint, json=payload, headers=self._headers
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:
            logger.debug("HTTP exporter close failed: %s", type(exc).__name__)
