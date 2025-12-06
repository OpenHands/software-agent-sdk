#!/usr/bin/env python3
"""
Compatibility shim for GitHub Workflows tests.

Provides a utility to resolve a subset of models by ID from a provided
list, preserving the requested ID order and failing fast for missing IDs.

This mirrors the interface expected by tests in
`tests/github_workflows/test_resolve_model_config.py`.
"""

from __future__ import annotations

import sys
from typing import Any


def error_exit(msg: str, exit_code: int = 1) -> None:
    """Print error message and exit with the given code (default 1)."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def find_models_by_id(
    models: list[dict[str, Any]], model_ids: list[str]
) -> list[dict[str, Any]]:
    """Return models matching the given IDs, preserving the order of IDs.

    Args:
        models: List of model dictionaries, each with an "id" field
        model_ids: List of requested model IDs

    Returns:
        List of model dictionaries in the order specified by model_ids

    Raises:
        SystemExit: If any requested model ID is not present in `models`
    """
    if not model_ids:
        return []

    id_to_model = {m.get("id"): m for m in models if "id" in m}

    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for mid in model_ids:
        m = id_to_model.get(mid)
        if m is None:
            missing.append(mid)
        else:
            resolved.append(m)

    if missing:
        available = ", ".join(sorted(k for k in id_to_model if k))
        msg = (
            "Model ID(s) not found: "
            + ", ".join(missing)
            + ". Available models: "
            + available
        )
        error_exit(msg)

    return resolved
