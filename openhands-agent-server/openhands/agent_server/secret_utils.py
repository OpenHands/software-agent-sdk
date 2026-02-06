"""Utilities for sanitizing secret entries in serialized conversation data.

When secrets are masked (due to a missing cipher or key rotation), their values
become ``null``.  Pydantic validation rejects these because ``SecretSource``
expects valid typed values.  The helpers in this module strip such entries
*before* validation so that conversations can still be loaded and polled.

See https://github.com/OpenHands/OpenHands/issues/12714
"""

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


def is_null_secret(value: Any) -> bool:
    """Check whether a single secret entry is null or has a null value.

    A secret is considered null when:
    - The value itself is ``None`` (any secret type serialized as bare null).
    - It is a dict whose ``value`` key is ``None``.  This covers:
      - ``StaticSecret`` where ``value`` was redacted/masked to null.
      - Untyped dicts (missing ``kind``) that only carried a value.
      ``LookupSecret`` is unaffected because it carries ``url`` (required,
      non-nullable) instead of ``value`` — masking only applies to
      ``StaticSecret.value`` via the cipher/redaction pipeline.

    Returns:
        True if the secret entry should be discarded.
    """
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    # Only filter dicts that represent a StaticSecret (or untyped) with a null
    # value.  Other kinds (e.g. LookupSecret) do not have a ``value`` field so
    # this check does not match them.
    if value.get("value") is None:
        kind = value.get("kind", "")
        if kind in ("StaticSecret", ""):
            return True
    return False


def filter_secrets_dict(secrets: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *secrets* with null/invalid entries removed.

    Args:
        secrets: Mapping of secret names to their serialized representations.

    Returns:
        A new dict containing only valid secret entries.
    """
    return {
        key: value for key, value in secrets.items() if not is_null_secret(value)
    }


def filter_invalid_secrets(data: dict[str, Any]) -> None:
    """Filter null secrets from a ``StoredConversation`` dict (in place).

    Targets the top-level ``secrets`` key used by ``StoredConversation``.
    When secrets are masked (due to missing cipher or key rotation), their
    values become null.  This causes Pydantic ``ValidationError`` because
    ``SecretSource`` expects valid typed values.

    Args:
        data: Parsed JSON dict that may contain a ``secrets`` key.
    """
    secrets = data.get("secrets")
    if secrets and isinstance(secrets, dict):
        data["secrets"] = filter_secrets_dict(secrets)


def filter_invalid_secrets_in_state(data: dict[str, Any]) -> None:
    """Filter null secrets from a ``ConversationState`` model dump (in place).

    Unlike :func:`filter_invalid_secrets` which targets the top-level
    ``secrets`` key on ``StoredConversation``, this targets
    ``secret_registry.secret_sources`` used by ``ConversationState``.

    Args:
        data: Dict produced by ``ConversationState.model_dump()``.
    """
    registry = data.get("secret_registry")
    if registry and isinstance(registry, dict):
        sources = registry.get("secret_sources")
        if sources and isinstance(sources, dict):
            registry["secret_sources"] = filter_secrets_dict(sources)


def preprocess_stored_conversation_json(json_str: str) -> str:
    """Preprocess stored conversation JSON to handle null secret values.

    This is applied before Pydantic validation to gracefully handle
    conversations that were persisted with masked/null secrets (e.g., after
    key rotation or missing ``OH_SECRET_KEY``).

    Args:
        json_str: Raw JSON string from meta.json.

    Returns:
        Cleaned JSON string safe for Pydantic validation.
    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to preprocess conversation JSON: {e}")
        return json_str

    filter_invalid_secrets(data)
    return json.dumps(data)
