"""Utilities for redacting sensitive data from logs and error responses.

This module provides a centralized, unified set of patterns and functions for
detecting and redacting secret-bearing keys in structured data (JSON objects,
headers, etc.). It's the single source of truth for secret key detection across
the SDK.
"""

from collections.abc import Mapping
from typing import Any

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
