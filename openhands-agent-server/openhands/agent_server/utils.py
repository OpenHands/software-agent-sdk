import logging
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import PlainSerializer


logger = logging.getLogger(__name__)


def safe_rmtree(path: str | Path | None, description: str = "directory") -> bool:
    """Safely remove a directory tree, handling permission errors gracefully.

    Args:
        path: Path to the directory to remove
        description: Description of what's being removed (for logging)

    Returns:
        bool: True if removal was successful, False if it failed
    """
    if not path or not os.path.exists(path):
        return True

    def handle_remove_readonly(func, path, _exc):
        """Error handler for removing read-only files."""
        if os.path.exists(path):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except (OSError, PermissionError) as e:
                logger.warning(f"Failed to remove read-only file {path}: {e}")

    try:
        shutil.rmtree(path, onerror=handle_remove_readonly)
        logger.debug(f"Successfully removed {description}: {path}")
        return True
    except (OSError, PermissionError) as e:
        logger.warning(
            f"Failed to remove {description} at {path}: {e}. "
            f"This may leave temporary files on disk but won't affect functionality."
        )
        return False
    except Exception as e:
        logger.error(f"Unexpected error removing {description} at {path}: {e}")
        return False


def utc_now():
    """Return the current time in UTC format (Since datetime.utcnow is deprecated)"""
    return datetime.now(UTC)


def _extract_discriminated_unions(schema: dict) -> dict:
    """Extract inline discriminated unions as separate components.

    Recursively scans the schema and extracts any inline discriminated union
    (oneOf + discriminator + title) as a separate component, replacing it with a $ref.
    Also deduplicates schemas with identical titles.
    """
    import json
    import re
    from collections import defaultdict

    if not isinstance(schema, dict):
        return schema

    # OpenAPI schema names must match this pattern
    valid_name_pattern = re.compile(r"^[a-zA-Z0-9._-]+$")

    schemas = schema.get("components", {}).get("schemas", {})
    extracted = {}

    def _find_and_extract(obj, path=""):
        if not isinstance(obj, dict):
            return obj

        # Extract inline discriminated unions
        if "oneOf" in obj and "discriminator" in obj and "title" in obj:
            title = obj["title"]
            if (
                title not in schemas
                and title not in extracted
                and valid_name_pattern.match(title)
            ):
                extracted[title] = {
                    "oneOf": obj["oneOf"],
                    "discriminator": obj["discriminator"],
                    "title": title,
                }
                return {"$ref": f"#/components/schemas/{title}"}

        # Recursively process nested structures
        result = {}
        for key, value in obj.items():
            if isinstance(value, dict):
                result[key] = _find_and_extract(value, f"{path}.{key}")
            elif isinstance(value, list):
                result[key] = [
                    _find_and_extract(item, f"{path}.{key}[]") for item in value
                ]
            else:
                result[key] = value
        return result

    schema = _find_and_extract(schema)

    if extracted and "components" in schema and "schemas" in schema["components"]:
        schema["components"]["schemas"].update(extracted)

    # Deduplicate schemas with same title (prefer *-Output over *-Input over base)
    schemas = schema.get("components", {}).get("schemas", {})
    title_to_names = defaultdict(list)
    for name, defn in schemas.items():
        if isinstance(defn, dict):
            title_to_names[defn.get("title", name)].append(name)

    to_remove = {}
    for title, names in title_to_names.items():
        if len(names) > 1:
            # Prefer: *-Output > *-Input > base name
            keep = sorted(
                names,
                key=lambda n: (
                    0 if n.endswith("-Output") else 1 if n.endswith("-Input") else 2,
                    n,
                ),
            )[0]
            for name in names:
                if name != keep:
                    to_remove[name] = keep

    if to_remove:
        schema_str = json.dumps(schema)
        for old, new in to_remove.items():
            schema_str = schema_str.replace(
                f'"#/components/schemas/{old}"', f'"#/components/schemas/{new}"'
            )
        schema = json.loads(schema_str)
        for old in to_remove:
            schema["components"]["schemas"].pop(old, None)

    return schema


def patch_fastapi_discriminated_union_support():
    """Patch FastAPI to handle discriminated union schemas without $ref.

    This ensures discriminated unions from DiscriminatedUnionMixin work correctly
    with FastAPI's OpenAPI schema generation. The patch prevents KeyError when
    FastAPI encounters schemas without $ref keys (which discriminated unions use).

    Also extracts inline discriminated unions as separate schema components for
    better OpenAPI documentation and Swagger UI display.
    """
    try:
        import fastapi._compat.v2 as fastapi_v2
        from fastapi import FastAPI

        _original_remap = fastapi_v2._remap_definitions_and_field_mappings

        def _patched_remap_definitions_and_field_mappings(**kwargs):
            """Patched version that handles schemas w/o $ref (discriminated unions)."""
            field_mapping = kwargs.get("field_mapping", {})
            model_name_map = kwargs.get("model_name_map", {})

            # Build old_name -> new_name map, skipping schemas without $ref
            old_name_to_new_name_map = {}
            for field_key, schema in field_mapping.items():
                model = field_key[0].type_
                if model not in model_name_map:
                    continue
                new_name = model_name_map[model]

                # Skip schemas without $ref (discriminated unions)
                if "$ref" not in schema:
                    continue

                old_name = schema["$ref"].split("/")[-1]
                if old_name in {f"{new_name}-Input", f"{new_name}-Output"}:
                    continue
                old_name_to_new_name_map[old_name] = new_name

            # Replace refs using FastAPI's helper
            from fastapi._compat.v2 import _replace_refs

            new_field_mapping = {}
            for field_key, schema in field_mapping.items():
                new_schema = _replace_refs(
                    schema=schema,
                    old_name_to_new_name_map=old_name_to_new_name_map,
                )
                new_field_mapping[field_key] = new_schema

            definitions = kwargs.get("definitions", {})
            new_definitions = {}
            for key, value in definitions.items():
                new_key = old_name_to_new_name_map.get(key, key)
                new_value = _replace_refs(
                    schema=value,
                    old_name_to_new_name_map=old_name_to_new_name_map,
                )
                new_definitions[new_key] = new_value

            return new_field_mapping, new_definitions

        # Apply the patch
        fastapi_v2._remap_definitions_and_field_mappings = (
            _patched_remap_definitions_and_field_mappings
        )

        # Patch FastAPI.openapi() to extract discriminated unions
        _original_openapi = FastAPI.openapi

        def _patched_openapi(self):
            """Patched openapi() that extracts discriminated unions."""
            schema = _original_openapi(self)
            return _extract_discriminated_unions(schema)

        FastAPI.openapi = _patched_openapi

    except (ImportError, AttributeError):
        # FastAPI not available or internal API changed
        pass


def _uuid_to_hex(uuid_obj: UUID) -> str:
    """Converts a UUID object to a hex string without hyphens."""
    return uuid_obj.hex


OpenHandsUUID = Annotated[UUID, PlainSerializer(_uuid_to_hex, when_used="json")]
