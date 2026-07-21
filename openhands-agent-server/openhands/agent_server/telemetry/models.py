"""Versioned, allowlisted diagnostic-event contract for product analytics.

Everything that can ever leave this process as product analytics is declared
here as a frozen Pydantic model whose every field is a *constrained* scalar.
This is deliberate: a plain ``str`` field can hold a prompt, a traceback, or an
API key, so no property field is typed ``str``, ``Any``, ``dict`` or ``list``.
A leak therefore becomes a construction-time :class:`ValidationError` rather
than something a reviewer has to notice.

The single intentional exception is :attr:`DiagnosticEvent.distinct_id`, which
carries the caller-supplied ``user_id`` verbatim so events correlate with the
identity the hosting deployment already owns. It is documented as a
pass-through and excluded from the "no bare str" rule by design, not by
oversight.

This module must not import any analytics vendor SDK.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


TELEMETRY_SCHEMA_VERSION = 1

# ── Constrained scalar aliases ────────────────────────────────────────────
# A value that cannot structurally hold a sentence, a path, or a secret.

SafeToken = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.:\-]{0,63}$")]
"""Lowercase enum-ish token: ``finished``, ``anthropic``, ``0-5s``."""

SafeIdentifier = Annotated[
    str,
    StringConstraints(
        max_length=96,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*([.:][A-Za-z_][A-Za-z0-9_]*)*$",
    ),
]
"""A dotted Python identifier: ``ValueError``, ``litellm:RateLimitError``.

Deliberately *stricter* than a general token — no dashes, spaces, slashes or
``@``. That rules out the shapes secrets and paths actually take: an API key
(``sk-ant-api03-…``) and a filesystem path both fail this pattern, so neither
can occupy an ``error_class`` field even if a future caller passes one in.
"""

VersionToken = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+\-/]{0,63}$")
]
"""A release identifier: ``1.36.1``, ``refs/heads/main``, ``unknown``.

Separate from :data:`SafeIdentifier` because versions legitimately start with a
digit and contain dots, dashes and slashes. These values come from
``importlib.metadata`` and build-time environment variables, never from user
input.
"""

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16,64}$")]
"""Lowercase hex digest produced by :mod:`.sanitizer`."""

Bucket = SafeToken
"""A bucketed magnitude such as ``11-50``. Never a raw count."""


SERVER_STARTED = "agent_server.server_started"
SERVER_STOPPED = "agent_server.server_stopped"
CONVERSATION_STARTED = "agent_server.conversation_started"
CONVERSATION_FINISHED = "agent_server.conversation_finished"
CONVERSATION_FAILED = "agent_server.conversation_failed"
CONVERSATION_ERROR = "agent_server.conversation_error"
REQUEST_FAILED = "agent_server.request_failed"

EventNameLiteral = Literal[
    "agent_server.server_started",
    "agent_server.server_stopped",
    "agent_server.conversation_started",
    "agent_server.conversation_finished",
    "agent_server.conversation_failed",
    "agent_server.conversation_error",
    "agent_server.request_failed",
]

# ── Error categories ──────────────────────────────────────────────────────
# A frozen, low-cardinality classification derived purely from the exception
# *type*. Never from its message.

ErrorCategory = Literal[
    "llm_auth",
    "llm_rate_limit",
    "llm_timeout",
    "llm_context_window",
    "llm_bad_request",
    "tool_execution",
    "mcp",
    "workspace_io",
    "git",
    "network",
    "config",
    "cancelled",
    "internal",
    "unknown",
]

#: Maps an exception class *name* (never a message) to a category.
ERROR_CATEGORY_BY_CLASS_NAME: dict[str, ErrorCategory] = {
    # LLM / provider
    "AuthenticationError": "llm_auth",
    "PermissionDeniedError": "llm_auth",
    "RateLimitError": "llm_rate_limit",
    "APITimeoutError": "llm_timeout",
    "Timeout": "llm_timeout",
    "TimeoutError": "llm_timeout",
    "ContextWindowExceededError": "llm_context_window",
    "BadRequestError": "llm_bad_request",
    "UnprocessableEntityError": "llm_bad_request",
    "APIError": "network",
    "APIConnectionError": "network",
    "ServiceUnavailableError": "network",
    "InternalServerError": "network",
    # Tools / MCP
    "ToolExecutionError": "tool_execution",
    "ToolNotFoundError": "tool_execution",
    "MCPError": "mcp",
    "McpError": "mcp",
    # Workspace / IO
    "FileNotFoundError": "workspace_io",
    "PermissionError": "workspace_io",
    "IsADirectoryError": "workspace_io",
    "NotADirectoryError": "workspace_io",
    "OSError": "workspace_io",
    "IOError": "workspace_io",
    # Git
    "GitCommandError": "git",
    "InvalidGitRepositoryError": "git",
    # Network
    "ConnectionError": "network",
    "ConnectError": "network",
    "ReadTimeout": "network",
    "HTTPStatusError": "network",
    "ClientConnectorError": "network",
    # Config / validation
    "ValidationError": "config",
    "ValueError": "config",
    "KeyError": "config",
    "TypeError": "internal",
    # Cancellation
    "CancelledError": "cancelled",
    "KeyboardInterrupt": "cancelled",
}


# ── Runtime envelope ──────────────────────────────────────────────────────


class RuntimeProperties(BaseModel):
    """Coarse, non-identifying facts about the process emitting the event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server_version: VersionToken
    sdk_version: VersionToken
    tools_version: VersionToken
    build_git_sha: VersionToken
    build_git_ref: VersionToken
    python_version: SafeToken
    platform: SafeToken
    deployment_mode: Literal["cloud_locked", "local_opt_in", "disabled"]
    deferred_init: bool
    source: Literal["openhands-agent-server"] = "openhands-agent-server"


# ── Per-event property models ─────────────────────────────────────────────


class _BaseProperties(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ServerLifecycleProperties(_BaseProperties):
    kind: Literal["server_lifecycle"] = "server_lifecycle"


class ConversationStartedProperties(_BaseProperties):
    kind: Literal["conversation_started"] = "conversation_started"

    conversation_ref: Digest
    llm_model_family: SafeToken
    agent_kind: SafeToken
    tool_count: int = Field(ge=0)
    is_fork: bool
    has_agent_profile: bool
    workspace_kind: SafeToken
    confirmation_policy: SafeToken


class ConversationOutcomeProperties(_BaseProperties):
    """Terminal outcome. Magnitudes are bucketed, never raw.

    Raw counts joined with a timestamp are a re-identification vector, so
    duration/iterations/tokens/cost are all reported as coarse buckets.
    """

    kind: Literal["conversation_outcome"] = "conversation_outcome"

    conversation_ref: Digest
    terminal_status: SafeToken
    duration_bucket: Bucket
    event_count_bucket: Bucket
    total_tokens_bucket: Bucket
    cost_bucket: Bucket
    llm_model_family: SafeToken


class ErrorProperties(_BaseProperties):
    """A failure, reduced to a groupable shape.

    Notably absent: the exception message, the traceback, and any path. See
    :func:`openhands.agent_server.telemetry.sanitizer.normalize_exception`.
    """

    kind: Literal["error"] = "error"

    conversation_ref: Digest | None = None
    error_class: SafeIdentifier
    error_category: ErrorCategory
    error_fingerprint: Digest
    error_origin_module: SafeIdentifier | None = None
    error_origin_lineno: int | None = Field(default=None, ge=0)
    is_first_party: bool
    is_terminal: bool
    tool_name: SafeToken | None = None
    error_id: SafeToken | None = None


class RequestFailedProperties(_BaseProperties):
    """An unhandled 5xx.

    ``route_template`` is the parametrised route (``/api/conversations/{id}``),
    never the concrete path, which embeds identifiers.
    """

    kind: Literal["request_failed"] = "request_failed"

    route_template: Annotated[
        str, StringConstraints(pattern=r"^/[A-Za-z0-9_/{}.\-]{0,127}$")
    ]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    status_code: int = Field(ge=100, le=599)
    error_class: SafeIdentifier
    error_category: ErrorCategory
    error_fingerprint: Digest
    error_id: SafeToken | None = None


DiagnosticProperties = Annotated[
    ServerLifecycleProperties
    | ConversationStartedProperties
    | ConversationOutcomeProperties
    | ErrorProperties
    | RequestFailedProperties,
    Field(discriminator="kind"),
]


# ── Envelope ──────────────────────────────────────────────────────────────


class DiagnosticEvent(BaseModel):
    """One sanitized analytics event. The only thing a sink ever accepts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_name: EventNameLiteral
    schema_version: int = TELEMETRY_SCHEMA_VERSION
    occurred_at: datetime

    distinct_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    """Correlation identity, passed through verbatim.

    This is the deployment-supplied ``user_id`` when present, so events land on
    the person the hosting deployment already identified, or ``anon:<hex>`` for
    an unidentified local session. It is the one field intentionally exempt
    from the constrained-token rule — correlation requires the value to match
    the host's id byte for byte. It is never used to *create* an identity; see
    ``posthog_exporter``.
    """

    runtime: RuntimeProperties
    properties: DiagnosticProperties

    def to_payload(self) -> dict[str, object]:
        """Flatten to the property bag an exporter sends.

        ``distinct_id`` is excluded — it is the transport's addressing field,
        not an event property.
        """
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            **self.runtime.model_dump(mode="json"),
            **self.properties.model_dump(mode="json", exclude={"kind"}),
        }
        return payload


#: Every property that may appear in a payload. A deny test asserts the models
#: produce exactly this set, so adding one is a deliberate, reviewed act.
EXPECTED_PROPERTY_NAMES: frozenset[str] = frozenset(
    {
        # envelope
        "schema_version",
        # runtime
        "server_version",
        "sdk_version",
        "tools_version",
        "build_git_sha",
        "build_git_ref",
        "python_version",
        "platform",
        "deployment_mode",
        "deferred_init",
        "source",
        # conversation
        "conversation_ref",
        "llm_model_family",
        "agent_kind",
        "tool_count",
        "is_fork",
        "has_agent_profile",
        "workspace_kind",
        "confirmation_policy",
        # outcome
        "terminal_status",
        "duration_bucket",
        "event_count_bucket",
        "total_tokens_bucket",
        "cost_bucket",
        # error
        "error_class",
        "error_category",
        "error_fingerprint",
        "error_origin_module",
        "error_origin_lineno",
        "is_first_party",
        "is_terminal",
        "tool_name",
        "error_id",
        # request
        "route_template",
        "method",
        "status_code",
    }
)
