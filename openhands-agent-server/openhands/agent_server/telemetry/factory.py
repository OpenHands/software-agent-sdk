"""Builds :class:`DiagnosticEvent` objects.

Centralising construction here means the runtime envelope, the pseudonym salt
and the identity rules are decided in exactly one place, so a caller cannot
accidentally assemble an event with a raw conversation id or an unbucketed
count.
"""

import sys
import uuid
from datetime import datetime
from typing import Final
from uuid import UUID

from openhands.agent_server.server_details_router import ServerInfo
from openhands.agent_server.telemetry.models import (
    TELEMETRY_SCHEMA_VERSION,
    DiagnosticEvent,
    DiagnosticProperties,
    EventName,
    RuntimeProperties,
)
from openhands.agent_server.telemetry.sanitizer import (
    UNKNOWN_TOKEN,
    pseudonymize,
    safe_token,
    safe_version,
)
from openhands.sdk.utils import utc_now


ANONYMOUS_PREFIX: Final[str] = "anon:"

#: Optional request header carrying the caller's analytics identity. The
#: frontend sets it (``posthog.get_distinct_id()``) so request-scoped activity
#: — which has no conversation ``user_id`` — attributes to the same person.
DISTINCT_ID_HEADER: Final[str] = "X-OpenHands-Telemetry-Distinct-Id"

_MAX_DISTINCT_ID_LEN: Final[int] = 256


def distinct_id_from_header(value: str | None) -> str | None:
    """Coerce the ``X-OpenHands-Telemetry-Distinct-Id`` header, or ``None``.

    Trusted the same way as ``user_id``: it becomes the analytics identity
    verbatim. Bounded and stripped of control characters so a stray value can
    neither exceed the schema's length limit nor smuggle newlines into a
    profile; anything left empty falls back to the anonymous id.
    """
    if not value:
        return None
    candidate = "".join(ch for ch in value if ch.isprintable()).strip()
    if not candidate:
        return None
    return candidate[:_MAX_DISTINCT_ID_LEN]


def _platform_token() -> str:
    return safe_token(sys.platform, default=UNKNOWN_TOKEN)


def _python_version() -> str:
    # Not sys.version: that carries compiler and build metadata.
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def build_runtime_properties(*, deferred_init: bool) -> RuntimeProperties:
    """Snapshot the coarse runtime facts shared by every event."""
    # Versions and build metadata come from ServerInfo's own field defaults, so
    # /server_info and telemetry can never disagree about what is running.
    info = ServerInfo(uptime=0.0, idle_time=0.0)
    return RuntimeProperties(
        server_version=safe_version(info.version),
        sdk_version=safe_version(info.sdk_version),
        tools_version=safe_version(info.tools_version),
        build_git_sha=safe_version(info.build_git_sha),
        build_git_ref=safe_version(info.build_git_ref),
        python_version=_python_version(),
        platform=_platform_token(),
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
