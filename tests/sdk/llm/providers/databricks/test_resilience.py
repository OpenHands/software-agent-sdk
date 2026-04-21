"""Tests for the fetch_with_retry retry loop.

Covers: success on first try, retry on 429/503, no retry on 400/401/403,
Retry-After cap at 300s (P1-4), exponential backoff, connection error retry,
and exhaustion of all retries.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import httpx
import pytest
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServiceUnavailableError,
)

from openhands.sdk.llm.providers.databricks.utils import (
    fetch_with_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_URL = "https://adb-123.azuredatabricks.net/serving-endpoints/my-model/invocations"
_HEADERS = {"Authorization": "Bearer tok", "Content-Type": "application/json"}
_PAYLOAD = {"messages": [{"role": "user", "content": "hi"}]}


def _make_client_with_responses(*responses: httpx.Response) -> httpx.Client:
    """Return a mock httpx.Client whose .post() yields responses in order."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = list(responses)
    return mock_client


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_fetch_with_retry_success_first_try() -> None:
    """200 response on first attempt — no retry, no sleep."""
    ok_resp = httpx.Response(200, json={"id": "1", "choices": []})
    client = _make_client_with_responses(ok_resp)

    with patch("time.sleep") as mock_sleep:
        result = fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert result.status_code == 200
    mock_sleep.assert_not_called()
    client.post.assert_called_once()


# ---------------------------------------------------------------------------
# 429 / 5xx — should retry
# ---------------------------------------------------------------------------

def test_fetch_with_retry_retries_on_429() -> None:
    """429 → sleep → retry → 200 on second attempt."""
    r429 = httpx.Response(429, json={"message": "Rate limit"})
    r200 = httpx.Response(200, json={"id": "1", "choices": []})
    client = _make_client_with_responses(r429, r200)

    with patch("time.sleep"):
        result = fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert result.status_code == 200
    assert client.post.call_count == 2


def test_fetch_with_retry_retries_on_503() -> None:
    """503 → retry → 200."""
    r503 = httpx.Response(503, json={"message": "Service unavailable"})
    r200 = httpx.Response(200, json={"id": "1", "choices": []})
    client = _make_client_with_responses(r503, r200)

    with patch("time.sleep"):
        result = fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert result.status_code == 200


def test_fetch_with_retry_uses_retry_after_header() -> None:
    """When server sends Retry-After header, sleep that many seconds (capped at 300s)."""
    r429 = httpx.Response(429, headers={"Retry-After": "42"}, json={"message": "rl"})
    r200 = httpx.Response(200, json={"id": "1", "choices": []})
    client = _make_client_with_responses(r429, r200)

    slept: list[float] = []
    with patch("time.sleep", side_effect=lambda s: slept.append(s)):
        fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert len(slept) == 1
    assert slept[0] == 42.0


def test_fetch_with_retry_caps_retry_after_at_300s() -> None:
    """Retry-After > 300s must be capped at 300s (P1-4)."""
    r429 = httpx.Response(429, headers={"Retry-After": "9999"}, json={"message": "rl"})
    r200 = httpx.Response(200, json={"id": "1", "choices": []})
    client = _make_client_with_responses(r429, r200)

    slept: list[float] = []
    with patch("time.sleep", side_effect=lambda s: slept.append(s)):
        fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert len(slept) == 1
    assert slept[0] == 300.0


def test_fetch_with_retry_exhausts_and_raises_rate_limit() -> None:
    """All retries exhausted on 429 → raises RateLimitError."""
    r429 = httpx.Response(429, json={"message": "Rate limit"})
    client = _make_client_with_responses(r429, r429, r429, r429)

    with patch("time.sleep"):
        with pytest.raises(RateLimitError):
            fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert client.post.call_count == 4  # 1 initial + 3 retries


def test_fetch_with_retry_exhausts_and_raises_service_unavailable() -> None:
    """All retries exhausted on 503 → raises ServiceUnavailableError."""
    r503 = httpx.Response(503, json={"message": "Down"})
    client = _make_client_with_responses(r503, r503, r503, r503)

    with patch("time.sleep"):
        with pytest.raises(ServiceUnavailableError):
            fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)


# ---------------------------------------------------------------------------
# Non-retryable status codes
# ---------------------------------------------------------------------------

def test_fetch_with_retry_does_not_retry_on_400() -> None:
    """400 raises BadRequestError immediately without retry."""
    r400 = httpx.Response(400, json={"message": "Bad request"})
    client = _make_client_with_responses(r400)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(BadRequestError):
            fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    mock_sleep.assert_not_called()
    client.post.assert_called_once()


def test_fetch_with_retry_does_not_retry_on_401() -> None:
    """401 raises AuthenticationError immediately without retry."""
    r401 = httpx.Response(401, json={"message": "Unauthorized"})
    client = _make_client_with_responses(r401)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(AuthenticationError):
            fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    mock_sleep.assert_not_called()


def test_fetch_with_retry_does_not_retry_on_403() -> None:
    """403 raises AuthenticationError immediately."""
    r403 = httpx.Response(403, json={"message": "Forbidden"})
    client = _make_client_with_responses(r403)

    with pytest.raises(AuthenticationError):
        with patch("time.sleep"):
            fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)


def test_fetch_with_retry_does_not_retry_on_422() -> None:
    """422 raises BadRequestError immediately."""
    r422 = httpx.Response(422, json={"message": "Validation error"})
    client = _make_client_with_responses(r422)

    with pytest.raises(BadRequestError):
        with patch("time.sleep"):
            fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=3)


# ---------------------------------------------------------------------------
# RETRYABLE_EXCEPTIONS (network errors)
# ---------------------------------------------------------------------------

def test_fetch_with_retry_retries_on_connect_error() -> None:
    """httpx.ConnectError is retried (network transient failure)."""
    mock_client = MagicMock(spec=httpx.Client)
    ok_resp = httpx.Response(200, json={"id": "1", "choices": []})
    mock_client.post.side_effect = [httpx.ConnectError("connection refused"), ok_resp]

    with patch("time.sleep"):
        result = fetch_with_retry(mock_client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert result.status_code == 200
    assert mock_client.post.call_count == 2


def test_fetch_with_retry_raises_api_connection_error_after_network_exhaustion() -> None:
    """Persistent ConnectError after all retries → APIConnectionError."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = httpx.ConnectError("connection refused")

    with patch("time.sleep"):
        with pytest.raises(APIConnectionError):
            fetch_with_retry(mock_client, _URL, _HEADERS, _PAYLOAD, max_retries=2)


def test_fetch_with_retry_retries_on_read_timeout() -> None:
    """httpx.ReadTimeout is retried."""
    mock_client = MagicMock(spec=httpx.Client)
    ok_resp = httpx.Response(200, json={"id": "1", "choices": []})
    mock_client.post.side_effect = [httpx.ReadTimeout("timed out"), ok_resp]

    with patch("time.sleep"):
        result = fetch_with_retry(mock_client, _URL, _HEADERS, _PAYLOAD, max_retries=3)

    assert result.status_code == 200


# ---------------------------------------------------------------------------
# max_retries=0 means one attempt only
# ---------------------------------------------------------------------------

def test_fetch_with_retry_zero_retries_raises_immediately_on_429() -> None:
    """max_retries=0: no retry on a retryable status — raises after first attempt."""
    r429 = httpx.Response(429, json={"message": "Rate limit"})
    client = _make_client_with_responses(r429)

    with patch("time.sleep") as mock_sleep:
        with pytest.raises(RateLimitError):
            fetch_with_retry(client, _URL, _HEADERS, _PAYLOAD, max_retries=0)

    mock_sleep.assert_not_called()
    client.post.assert_called_once()
