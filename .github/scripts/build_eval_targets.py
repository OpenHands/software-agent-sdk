#!/usr/bin/env python3
"""Emit the model targets JSON for the run-eval workflow.

The workflow needs to loop over models differently depending on trigger type:
release triggers run every configured model, workflow_dispatch accepts a
comma-separated selection, and PR labels fall back to a single default model.
This helper centralizes that logic, validates model IDs, and writes the
`targets` / `models_text` outputs for later steps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def emit(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise SystemExit("GITHUB_OUTPUT is not set")
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def parse_model_ids(
    event_name: str,
    raw_models: str,
    all_models: list[dict],
    default_model: str,
) -> list[str]:
    if event_name == "release":
        return [entry["id"] for entry in all_models]
    if event_name == "workflow_dispatch":
        raw = raw_models or default_model
        values = [value.strip() for value in raw.split(",")]
        result = [value for value in values if value]
        if not result:
            raise SystemExit("No valid models provided in 'models' input")
        return result
    return [default_model]


def main() -> None:
    models = json.loads(os.environ["MODELS_JSON"])
    event_name = os.environ["GITHUB_EVENT_NAME"]
    instances = os.environ.get("EVAL_INSTANCES")
    if not instances:
        raise SystemExit("EVAL_INSTANCES is not set")
    default_model = os.environ.get("DEFAULT_MODEL_ID")
    if not default_model:
        raise SystemExit("DEFAULT_MODEL_ID is not set")

    model_map = {entry["id"]: entry for entry in models}
    model_ids = parse_model_ids(
        event_name,
        os.environ.get("MODELS_INPUT", "").strip(),
        models,
        default_model,
    )

    invalid = [model_id for model_id in model_ids if model_id not in model_map]
    if invalid:
        raise SystemExit(f"Unsupported model(s): {', '.join(invalid)}")

    targets = [
        {
            "model_id": model_id,
            "display_name": model_map[model_id]["display_name"],
            "llm_config": model_map[model_id]["llm_config"],
            "eval_instances": instances,
        }
        for model_id in model_ids
    ]

    models_text = ", ".join(
        f"{target['display_name']} ({target['eval_instances']})" for target in targets
    )

    emit("targets", json.dumps(targets))
    emit("models_text", models_text)


if __name__ == "__main__":
    main()
