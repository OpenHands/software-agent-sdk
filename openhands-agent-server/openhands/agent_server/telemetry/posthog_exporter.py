"""Optional PostHog exporter. The only vendor-aware module in the tree.

``posthog`` is imported lazily inside :meth:`PostHogExporter.__init__` so that
importing this package — or running the server with telemetry disabled — never
pulls the dependency in. Library and headless consumers therefore pay nothing,
and a missing extra degrades to the no-op sink instead of crashing startup.

**This exporter calls ``capture()`` and nothing else.** No ``identify()``, no
``alias()``, no ``group_identify()``, no ``$set``. The reasons, in order of
severity:

1. ``identify()`` creates or mutates a *person profile* that the hosting
   product (Canvas) already owns. A second writer clobbers its properties, and
   any drift in ``distinct_id`` (email vs uuid) mints a duplicate person that
   is billed as a second monthly active user.
2. ``alias()`` is irreversible in PostHog — a single mistake permanently
   merges two identities.
3. Person profiles turn cheap events into billed ones, and these are
   diagnostics rather than per-person product usage.

Correlation is achieved by *reusing* the identity the deployment already
established, never by asserting a new one.
"""

from __future__ import annotations

import asyncio

from openhands.agent_server.telemetry.factory import ANONYMOUS_PREFIX
from openhands.agent_server.telemetry.models import DiagnosticEvent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"


class PostHogExporter:
    """Ships sanitized diagnostic events to PostHog."""

    def __init__(
        self,
        api_key: str,
        *,
        host: str = DEFAULT_POSTHOG_HOST,
        timeout: int = 15,
    ) -> None:
        # Lazy, deliberately: module scope would defeat the optional extra.
        from posthog import Posthog

        self._client = Posthog(
            project_api_key=api_key,
            host=host,
            timeout=timeout,
            # Autocapture would ship full tracebacks, defeating `sanitizer`.
            enable_exception_autocapture=False,
            log_captured_exceptions=False,
            disable_geoip=True,
            enable_local_evaluation=False,
            sync_mode=False,
        )

    async def send(self, events: list[DiagnosticEvent]) -> None:
        """Hand a batch to the client.

        Runs on a worker thread: the PostHog client is synchronous and can
        block when its internal queue is saturated, which must never happen on
        the event loop.
        """
        await asyncio.to_thread(self._send_sync, events)

    def _send_sync(self, events: list[DiagnosticEvent]) -> None:
        for event in events:
            properties = event.to_payload()

            # Identified events omit this so they attach to the host's existing
            # person -- that is the correlation mechanism.
            if event.distinct_id.startswith(ANONYMOUS_PREFIX):
                properties["$process_person_profile"] = False

            self._client.capture(
                event.event_name,
                distinct_id=event.distinct_id,
                properties=properties,
                timestamp=event.occurred_at,
                disable_geoip=True,
            )

    async def aclose(self) -> None:
        try:
            await asyncio.to_thread(self._client.shutdown)
        except Exception as exc:
            logger.debug("PostHog shutdown failed: %s", type(exc).__name__)
