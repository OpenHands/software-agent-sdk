#!/usr/bin/env python3
"""ACP-registry drift check vs openhands-sdk.

Reads `src/models/acp-providers.json` (the TS-side source of truth) and
compares it field-for-field against `ACP_PROVIDERS` in the installed
openhands-sdk. Exits non-zero with a unified diff on mismatch.

Run locally:
    pip install -r scripts/requirements-acp-check.txt
    python scripts/check-acp-drift.py

Pass --write to regenerate src/models/acp-providers.json from ACP_PROVIDERS
instead of just checking for drift (e.g. after bumping a provider's pinned
version or adding a new one to the SDK registry).

CI runs this as the `validate-acp-providers` job on every PR + push to main.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import pathlib
import sys


def _normalize(value):
    """Coerce tuples to lists and dataclasses/pydantic models to dicts; recurse."""
    if hasattr(value, "model_dump"):  # pydantic models (e.g. ACPFileSecretSpec)
        return _normalize(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _normalize(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def _dump_json(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate src/models/acp-providers.json from ACP_PROVIDERS "
        "instead of failing on drift.",
    )
    args = parser.parse_args(argv)

    try:
        from openhands.sdk.settings.acp_providers import (
            ACP_PROVIDERS,  # type: ignore[import-not-found]
        )
    except ImportError as exc:
        print(
            "ERROR: cannot import openhands-sdk. "
            "Run `pip install -r scripts/requirements-acp-check.txt` first.",
            file=sys.stderr,
        )
        print(f"  ({exc})", file=sys.stderr)
        return 2

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    ts_json_path = repo_root / "src" / "models" / "acp-providers.json"
    py_data = _normalize(dict(ACP_PROVIDERS))
    py_text = _dump_json(py_data)

    if args.write:
        ts_json_path.write_text(py_text)
        n = len(py_data) if isinstance(py_data, dict) else 0
        print(f"Wrote {ts_json_path} from openhands-sdk ACP_PROVIDERS ({n} providers).")
        return 0

    ts_data = _normalize(json.loads(ts_json_path.read_text()))
    ts_text = _dump_json(ts_data)

    if ts_text == py_text:
        n = len(ts_data) if isinstance(ts_data, dict) else 0
        print(
            f"OK: src/models/acp-providers.json matches openhands-sdk ({n} providers)."
        )
        return 0

    print(
        "ERROR: src/models/acp-providers.json has drifted from openhands-sdk.\n"
        "Update src/models/acp-providers.json to match the Python source at\n"
        "openhands-sdk/openhands/sdk/settings/acp_providers.py.\n",
        file=sys.stderr,
    )
    diff = difflib.unified_diff(
        ts_text.splitlines(keepends=True),
        py_text.splitlines(keepends=True),
        fromfile="src/models/acp-providers.json",
        tofile="openhands-sdk ACP_PROVIDERS",
    )
    sys.stderr.writelines(diff)
    return 1


if __name__ == "__main__":
    sys.exit(main())
