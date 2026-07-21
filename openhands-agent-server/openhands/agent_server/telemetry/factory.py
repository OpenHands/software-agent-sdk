"""Builds :class:`DiagnosticEvent` objects.

Centralising construction here means the runtime envelope, the pseudonym salt
and the identity rules are decided in exactly one place, so a caller cannot
accidentally assemble an event with a raw conversation id or an unbucketed
count.
"""

import os
import sys
import uuid
from datetime import datetime
from importlib.metadata import version
from typing import Final
from uuid import UUID

from openhands.agent_server.telemetry.models import (
    TELEMETRY_SCHEMA_VERSION,
    DiagnosticEvent,
    DiagnosticProperties,
    EventName,
    RuntimeProperties,
)
from openhands.agent_server.telemetry.policy import TelemetryMode
from openhands.agent_server.telemetry.sanitizer import (
    UNKNOWN_TOKEN,
    pseudonymize,
    safe_token,
    safe_version,
)
from openhands.sdk.utils import utc_now


ANONYMOUS_PREFIX: Final = "anon:"


def _package_version(dist_name: str) -> str:
    """Mirror of ``server_details_router._package_version``."""
    try:
        return safe_version(version(dist_name))
    except Exception:
        return UNKNOWN_TOKEN


def _platform_token() -> str:
    return safe_token(sys.platform, default=UNKNOWN_TOKEN)


def _python_version() -> str:
    # Not sys.version: that carries compiler and build metadata.
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def build_runtime_properties(
    *, mode: TelemetryMode, deferred_init: bool
) -> RuntimeProperties:
    """Snapshot the coarse runtime facts shared by every event."""
    return RuntimeProperties(
        server_version=_package_version("openhands-agent-server"),
        sdk_version=_package_version("openhands-sdk"),
        tools_version=_package_version("openhands-tools"),
        build_git_sha=safe_version(os.environ.get("OPENHANDS_BUILD_GIT_SHA")),
        build_git_ref=safe_version(os.environ.get("OPENHANDS_BUILD_GIT_REF")),
        python_version=_python_version(),
        platform=_platform_token(),
        deployment_mode=mode,
        deferred_init=deferred_init,
    )


class DiagnosticEventFactory:
    """Stamps the envelope onto sanitized per-event properties."""

    def __init__(
        self,
        *,
        runtime: RuntimeProperties,
        salt: str | bytes | None = None,
    ) -> None:
        self._runtime = runtime
        # Random fallback: stable within a run, unlinkable across runs.
        self._salt = salt if salt else uuid.uuid4().hex
        self._session_ref = uuid.uuid4().hex

    @property
    def session_ref(self) -> str:
        return self._session_ref

    @property
    def runtime(self) -> RuntimeProperties:
        return self._runtime

    def conversation_ref(self, conversation_id: UUID | str) -> str:
        """Keyed pseudonym for a conversation id.

        Never the raw UUID: that value appears in URLs, logs and the hosting
        product's database, so emitting it would make the analytics dataset
        joinable back to an individual.
        """
        raw = (
            conversation_id.bytes
            if isinstance(conversation_id, UUID)
            else str(conversation_id).encode("utf-8")
        )
        return pseudonymize(raw, self._salt)

    def distinct_id(self, user_id: str | None) -> str:
        """Resolve the correlation identity.

        A deployment-supplied ``user_id`` is passed through verbatim so events
        land on the person the host already identified. Absent one, an
        in-memory per-process anonymous id is used — a restart yields a new
        value, which under-counts local uniques rather than minting a
        persistent identifier on someone's machine.
        """
        if user_id and user_id.strip():
            return user_id.strip()[:256]
        return f"{ANONYMOUS_PREFIX}{self._session_ref}"

    def build(
        self,
        event_name: EventName,
        properties: DiagnosticProperties,
        *,
        user_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> DiagnosticEvent:
        return DiagnosticEvent(
            event_name=event_name,
            schema_version=TELEMETRY_SCHEMA_VERSION,
            occurred_at=occurred_at or utc_now(),
            distinct_id=self.distinct_id(user_id),
            runtime=self._runtime,
            properties=properties,
        )
