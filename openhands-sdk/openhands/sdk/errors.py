"""Domain errors: expected failures a caller can act on.

Stores and services raise these instead of transport-specific errors. A
transport maps each category to its own response once; the agent server does
it in ``openhands.agent_server.errors``. Nothing in this module knows about
HTTP, and it imports nothing from the SDK so any layer can raise from it.

The categories are deliberately few. Pick the one that says what the caller
should do: fix the reference (``NotFound``), resolve the clash (``Conflict``),
fix the input (``Invalid``), retry later (``Busy``, ``RateLimited``,
``Unavailable``), or report the failure (``OperationFailed``).
"""

from collections.abc import Mapping, Sequence


__all__ = [
    "Busy",
    "Conflict",
    "DomainError",
    "ErrorDetail",
    "Forbidden",
    "Gone",
    "Invalid",
    "LockTimeout",
    "Misdirected",
    "NotFound",
    "NotSupported",
    "OperationFailed",
    "RateLimited",
    "Unauthenticated",
    "Unavailable",
    "Unprocessable",
    "UpstreamFailed",
    "UpstreamTimeout",
]


# What a client is shown: a sentence, or a small structured payload such as
# ``{"code": ..., "message": ...}`` that a client can branch on.
type ErrorDetail = str | Mapping[str, object] | Sequence[object]


class DomainError(Exception):
    """Base of the domain error hierarchy.

    Args:
        detail: What the client is shown. ``None`` lets the transport use its
            default wording for the category.
        message: What logs and ``str(exc)`` show, when it should differ from
            ``detail`` (for example a lock timeout whose client-facing detail
            is just "busy, retry"). Defaults to ``detail`` when it is a string.
        extra: Additional top-level fields for the client, next to the detail.
        retry_after: Seconds (or an HTTP date) after which a retry may succeed.
    """

    def __init__(
        self,
        detail: ErrorDetail | None = None,
        *,
        message: str | None = None,
        extra: Mapping[str, object] | None = None,
        retry_after: str | None = None,
    ) -> None:
        if message is None:
            message = detail if isinstance(detail, str) else ""
        super().__init__(message)
        self.detail = detail
        self.extra: Mapping[str, object] = extra or {}
        self.retry_after = retry_after


class NotFound(DomainError):
    """The referenced item does not exist."""


class Gone(DomainError):
    """The item existed but is no longer available (archived, removed)."""


class Conflict(DomainError):
    """The request clashes with the current state (exists, running, in use)."""


class Invalid(DomainError, ValueError):
    """The request is malformed. Also a ``ValueError`` for existing callers."""


class Unprocessable(Invalid):
    """The request is well formed but cannot be acted on as given."""


class Unauthenticated(DomainError):
    """The caller did not prove who it is."""


class Forbidden(DomainError):
    """The caller is known but not allowed to do this."""


class Busy(DomainError):
    """A shared resource is held by someone else; retry shortly."""


class LockTimeout(Busy, TimeoutError):
    """A store lock wait timed out. Also a ``TimeoutError`` for existing callers."""


class RateLimited(DomainError):
    """The caller has used its allowance or capacity; back off and retry."""


class Unavailable(DomainError):
    """A dependency is not configured or not ready."""


class NotSupported(DomainError):
    """This deployment does not implement the operation."""


class Misdirected(DomainError):
    """The request reached a server that cannot answer for this target."""


class UpstreamFailed(DomainError):
    """A service this one depends on failed or answered unusably."""


class UpstreamTimeout(UpstreamFailed):
    """A service this one depends on did not answer in time."""


class OperationFailed(DomainError):
    """The operation failed for a reason the caller cannot fix."""
