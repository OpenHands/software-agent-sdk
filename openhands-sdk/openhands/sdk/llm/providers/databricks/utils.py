"""Databricks FMAPI resilience utilities.

Provides:
  USER_AGENT   — PWAF-required constant; set once, applied to ALL Databricks HTTP calls.
  fetch_with_retry — synchronous retry loop with exponential back-off + Retry-After.
  Helper functions: _log_retry, _raise_non_retryable, _raise_mapped, compute_backoff,
                    normalize_host, map_databricks_error, validate_databricks_config.
"""

from __future__ import annotations

import importlib.metadata
import logging
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServiceUnavailableError,
)

if TYPE_CHECKING:
    from openhands.sdk.llm.providers.databricks.auth import AuthStrategy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PWAF: User-Agent constant
# Must be set once at module load time and applied to ALL Databricks HTTP calls.
# Never re-imported per request. Never user-configurable.
# ---------------------------------------------------------------------------
def _get_version() -> str:
    try:
        return importlib.metadata.version("openhands-sdk")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


USER_AGENT: str = f"openhands_oss/{_get_version()}"
"""PWAF User-Agent for the OpenHands OSS Databricks connector.

Format (per Partner AI Dev Kit / PWAF telemetry skill):
    <isv>_<product>/<version>

- ISV:      openhands
- Product:  oss   (this is the OSS OpenHands product; non-OSS distributions
                   may ship their own build with a different product string)
- Version:  resolved from the installed `openhands-sdk` package metadata.

Applied to every Databricks HTTP call (AI Gateway `/invocations`, OAuth
token endpoint, serving-endpoints discovery). Never exposed as a user
config knob — connector-level constant per PWAF rules.
"""


# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------
@dataclass
class DatabricksTimeouts:
    connect_s: float = 10.0   # TCP + TLS; fail fast on unreachable host
    read_s: float = 120.0     # Non-streaming: full response wait
    chunk_s: float = 30.0     # Streaming: per-chunk idle timeout (resets per chunk)
    pool_s: float = 5.0       # Wait for connection from pool


# ---------------------------------------------------------------------------
# Retry tables
# ---------------------------------------------------------------------------
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
NON_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({400, 401, 403, 404, 422})
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)

STATUS_TO_LITELLM: dict[int, type[Exception]] = {
    429: RateLimitError,
    500: APIConnectionError,
    502: ServiceUnavailableError,
    503: ServiceUnavailableError,
    504: ServiceUnavailableError,
    400: BadRequestError,
    401: AuthenticationError,
    403: AuthenticationError,
    404: BadRequestError,
    422: BadRequestError,
}

# Hard cap on Retry-After header value to prevent runaway sleeps from misbehaving proxies.
_RETRY_AFTER_MAX_S: float = 300.0


# ---------------------------------------------------------------------------
# Retry helper functions (P0-2: were called but not defined in P3 plan)
# ---------------------------------------------------------------------------

def _log_retry(
    attempt: int,
    max_retries: int,
    status_code: int,
    wait_s: float,
    url: str,
    response_headers: httpx.Headers,
) -> None:
    """Log a retry event. Never logs credential values."""
    logger.warning(
        "databricks_fmapi_retry",
        extra={
            "attempt": attempt + 1,
            "max_retries": max_retries,
            "status_code": status_code,
            "wait_s": round(wait_s, 2),
            "request_id": response_headers.get("x-request-id"),
            "url": url,
            # Intentionally NOT logging: Authorization header, token, or secret
        },
    )


def _raise_non_retryable(response: httpx.Response) -> None:
    """Raise the appropriate LiteLLM exception for a non-retryable status code.

    Called immediately (no sleep) for 400/401/403/404/422.
    """
    try:
        body = response.json()
    except Exception:
        body = {}
    msg = map_databricks_error(response.status_code, body)
    exc_class = STATUS_TO_LITELLM.get(response.status_code, BadRequestError)
    raise exc_class(msg, model="", llm_provider="databricks")


def _raise_mapped(response: httpx.Response) -> None:
    """Raise LiteLLM exception for a retryable status after all retries are exhausted."""
    try:
        body = response.json()
    except Exception:
        body = {}
    msg = map_databricks_error(response.status_code, body)
    exc_class = STATUS_TO_LITELLM.get(response.status_code, APIConnectionError)
    raise exc_class(msg, model="", llm_provider="databricks")


# ---------------------------------------------------------------------------
# Backoff and retry loop
# ---------------------------------------------------------------------------

def compute_backoff(attempt: int, retry_after: str | None = None) -> float:
    """Compute sleep duration for a retry attempt.

    Retry-After header wins but is capped at _RETRY_AFTER_MAX_S to prevent
    runaway sleeps from misbehaving proxies.  Falls back to full-jitter
    exponential backoff: sleep in [0, min(60, 1 * 2^attempt)].
    """
    if retry_after:
        return min(float(retry_after), _RETRY_AFTER_MAX_S)
    return min(60.0, 1.0 * (2**attempt)) * random.uniform(0, 1)


def fetch_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict,
    json: dict,
    max_retries: int = 3,
) -> httpx.Response:
    """Synchronous retry loop for FMAPI POST calls.

    Uses time.sleep (NOT asyncio.sleep) — _transport_call is always synchronous.
    On exhaustion of retries, raises the mapped LiteLLM exception.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.post(url, headers=headers, json=json)
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                wait = compute_backoff(
                    attempt, response.headers.get("Retry-After")
                )
                _log_retry(attempt, max_retries, response.status_code, wait, url, response.headers)
                time.sleep(wait)
                continue
            if response.status_code in NON_RETRYABLE_STATUS_CODES:
                _raise_non_retryable(response)
            if response.status_code in RETRYABLE_STATUS_CODES:
                # Exhausted retries on a retryable status code
                _raise_mapped(response)
            return response
        except RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt == max_retries:
                raise APIConnectionError(
                    str(exc), model="", llm_provider="databricks"
                ) from exc
            time.sleep(compute_backoff(attempt))

    # Unreachable; satisfies type checker
    raise APIConnectionError(
        f"Retry loop exhausted: {last_exc}", model="", llm_provider="databricks"
    )


# ---------------------------------------------------------------------------
# Miscellaneous helpers
# ---------------------------------------------------------------------------

def normalize_host(host: str) -> str:
    """Ensure host has https:// scheme and no trailing slash."""
    host = host.strip().rstrip("/")
    if not host.startswith("https://"):
        host = f"https://{host}"
    return host


def map_databricks_error(status: int, body: dict) -> str:
    """Extract human-readable error message from FMAPI error response body."""
    msg = (
        body.get("message")
        or body.get("error_description")
        or body.get("error")
        or "Unknown error"
    )
    return f"[{status}] {msg}"


def validate_databricks_config(
    host: str | None,
    strategy: "AuthStrategy",
    **creds: object,
) -> None:
    """Pre-flight validation — raises ValueError with actionable messages.

    Called during DatabricksLLM._init_databricks() so configuration errors
    surface at construction time rather than at first inference call.
    """
    if not host:
        raise ValueError(
            "Databricks host is required. Set databricks_host= or base_url= "
            "to your workspace URL (e.g. https://adb-xxx.azuredatabricks.net)"
        )
    if not host.startswith("https://"):
        raise ValueError(
            f"databricks_host must start with 'https://'. Got: {host!r}"
        )

    # Import AuthStrategy here to avoid circular import at module level
    from openhands.sdk.llm.providers.databricks.auth import AuthStrategy as _AS

    if strategy == _AS.U2M and not creds.get("stored_tokens"):
        raise ValueError(
            "U2M auth requires stored OAuth tokens. Complete the browser login flow "
            "at /auth/databricks/initiate first."
        )
    if strategy == _AS.M2M:
        if not creds.get("client_id") or not creds.get("client_secret"):
            raise ValueError(
                "M2M auth requires DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET "
                "(service principal credentials). Note: these are DIFFERENT from "
                "DATABRICKS_U2M_CLIENT_ID used for browser OAuth login."
            )
