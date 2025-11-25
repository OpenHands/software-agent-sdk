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


def main() -> None:
    models_json_path = os.environ.get("MODELS_JSON_PATH")
    if not models_json_path:
        print("ERROR: MODELS_JSON_PATH not set", file=sys.stderr)
        sys.exit(1)

    model_ids_str = os.environ.get("MODEL_IDS", "")
    if not model_ids_str:
        print("ERROR: MODEL_IDS not set", file=sys.stderr)
        sys.exit(1)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        print("ERROR: GITHUB_OUTPUT not set", file=sys.stderr)
        sys.exit(1)

    # Load models.json
    models_path = Path(models_json_path)
    if not models_path.exists():
        print(f"ERROR: Models file not found: {models_path}", file=sys.stderr)
        sys.exit(1)

    with open(models_path, encoding="utf-8") as f:
        models = json.load(f)

    # Build lookup dict
    models_by_id = {m["id"]: m for m in models}

    # Parse requested model IDs
    model_ids = [mid.strip() for mid in model_ids_str.split(",") if mid.strip()]

    # Resolve model configs
    resolved = []
    for model_id in model_ids:
        if model_id not in models_by_id:
            print(
                f"ERROR: Model ID '{model_id}' not found in {models_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        resolved.append(models_by_id[model_id])

    # Output as JSON
    models_json = json.dumps(resolved, separators=(",", ":"))
    with open(github_output, "a", encoding="utf-8") as f:
        f.write(f"models_json={models_json}\n")

    print(f"Resolved {len(resolved)} model(s): {', '.join(model_ids)}")


if __name__ == "__main__":
    main()
