#!/usr/bin/env python3
"""
Resolve model IDs to full model configurations.

Reads:
- MODELS_JSON_PATH: path to models.json file
- MODEL_IDS: comma-separated model IDs

Outputs to GITHUB_OUTPUT:
- models_json: JSON array of full model configs with display names
"""

import json
import os
import sys
from pathlib import Path


def error_exit(msg: str, exit_code: int = 1) -> None:
    """Print error message and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def get_required_env(key: str) -> str:
    """Get required environment variable or exit with error."""
    value = os.environ.get(key)
    if not value:
        error_exit(f"{key} not set")
    return value


def find_models_by_id(models: list[dict], model_ids: list[str]) -> list[dict]:
    """Find models by ID. Fails fast on missing ID.

    Args:
        models: List of model dictionaries from models.json
        model_ids: List of model IDs to find

    Returns:
        List of model dictionaries matching the IDs

    Raises:
        SystemExit: If any model ID is not found
    """
    models_by_id = {m["id"]: m for m in models}
    resolved = []
    for model_id in model_ids:
        if model_id not in models_by_id:
            error_exit(f"Model ID '{model_id}' not found")
        resolved.append(models_by_id[model_id])
    return resolved


def main() -> None:
    models_json_path = get_required_env("MODELS_JSON_PATH")
    model_ids_str = get_required_env("MODEL_IDS")
    github_output = get_required_env("GITHUB_OUTPUT")

    # Load models.json
    models_path = Path(models_json_path)
    if not models_path.exists():
        error_exit(f"Models file not found: {models_path}")

    with open(models_path, encoding="utf-8") as f:
        models = json.load(f)

    # Parse requested model IDs
    model_ids = [mid.strip() for mid in model_ids_str.split(",") if mid.strip()]

    # Resolve model configs
    resolved = find_models_by_id(models, model_ids)

    # Output as JSON
    models_json = json.dumps(resolved, separators=(",", ":"))
    with open(github_output, "a", encoding="utf-8") as f:
        f.write(f"models_json={models_json}\n")

    print(f"Resolved {len(resolved)} model(s): {', '.join(model_ids)}")


if __name__ == "__main__":
    main()
