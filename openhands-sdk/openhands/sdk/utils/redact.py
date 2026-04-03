"""Utilities for redacting sensitive data from logs and error responses.

This module provides a centralized, unified set of patterns and functions for
detecting and redacting secret-bearing keys in structured data (JSON objects,
headers, URLs, etc.). It's the single source of truth for secret key detection
across the SDK.

Copies / consumers of this module (keep in sync when changing):
  - OpenHands/runtime-api  →  utils/redact.py  (partial copy: sanitize_dict, is_secret_key)
  - All-Hands-AI/OpenHands →  openhands/utils/log_utils.py  (imports sanitize_dict, adds URL redaction)
"""

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx


# Patterns used for substring matching against key names (case-insensitive).
# Keys containing any of these patterns will have their values redacted.
# Examples: api_key, X-Access-Token, Authorization, password, secret
# Note: We use "AUTHORIZATION" instead of "AUTH" to avoid false positives
# like "Author" headers.
SECRET_KEY_PATTERNS = frozenset(
    {
        "AUTHORIZATION",
        "COOKIE",
        "CREDENTIAL",
        "KEY",
        "PASSWORD",
        "SECRET",
        "SESSION",
        "TOKEN",
    }
)

# Keys that should have ALL nested values redacted (not just detected secret keys).
# These typically contain environment variables or headers that may include secrets.
REDACT_ALL_VALUES_KEYS = frozenset({"environment", "env", "headers", "acp_env"})

# Specific URL query parameter names (lowercased) that should always be redacted,
# in addition to any parameter matching SECRET_KEY_PATTERNS via is_secret_key().
SENSITIVE_URL_PARAMS = frozenset(
    {
        "tavilyapikey",
        "apikey",
        "api_key",
        "token",
        "access_token",
        "secret",
        "key",
    }
)


def is_secret_key(key: str) -> bool:
    """Check if a key name likely contains secret data.

    Performs case-insensitive substring matching against known secret key patterns.

    Args:
        key: The key name to check (e.g., "api_key", "Authorization", "X-Token")

    Returns:
        True if the key matches any secret pattern, False otherwise

    Examples:
        >>> is_secret_key("api_key")
        True
        >>> is_secret_key("Authorization")
        True
        >>> is_secret_key("user_name")
        False
    """
    key_upper = key.upper()
    return any(pattern in key_upper for pattern in SECRET_KEY_PATTERNS)


def _redact_all_values(value: Any) -> Any:
    """Recursively redact all values while preserving structure (key names)."""
    if isinstance(value, Mapping):
        return {k: _redact_all_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_all_values(item) for item in value]
    return "<redacted>"


def sanitize_dict(content: Any) -> Any:
    """Recursively redact likely secrets from structured data.

    This function walks through a nested dict/list structure and:
    - Redacts values for keys matching SECRET_KEY_PATTERNS
    - Redacts ALL nested values for keys in REDACT_ALL_VALUES_KEYS
    - Leaves other values unchanged

    Args:
        content: A dict, list, or scalar value to sanitize

    Returns:
        A sanitized copy with secrets replaced by '<redacted>'
    """
    if isinstance(content, Mapping):
        sanitized = {}
        for key, value in content.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower in REDACT_ALL_VALUES_KEYS:
                sanitized[key] = _redact_all_values(value)
            elif is_secret_key(key_str):
                sanitized[key] = "<redacted>"
            else:
                sanitized[key] = sanitize_dict(value)
        return sanitized
    if isinstance(content, list):
        return [sanitize_dict(item) for item in content]
    return content


def http_error_log_content(response: httpx.Response) -> str | dict:
    """Return a sanitized representation of an HTTP error body for logs.

    For JSON responses, returns a sanitized dict with secrets redacted.
    For non-JSON responses, returns a placeholder message with the body length.

    Args:
        response: The httpx.Response to extract error content from

    Returns:
        A sanitized dict or string safe for logging
    """
    try:
        return sanitize_dict(response.json())
    except Exception:
        body_len = len(response.text or "")
        return f"<non-JSON response body omitted ({body_len} chars)>"


def redact_url_params(url: str) -> str:
    """Redact sensitive query parameter values from a URL string.

    Parses the URL, checks each query parameter name against both
    ``SENSITIVE_URL_PARAMS`` (exact, case-insensitive) and ``is_secret_key()``
    (substring pattern matching), and replaces matching values with
    ``<redacted>``.

    Args:
        url: The URL string to sanitize.

    Returns:
        The URL with sensitive query parameter values replaced by '<redacted>'.
        If the URL has no query parameters or cannot be parsed, it is returned
        unchanged.

    Examples:
        >>> redact_url_params("https://example.com/search?q=hello&apikey=secret123")
        'https://example.com/search?q=hello&apikey=%3Credacted%3E'
        >>> redact_url_params("https://example.com/path")
        'https://example.com/path'
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    if not parsed.query:
        return url

    # parse_qs returns values as lists; keep_blank_values preserves params
    # with empty values so the reconstructed URL matches the original shape.
    params = parse_qs(parsed.query, keep_blank_values=True)

    redacted_params: dict[str, list[str]] = {}
    for param_name, values in params.items():
        if param_name.lower() in SENSITIVE_URL_PARAMS or is_secret_key(param_name):
            redacted_params[param_name] = ["<redacted>"] * len(values)
        else:
            redacted_params[param_name] = values

    # doseq=True tells urlencode to unpack the value lists correctly.
    redacted_query = urlencode(redacted_params, doseq=True)
    return urlunparse(parsed._replace(query=redacted_query))
