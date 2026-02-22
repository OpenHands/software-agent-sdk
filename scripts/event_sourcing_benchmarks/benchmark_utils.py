"""Shared utilities for event-sourcing benchmarks."""

import json
import os
import tarfile


def extract_conversation(tarpath: str, dest: str) -> str | None:
    """Extract a conversation .tar.gz and return the events/ dir path."""
    with tarfile.open(tarpath, "r:gz") as tf:
        tf.extractall(dest, filter="data")
    for root, _, _ in os.walk(dest):
        if os.path.basename(root) == "events":
            return root
    return None


def read_event_files(events_dir: str) -> list[dict]:
    """Read all event JSON files.

    Returns list of dicts with keys: filename, json_str, size_bytes, kind.
    """
    files = sorted(f for f in os.listdir(events_dir) if f.endswith(".json"))
    result = []
    for fname in files:
        path = os.path.join(events_dir, fname)
        with open(path) as f:
            content = f.read()
        try:
            kind = json.loads(content).get("kind", "unknown")
        except Exception:
            kind = "unknown"
        result.append(
            {
                "filename": fname,
                "json_str": content,
                "size_bytes": len(content.encode("utf-8")),
                "kind": kind,
            }
        )
    return result


def register_tool_types() -> None:
    """Import concrete tool classes to register them in the
    ToolDefinition discriminated union, enabling deserialization
    of real evaluation events that reference these tools.
    """
    import openhands.tools.file_editor  # noqa: F401
    import openhands.tools.task_tracker  # noqa: F401
    import openhands.tools.terminal  # noqa: F401
