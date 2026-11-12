"""HTTP mapping for the SDK domain errors.

``ERROR_STATUS`` is the only place a domain error category gets an HTTP
status. The exception handler in ``api.py`` resolves raised errors through it,
and routes document their error responses with :func:`error_responses`, which
reads the same table. Routers and services raise ``openhands.sdk.errors``
categories; they never build ``HTTPException`` (a guard test enforces it).
"""

from collections.abc import Mapping
from typing import Any

from fastapi import status

from openhands.sdk.errors import (
    Busy,
    Conflict,
    DomainError,
    ErrorDetail,
    Forbidden,
    Gone,
    Invalid,
    Misdirected,
    NotFound,
    NotSupported,
    OperationFailed,
    RateLimited,
    TooLarge,
    Unauthenticated,
    Unavailable,
    Unprocessable,
    UpstreamFailed,
    UpstreamTimeout,
)


ERROR_STATUS: Mapping[type[DomainError], int] = {
    Invalid: status.HTTP_400_BAD_REQUEST,
    Unauthenticated: status.HTTP_401_UNAUTHORIZED,
    Forbidden: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,
    Gone: status.HTTP_410_GONE,
    TooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    Misdirected: status.HTTP_421_MISDIRECTED_REQUEST,
    Unprocessable: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RateLimited: status.HTTP_429_TOO_MANY_REQUESTS,
    OperationFailed: status.HTTP_500_INTERNAL_SERVER_ERROR,
    NotSupported: status.HTTP_501_NOT_IMPLEMENTED,
    UpstreamFailed: status.HTTP_502_BAD_GATEWAY,
    Busy: status.HTTP_503_SERVICE_UNAVAILABLE,
    Unavailable: status.HTTP_503_SERVICE_UNAVAILABLE,
    UpstreamTimeout: status.HTTP_504_GATEWAY_TIMEOUT,
}

# Inverse lookup for errors that arrive carrying a status (a provider error
# with ``status_code``). Where two categories share a status, the first one
# listed in ``ERROR_STATUS`` wins (503 -> Busy).
_CATEGORY_FOR_STATUS: Mapping[int, type[DomainError]] = {
    code: category for category, code in reversed(ERROR_STATUS.items())
}


class ForwardedStatus(DomainError):
    """Relay the status another HTTP server chose (a proxied runtime's reply).

    Only for forwarding: the status is not ours to pick, so it cannot go
    through a category. Everything raised by this server uses a category.
    """

    def __init__(
        self,
        status_code: int,
        detail: ErrorDetail,
        *,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(detail, retry_after=retry_after)
        self.status_code = status_code


def _category_status(error_type: type[DomainError]) -> int:
    for cls in error_type.__mro__:
        if cls in ERROR_STATUS:
            return ERROR_STATUS[cls]
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def status_for(exc: DomainError) -> int:
    """The HTTP status for ``exc``: its nearest category in ``ERROR_STATUS``."""
    if isinstance(exc, ForwardedStatus):
        return exc.status_code
    return _category_status(type(exc))


def error_for_status(
    status_code: int, detail: ErrorDetail, *, retry_after: str | None = None
) -> DomainError:
    """Build the category error for a status carried by a lower-level error.

    A status no category maps to is relayed unchanged (``ForwardedStatus``).
    """
    category = _CATEGORY_FOR_STATUS.get(status_code)
    if category is None:
        return ForwardedStatus(status_code, detail, retry_after=retry_after)
    return category(detail, retry_after=retry_after)


def error_responses(
    descriptions: Mapping[type[DomainError], str],
) -> dict[int | str, dict[str, Any]]:
    """``responses=`` for a route, keyed by the statuses in ``ERROR_STATUS``."""
    return {
        _category_status(error_type): {"description": description}
        for error_type, description in descriptions.items()
    }
