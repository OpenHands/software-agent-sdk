"""Utility functions for persistence module."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries.

    Values in ``updates`` override values in ``base``. Nested dicts are merged
    recursively. Non-dict values in ``updates`` replace values in ``base``.

    Args:
        base: The base dictionary.
        updates: The dictionary with updates to apply.

    Returns:
        A new dictionary with merged values.
    """
    result = dict(base)
    for key, value in updates.items():
        if (
            key in result
            and isinstance(result[key], Mapping)
            and isinstance(value, Mapping)
        ):
            result[key] = deep_merge(dict(result[key]), dict(value))
        else:
            result[key] = value
    return result
