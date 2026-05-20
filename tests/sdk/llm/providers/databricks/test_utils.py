"""Tests for Databricks FMAPI resilience utilities.

Covers: USER_AGENT format, DatabricksTimeouts defaults, compute_backoff (Retry-After cap,
full-jitter fallback), normalize_host, map_databricks_error, validate_databricks_config,
and the _raise_non_retryable / _raise_mapped helper functions.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch
from litellm.exceptions import (
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServiceUnavailableError,
)

import httpx

from openhands.sdk.llm.providers.databricks.utils import (
    USER_AGENT,
    DatabricksTimeouts,
    _RETRY_AFTER_MAX_S,
    _raise_mapped,
    _raise_non_retryable,
    compute_backoff,
    map_databricks_error,
    normalize_host,
    validate_databricks_config,
)
from openhands.sdk.llm.providers.databricks.auth import AuthStrategy


# ---------------------------------------------------------------------------
# USER_AGENT
# ---------------------------------------------------------------------------

def test_user_agent_format() -> None:
    """PWAF: USER_AGENT must be '<product>/<version>' and identify as OpenHandsOSS."""
    assert USER_AGENT.startswith("OpenHandsOSS/"), (
        f"PWAF non-compliant User-Agent: {USER_AGENT!r}"
    )
    # Version portion must be non-empty
    _, version = USER_AGENT.split("/", 1)
    assert version, f"User-Agent missing version: {USER_AGENT!r}"


def test_user_agent_no_newlines() -> None:
    """USER_AGENT must not contain whitespace (HTTP header safety)."""
    assert "\n" not in USER_AGENT
    assert "\r" not in USER_AGENT


# ---------------------------------------------------------------------------
# DatabricksTimeouts
# ---------------------------------------------------------------------------

def test_databricks_timeouts_defaults() -> None:
    t = DatabricksTimeouts()
    assert t.connect_s == 10.0
    assert t.read_s == 120.0
    assert t.chunk_s == 30.0
    assert t.pool_s == 5.0


def test_databricks_timeouts_override() -> None:
    t = DatabricksTimeouts(connect_s=5.0, read_s=60.0, chunk_s=15.0)
    assert t.connect_s == 5.0
    assert t.read_s == 60.0
    assert t.chunk_s == 15.0


# ---------------------------------------------------------------------------
# compute_backoff
# ---------------------------------------------------------------------------

def test_compute_backoff_retry_after_within_cap() -> None:
    """Retry-After of 10s → sleep 10s."""
    result = compute_backoff(attempt=0, retry_after="10")
    assert result == 10.0


def test_compute_backoff_retry_after_exceeds_cap() -> None:
    """Retry-After above _RETRY_AFTER_MAX_S is capped (P1-4: 300s cap)."""
    result = compute_backoff(attempt=0, retry_after="999")
    assert result == _RETRY_AFTER_MAX_S


def test_compute_backoff_retry_after_at_cap() -> None:
    """Retry-After exactly at _RETRY_AFTER_MAX_S passes through."""
    result = compute_backoff(attempt=0, retry_after=str(_RETRY_AFTER_MAX_S))
    assert result == _RETRY_AFTER_MAX_S


def test_compute_backoff_no_retry_after_is_bounded() -> None:
    """Full-jitter fallback: result is in [0, min(60, 1 * 2^attempt)]."""
    for attempt in range(6):
        result = compute_backoff(attempt=attempt)
        max_wait = min(60.0, 1.0 * (2**attempt))
        assert 0.0 <= result <= max_wait, (
            f"attempt={attempt}: backoff {result:.3f} outside [0, {max_wait}]"
        )


def test_compute_backoff_caps_at_60s() -> None:
    """Full-jitter backoff never exceeds 60s for any attempt."""
    with patch("random.uniform", return_value=1.0):  # worst case: full multiplier
        for attempt in range(10, 20):
            result = compute_backoff(attempt=attempt)
            assert result <= 60.0


# ---------------------------------------------------------------------------
# normalize_host
# ---------------------------------------------------------------------------

def test_normalize_host_adds_https() -> None:
    assert normalize_host("adb-123.azuredatabricks.net") == (
        "https://adb-123.azuredatabricks.net"
    )


def test_normalize_host_strips_trailing_slash() -> None:
    assert normalize_host("https://adb-123.azuredatabricks.net/") == (
        "https://adb-123.azuredatabricks.net"
    )


def test_normalize_host_already_correct() -> None:
    host = "https://adb-123.azuredatabricks.net"
    assert normalize_host(host) == host


def test_normalize_host_strips_multiple_slashes() -> None:
    assert normalize_host("https://adb-123.azuredatabricks.net///") == (
        "https://adb-123.azuredatabricks.net"
    )


# ---------------------------------------------------------------------------
# map_databricks_error
# ---------------------------------------------------------------------------

def test_map_databricks_error_message_field() -> None:
    msg = map_databricks_error(429, {"message": "Rate limit exceeded"})
    assert "429" in msg
    assert "Rate limit exceeded" in msg


def test_map_databricks_error_error_description_field() -> None:
    msg = map_databricks_error(401, {"error_description": "token expired"})
    assert "401" in msg
    assert "token expired" in msg


def test_map_databricks_error_error_field() -> None:
    msg = map_databricks_error(500, {"error": "internal server error"})
    assert "500" in msg
    assert "internal server error" in msg


def test_map_databricks_error_empty_body() -> None:
    msg = map_databricks_error(503, {})
    assert "503" in msg
    assert "Unknown error" in msg


# ---------------------------------------------------------------------------
# validate_databricks_config
# ---------------------------------------------------------------------------

def test_validate_databricks_config_missing_host() -> None:
    with pytest.raises(ValueError, match="host is required"):
        validate_databricks_config(None, AuthStrategy.PAT)


def test_validate_databricks_config_no_https() -> None:
    with pytest.raises(ValueError, match="must start with 'https://'"):
        validate_databricks_config("http://adb-123.net", AuthStrategy.PAT)


def test_validate_databricks_config_u2m_no_tokens() -> None:
    """U2M without stored tokens must raise before any HTTP call."""
    with pytest.raises(ValueError, match="stored OAuth tokens"):
        validate_databricks_config(
            "https://adb-123.azuredatabricks.net",
            AuthStrategy.U2M,
            stored_tokens=None,
        )


def test_validate_databricks_config_m2m_missing_client_id() -> None:
    with pytest.raises(ValueError, match="DATABRICKS_CLIENT_ID"):
        validate_databricks_config(
            "https://adb-123.azuredatabricks.net",
            AuthStrategy.M2M,
            client_id=None,
            client_secret="secret",
        )


def test_validate_databricks_config_m2m_missing_client_secret() -> None:
    with pytest.raises(ValueError, match="DATABRICKS_CLIENT_SECRET"):
        validate_databricks_config(
            "https://adb-123.azuredatabricks.net",
            AuthStrategy.M2M,
            client_id="client-id",
            client_secret=None,
        )


def test_validate_databricks_config_pat_passes() -> None:
    """PAT path: only host is required."""
    # Should not raise
    validate_databricks_config("https://adb-123.azuredatabricks.net", AuthStrategy.PAT)


# ---------------------------------------------------------------------------
# _raise_non_retryable
# ---------------------------------------------------------------------------

def test_raise_non_retryable_401() -> None:
    resp = httpx.Response(401, json={"message": "Unauthorized"})
    with pytest.raises(AuthenticationError):
        _raise_non_retryable(resp)


def test_raise_non_retryable_400() -> None:
    resp = httpx.Response(400, json={"message": "Bad request"})
    with pytest.raises(BadRequestError):
        _raise_non_retryable(resp)


def test_raise_non_retryable_403() -> None:
    resp = httpx.Response(403, json={"message": "Forbidden"})
    with pytest.raises(AuthenticationError):
        _raise_non_retryable(resp)


# ---------------------------------------------------------------------------
# _raise_mapped
# ---------------------------------------------------------------------------

def test_raise_mapped_429() -> None:
    resp = httpx.Response(429, json={"message": "Rate limit exceeded"})
    with pytest.raises(RateLimitError):
        _raise_mapped(resp)


def test_raise_mapped_503() -> None:
    resp = httpx.Response(503, json={"message": "Service unavailable"})
    with pytest.raises(ServiceUnavailableError):
        _raise_mapped(resp)
