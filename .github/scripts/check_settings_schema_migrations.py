#!/usr/bin/env python3
"""Require SDK settings migrations for incompatible persisted schema changes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]

SCHEMA_PROGRAM = r"""
import json
import sys
from pathlib import Path

source_tree = Path(sys.argv[1])
sys.path = [str(source_tree / path) for path in (
    "openhands-sdk",
    "openhands-tools",
    "openhands-workspace",
    "openhands-agent-server",
)] + sys.path

from openhands.sdk.settings import ConversationSettings, export_agent_settings_schema
import openhands.sdk.settings.model as settings_model

print(json.dumps({
    "agent_schema": export_agent_settings_schema().model_dump(mode="json"),
    "conversation_schema": ConversationSettings.export_schema().model_dump(mode="json"),
    "agent_version": settings_model.AGENT_SETTINGS_SCHEMA_VERSION,
    "conversation_version": settings_model.CONVERSATION_SETTINGS_SCHEMA_VERSION,
    "agent_migrations": sorted(settings_model._AGENT_SETTINGS_MIGRATIONS),
    "conversation_migrations": sorted(settings_model._CONVERSATION_SETTINGS_MIGRATIONS),
}))
"""


def _run(command: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def _candidate_refs(ref: str) -> list[str]:
    candidates = [ref]
    if not ref.startswith("origin/"):
        candidates.insert(0, f"origin/{ref}")
    return list(dict.fromkeys(candidates))


def _archive_ref(ref: str) -> Path | None:
    for candidate in _candidate_refs(ref):
        archive = subprocess.run(
            ["git", "archive", candidate],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        if archive.returncode != 0:
            continue

        source_tree = Path(tempfile.mkdtemp(prefix="settings-schema-base-src-"))
        extract = subprocess.run(
            ["tar", "-x", "-C", str(source_tree)],
            input=archive.stdout,
            capture_output=True,
        )
        if extract.returncode == 0:
            return source_tree
    return None


def _capture(source_tree: Path) -> dict[str, Any]:
    result = _run([sys.executable, "-c", SCHEMA_PROGRAM, str(source_tree)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])
    return json.loads(result.stdout)


def _flatten_fields(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for section in schema.get("sections", []):
        section_variant = section.get("variant")
        for field in section.get("fields", []):
            variant = field.get("variant") or section_variant or "*"
            field_id = f"{variant}:{field.get('key')}"
            fields[field_id] = field
    return fields


def _choice_values(field: dict[str, Any]) -> set[Any]:
    return {choice.get("value") for choice in field.get("choices", [])}


def _incompatible_changes(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
) -> list[str]:
    old_fields = _flatten_fields(old_schema)
    new_fields = _flatten_fields(new_schema)

    changes: list[str] = []
    for field_id, old_field in sorted(old_fields.items()):
        new_field = new_fields.get(field_id)
        if new_field is None:
            changes.append(f"removed field {field_id}")
            continue

        if old_field.get("value_type") != new_field.get("value_type"):
            changes.append(
                "changed field type "
                f"{field_id}: {old_field.get('value_type')} -> "
                f"{new_field.get('value_type')}"
            )

        removed_choices = _choice_values(old_field) - _choice_values(new_field)
        if removed_choices:
            changes.append(
                f"removed choices from {field_id}: {sorted(removed_choices)!r}"
            )

    return changes


def _check_model(
    *,
    name: str,
    old_info: dict[str, Any],
    new_info: dict[str, Any],
    schema_key: str,
    version_key: str,
    migrations_key: str,
) -> list[str]:
    errors: list[str] = []
    changes = _incompatible_changes(old_info[schema_key], new_info[schema_key])
    old_version = int(old_info[version_key])
    new_version = int(new_info[version_key])
    migrations = set(new_info[migrations_key])

    if new_version < old_version:
        errors.append(
            f"{name} schema version went backwards: {old_version} -> {new_version}"
        )

    if not changes:
        return errors

    print(f"{name} incompatible settings schema changes:")
    for change in changes:
        print(f"  - {change}")

    if new_version <= old_version:
        errors.append(
            f"{name} has incompatible settings schema changes but "
            f"{version_key} did not increase ({old_version} -> {new_version})."
        )
        return errors

    missing = [
        version
        for version in range(old_version, new_version)
        if version not in migrations
    ]
    if missing:
        errors.append(
            f"{name} schema version increased ({old_version} -> {new_version}) "
            f"but migrations are missing for version(s): {missing}."
        )

    return errors


def _base_ref() -> str:
    return (
        os.environ.get("SETTINGS_SCHEMA_BASE_REF")
        or os.environ.get("GITHUB_BASE_REF")
        or "origin/main"
    )


def main() -> int:
    base_ref = _base_ref()
    base_tree = _archive_ref(base_ref)
    if base_tree is None:
        print(
            f"::warning title=Settings schema::Unable to read base ref {base_ref}; "
            "skipping settings schema migration check."
        )
        return 0

    try:
        old_info = _capture(base_tree)
        new_info = _capture(REPO_ROOT)
    except Exception as exc:
        print(f"::error title=Settings schema::Failed to capture schemas: {exc}")
        return 1

    errors: list[str] = []
    errors.extend(
        _check_model(
            name="AgentSettings",
            old_info=old_info,
            new_info=new_info,
            schema_key="agent_schema",
            version_key="agent_version",
            migrations_key="agent_migrations",
        )
    )
    errors.extend(
        _check_model(
            name="ConversationSettings",
            old_info=old_info,
            new_info=new_info,
            schema_key="conversation_schema",
            version_key="conversation_version",
            migrations_key="conversation_migrations",
        )
    )

    if errors:
        for error in errors:
            print(f"::error title=Settings schema::{error}")
        return 1

    print("Settings schema migration check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
