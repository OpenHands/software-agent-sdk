#!/usr/bin/env python3
"""REST API breakage detection for openhands-agent-server using oasdiff.

This script compares the current OpenAPI schema for the agent-server REST API against
an already-published release. The baseline version is selected from PyPI, but the
baseline schema is generated from the matching git tag under the current workspace's
locked dependency set. This keeps the comparison focused on API changes in our code,
not schema drift from newer FastAPI/Pydantic releases.

The deprecation note it recognizes intentionally matches the phrasing used by the
Python deprecation checks, for example:

    Deprecated since v1.14.0 and scheduled for removal in v1.19.0.

Policies enforced:

1) REST deprecations must use FastAPI/OpenAPI metadata
   - FastAPI route handlers must not use `openhands.sdk.utils.deprecation.deprecated`.
   - Endpoints documented as deprecated in their OpenAPI description must also be
     marked `deprecated: true` in the generated schema.

2) Deprecation runway before removal
   - If a REST operation (path + HTTP method) or schema property is removed, it
     must have been marked `deprecated: true` in the baseline release and its
     OpenAPI description must declare a scheduled removal version that has been
     reached by the current package version.

3) Additive response oneOf/anyOf expansion is allowed
   - Adding new members to ``oneOf`` or ``anyOf`` discriminated unions in response
     schemas is a normal evolution for extensible event-stream APIs.  Clients MUST
     handle unknown discriminator values gracefully (skip/ignore).
   - oasdiff flags ``response-body-one-of-added`` and
     ``response-property-one-of-added`` as ERR; this script downgrades them to
     informational notices.

4) No in-place contract breakage
   - Breaking REST contract changes that are not removals of previously-deprecated
     operations/properties or additive oneOf expansions fail the check. REST clients
     need 5 minor releases of runway, so incompatible replacements must ship
     additively or behind a versioned contract until the scheduled removal version.

If the baseline release schema can't be generated (e.g., missing tag / repo issues),
the script emits a warning and exits successfully to avoid flaky CI.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

from packaging import version as pkg_version


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_SERVER_PYPROJECT = REPO_ROOT / "openhands-agent-server" / "pyproject.toml"
PYPI_DISTRIBUTION = "openhands-agent-server"
# Keep this in sync with REST_ROUTE_DEPRECATION_RE in check_deprecations.py so
# the REST breakage and deprecation checks recognize the same wording.
REST_ROUTE_DEPRECATION_RE = re.compile(
    r"Deprecated since v(?P<deprecated>[0-9A-Za-z.+-]+)\s+"
    r"and scheduled for removal in v(?P<removed>[0-9A-Za-z.+-]+)\.?",
    re.IGNORECASE,
)
HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "options",
    "head",
    "trace",
}
ROUTE_DECORATOR_NAMES = HTTP_METHODS | {"api_route"}
OPENAPI_PROGRAM = """
import json
import sys
from pathlib import Path

source_tree = Path(sys.argv[1])
sys.path = [
    str(source_tree / "openhands-agent-server"),
    str(source_tree / "openhands-sdk"),
    str(source_tree / "openhands-tools"),
    str(source_tree / "openhands-workspace"),
] + sys.path

from openhands.agent_server.api import create_app

print(json.dumps(create_app().openapi()))
"""


def _read_version_from_pyproject(pyproject: Path) -> str:
    data = tomllib.loads(pyproject.read_text())
    try:
        return str(data["project"]["version"])
    except KeyError as exc:  # pragma: no cover
        raise SystemExit(
            f"Unable to determine project version from {pyproject}"
        ) from exc


def _fetch_pypi_metadata(distribution: str) -> dict:
    req = urllib.request.Request(
        url=f"https://pypi.org/pypi/{distribution}/json",
        headers={"User-Agent": "openhands-agent-server-openapi-check/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def _get_baseline_version(distribution: str, current: str) -> str | None:
    try:
        meta = _fetch_pypi_metadata(distribution)
    except Exception as exc:  # pragma: no cover
        print(
            f"::warning title={distribution} REST API::Failed to fetch PyPI metadata: "
            f"{exc}"
        )
        return None

    releases = list(meta.get("releases", {}).keys())
    if not releases:
        return None

    if current in releases:
        return current

    current_parsed = pkg_version.parse(current)
    older = [rv for rv in releases if pkg_version.parse(rv) < current_parsed]
    if not older:
        return None

    return max(older, key=pkg_version.parse)


def _generate_openapi_from_source_tree(source_tree: Path, label: str) -> dict | None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", OPENAPI_PROGRAM, str(source_tree)],
            check=True,
            capture_output=True,
            text=True,
            cwd=source_tree,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        excerpt = output.strip()[-1000:]
        print(
            f"::warning title={PYPI_DISTRIBUTION} REST API::Failed to generate "
            f"OpenAPI schema for {label}: {exc}\n{excerpt}"
        )
        return None
    except Exception as exc:
        print(
            f"::warning title={PYPI_DISTRIBUTION} REST API::Failed to generate "
            f"OpenAPI schema for {label}: {exc}"
        )
        return None


def _generate_current_openapi() -> dict | None:
    return _generate_openapi_from_source_tree(REPO_ROOT, "current workspace")


def _generate_openapi_for_git_ref(git_ref: str) -> dict | None:
    with tempfile.TemporaryDirectory(prefix="agent-server-openapi-") as tmp:
        source_tree = Path(tmp)

        try:
            archive = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "archive", git_ref],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["tar", "-x", "-C", str(source_tree)],
                check=True,
                input=archive.stdout,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            output = (exc.stdout or b"") + (b"\n" + exc.stderr if exc.stderr else b"")
            excerpt = output.decode(errors="replace").strip()[-1000:]
            print(
                f"::warning title={PYPI_DISTRIBUTION} REST API::Failed to extract "
                f"source for {git_ref}: {exc}\n{excerpt}"
            )
            return None

        return _generate_openapi_from_source_tree(source_tree, git_ref)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        if prefix is None:
            return None
        return f"{prefix}.{node.attr}"
    return None


def _find_sdk_deprecated_fastapi_routes_in_file(
    file_path: Path, repo_root: Path
) -> list[str]:
    tree = ast.parse(file_path.read_text(), filename=str(file_path))

    deprecated_names: set[str] = set()
    deprecation_module_names: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module == "openhands.sdk.utils.deprecation":
                for alias in node.names:
                    if alias.name == "deprecated":
                        deprecated_names.add(alias.asname or alias.name)
            elif node.module == "openhands.sdk.utils":
                for alias in node.names:
                    if alias.name == "deprecation":
                        deprecation_module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "openhands.sdk.utils.deprecation":
                    deprecation_module_names.add(alias.asname or alias.name)

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        has_route_decorator = False
        uses_sdk_deprecated = False

        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue

            dotted_name = _dotted_name(decorator.func)
            if (
                isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in ROUTE_DECORATOR_NAMES
            ):
                has_route_decorator = True

            if dotted_name in deprecated_names or (
                dotted_name == "openhands.sdk.utils.deprecation.deprecated"
            ):
                uses_sdk_deprecated = True
                continue

            if (
                isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "deprecated"
            ):
                base_name = _dotted_name(decorator.func.value)
                if base_name in deprecation_module_names or (
                    base_name == "openhands.sdk.utils.deprecation"
                ):
                    uses_sdk_deprecated = True

        if has_route_decorator and uses_sdk_deprecated:
            rel_path = file_path.relative_to(repo_root).as_posix()
            errors.append(
                f"{rel_path}:{node.lineno} FastAPI route `{node.name}` uses "
                "openhands.sdk.utils.deprecation.deprecated; use the route "
                "decorator's deprecated=True flag instead."
            )

    return errors


def _find_sdk_deprecated_fastapi_routes(repo_root: Path) -> list[str]:
    app_root = repo_root / "openhands-agent-server" / "openhands" / "agent_server"
    errors: list[str] = []

    for file_path in sorted(app_root.rglob("*.py")):
        errors.extend(_find_sdk_deprecated_fastapi_routes_in_file(file_path, repo_root))

    return errors


def _find_deprecation_policy_errors(schema: dict) -> list[str]:
    errors: list[str] = []

    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue

        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue

            description = operation.get("description") or ""
            if "deprecated since" not in description.lower():
                continue

            if operation.get("deprecated") is True:
                continue

            errors.append(
                f"{method.upper()} {path} documents deprecation in its "
                "description but is not marked deprecated=true in OpenAPI."
            )

    return errors


def _parse_openapi_deprecation_description(
    description: str | None,
) -> tuple[str, str] | None:
    """Extract ``(deprecated_in, removed_in)`` from an OpenAPI description.

    The accepted wording intentionally matches ``check_deprecations.py`` so both
    CI checks recognize the same note, for example:

        Deprecated since v1.14.0 and scheduled for removal in v1.19.0.
    """
    if not description:
        return None

    match = REST_ROUTE_DEPRECATION_RE.search(" ".join(description.split()))
    if match is None:
        return None

    return match.group("deprecated").rstrip("."), match.group("removed").rstrip(".")


def _version_ge(current: str, target: str) -> bool:
    try:
        return pkg_version.parse(current) >= pkg_version.parse(target)
    except pkg_version.InvalidVersion as exc:
        raise SystemExit(
            f"Invalid semantic version comparison: {current=} {target=}"
        ) from exc


def _get_openapi_operation(schema: dict, path: str, method: str) -> dict | None:
    path_item = schema.get("paths", {}).get(path)
    if not isinstance(path_item, dict):
        return None

    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return None

    return operation


def _validate_removed_operations(
    removed_operations: list[dict],
    prev_schema: dict,
    current_version: str,
) -> list[str]:
    """Validate removed operations against the baseline deprecation metadata."""
    errors: list[str] = []

    for operation in removed_operations:
        path = str(operation.get("path", ""))
        method = str(operation.get("method", "")).lower()
        method_label = method.upper() or "<unknown method>"

        if not operation.get("deprecated", False):
            errors.append(
                f"Removed {method_label} {path} without prior deprecation "
                "(deprecated=true)."
            )
            continue

        baseline_operation = _get_openapi_operation(prev_schema, path, method)
        if baseline_operation is None:
            errors.append(
                f"Removed {method_label} {path} was marked deprecated in the "
                "baseline release, but the previous OpenAPI schema could not be "
                "inspected for its scheduled removal version."
            )
            continue

        deprecation_details = _parse_openapi_deprecation_description(
            baseline_operation.get("description")
        )
        if deprecation_details is None:
            errors.append(
                f"Removed {method_label} {path} was marked deprecated in the "
                "baseline release, but its OpenAPI description does not declare "
                "a scheduled removal version. REST API removals require 5 minor "
                "releases of deprecation runway."
            )
            continue

        _, removed_in = deprecation_details
        if not _version_ge(current_version, removed_in):
            errors.append(
                f"Removed {method_label} {path} before its scheduled removal "
                f"version v{removed_in} (current version: v{current_version}). "
                "REST API removals require 5 minor releases of deprecation "
                "runway."
            )
            continue

        print(
            f"::notice title={PYPI_DISTRIBUTION} REST API::Removed previously-"
            f"deprecated {method_label} {path} after its scheduled removal "
            f"version v{removed_in}."
        )

    return errors


def _iter_schema_properties(schema: dict):
    if not isinstance(schema, dict):
        return

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for property_name, property_schema in properties.items():
            if isinstance(property_schema, dict):
                yield property_name, property_schema

    for value in schema.values():
        if isinstance(value, dict):
            yield from _iter_schema_properties(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from _iter_schema_properties(item)


def _removed_property_name(change: dict) -> str | None:
    text = str(change.get("text", ""))
    match = re.search(r"(?:request property|optional property) `([^`]+)`", text)
    if match is None:
        return None
    return match.group(1).rstrip("/").rsplit("/", maxsplit=1)[-1]


def _validate_removed_schema_properties(
    removed_properties: list[dict],
    prev_schema: dict,
    current_version: str,
) -> list[str]:
    """Validate removed schema properties against baseline deprecation metadata."""
    errors: list[str] = []
    baseline_properties: dict[str, list[dict]] = {}
    for property_name, property_schema in _iter_schema_properties(prev_schema):
        baseline_properties.setdefault(property_name, []).append(property_schema)

    for change in removed_properties:
        property_name = _removed_property_name(change)
        if property_name is None:
            errors.append(
                "Removed schema property could not be identified from oasdiff output: "
                f"{change.get('text', str(change))}"
            )
            continue

        deprecated_candidates = [
            property_schema
            for property_schema in baseline_properties.get(property_name, [])
            if property_schema.get("deprecated") is True
        ]
        if not deprecated_candidates:
            errors.append(
                f"Removed schema property {property_name!r} without prior "
                "deprecation (deprecated=true)."
            )
            continue

        removal_targets = [
            deprecation_details[1]
            for property_schema in deprecated_candidates
            if (
                deprecation_details := _parse_openapi_deprecation_description(
                    property_schema.get("description")
                )
            )
            is not None
        ]
        if not removal_targets:
            errors.append(
                f"Removed schema property {property_name!r} was marked deprecated "
                "in the baseline release, but its OpenAPI description does not "
                "declare a scheduled removal version. REST API property removals "
                "require 5 minor releases of deprecation runway."
            )
            continue

        if not any(
            _version_ge(current_version, removed_in) for removed_in in removal_targets
        ):
            errors.append(
                f"Removed schema property {property_name!r} before its scheduled "
                f"removal version(s): {', '.join(f'v{v}' for v in removal_targets)} "
                f"(current version: v{current_version}). REST API property removals "
                "require 5 minor releases of deprecation runway."
            )
            continue

        print(
            f"::notice title={PYPI_DISTRIBUTION} REST API::Removed previously-"
            f"deprecated schema property {property_name!r} after its scheduled "
            "removal version was reached."
        )

    return errors


# oasdiff rule IDs for additive oneOf/anyOf expansion in response schemas.
# These are flagged as ERR by oasdiff but are expected evolution for extensible
# discriminated-union APIs (e.g. the events endpoint).  We downgrade them to
# informational notices so they don't block CI.
_ADDITIVE_RESPONSE_ONEOF_IDS = frozenset(
    {
        "response-body-one-of-added",
        "response-property-one-of-added",
        # Keep the anyOf variants here too so that if oasdiff ever reports them
        # as breakages, additive response-union expansion gets the same
        # downgrade without further script changes.
        "response-body-any-of-added",
        "response-property-any-of-added",
    }
)

# Breaking changes allowed for the settings persistence feature (PR #3026).
# Making `agent` and `workspace` optional in StartConversationRequest enables
# the server to build these from persisted settings. This is a backward-compatible
# change: existing clients that provide these fields continue to work, while new
# clients can omit them to use server-side defaults.
#
# oasdiff flags these as breaking because:
# 1. The property type changes from `object` to `anyOf[object, null]`
# 2. Nested properties appear "removed" from the old path structure
#
# NOTE: These patterns are for PR #3026 (making agent/workspace optional).
# They use prefix matching because the anyOf wrapper causes oasdiff to report
# ALL nested properties as "removed" - there's no practical way to enumerate
# every possible nested path.
#
# IMPORTANT: Any genuine breaking changes to agent/ or workspace/ fields
# should go through the normal deprecation process with explicit annotations.
# Review changes carefully during code review - don't rely solely on CI.
#
# Pattern design rationale:
# - Type/format changes are explicit (top-level only)
# - Nested property patterns use prefix match because oasdiff reports the
#   entire nested structure as removed when parent becomes anyOf[object, null]
# - If you're modifying agent or workspace schema, verify changes manually
#
# WARNING: These patterns are intentionally broad to handle oasdiff's behavior
# of reporting ALL nested paths when a parent field's type changes. This means:
# 1. A genuine removal of a nested agent/workspace field WILL be allowlisted
# 2. Code reviewers MUST manually verify schema changes to these fields
# 3. When in doubt, add explicit test coverage for the schema change
_OPTIONAL_AGENT_WORKSPACE_PATTERNS = (
    # Type changes for making agent/workspace optional (top-level only)
    "the `agent` request property type/format changed",
    "the `workspace` request property type/format changed",
    # Nested properties reported as "removed" due to anyOf wrapper.
    # Prefix match is intentional - oasdiff reports ALL nested paths.
    # REVIEWER NOTE: If you're genuinely removing a field under agent/ or
    # workspace/, this will be silently allowlisted. Double-check schema diffs.
    "removed the request property `agent/",
    "removed the request property `workspace/",
    # Optional nested properties (oasdiff sometimes uses this format)
    "removed the optional property `agent/",
    "removed the optional property `workspace/",
)

# Separate allowlist for upstream deprecations unrelated to optional args.
# These are breaking changes from upstream dependencies that we accept.
_UPSTREAM_DEPRECATION_PATTERNS = (
    # LLM safety_settings was removed in litellm - not related to optional args
    "removed the request property `llm/safety_settings`",
)


def _is_allowed_optional_request_change(change_text: str) -> bool:
    """Check if a breaking change is allowed as part of optional request args."""
    return any(
        pattern in change_text for pattern in _OPTIONAL_AGENT_WORKSPACE_PATTERNS
    ) or any(pattern in change_text for pattern in _UPSTREAM_DEPRECATION_PATTERNS)


def _split_breaking_changes(
    breaking_changes: list[dict],
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Split oasdiff results into allowlisted buckets and other breakages."""
    removed_operations: list[dict] = []
    removed_schema_properties: list[dict] = []
    additive_response_oneof: list[dict] = []
    optional_request_changes: list[dict] = []
    other_breaking_changes: list[dict] = []

    for change in breaking_changes:
        change_id = str(change.get("id", ""))
        change_text = str(change.get("text", ""))
        details = change.get("details", {})

        # Check for allowed optional request property changes first
        if _is_allowed_optional_request_change(change_text):
            optional_request_changes.append(change)
            continue

        if "removed" in change_id.lower() and "operation" in change_id.lower():
            removed_operations.append(
                {
                    "path": details.get("path", ""),
                    "method": details.get("method", ""),
                    "deprecated": details.get("deprecated", False),
                }
            )
            continue

        if "removed" in change_id.lower() and "property" in change_id.lower():
            removed_schema_properties.append(change)
            continue

        if change_id in _ADDITIVE_RESPONSE_ONEOF_IDS:
            additive_response_oneof.append(change)
            continue

        other_breaking_changes.append(change)

    return (
        removed_operations,
        removed_schema_properties,
        additive_response_oneof,
        optional_request_changes,
        other_breaking_changes,
    )


def _normalize_openapi_for_oasdiff(schema: dict) -> dict:
    """Normalize OpenAPI 3.1 schema for oasdiff compatibility.

    oasdiff expects OpenAPI 3.0-style exclusiveMinimum/exclusiveMaximum booleans
    (https://spec.openapis.org/oas/v3.0.3.html#schema-object), while OpenAPI 3.1
    emits numeric values. Convert numeric exclusives into minimum/maximum +
    exclusive boolean flags so oasdiff can parse the schema.

    Mutates the schema in place and returns it for convenience.
    """

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            if (
                "exclusiveMinimum" in node
                and isinstance(node["exclusiveMinimum"], (int, float))
                and not isinstance(node["exclusiveMinimum"], bool)
            ):
                value = node["exclusiveMinimum"]
                if "minimum" not in node:
                    node["minimum"] = value
                node["exclusiveMinimum"] = True
            if (
                "exclusiveMaximum" in node
                and isinstance(node["exclusiveMaximum"], (int, float))
                and not isinstance(node["exclusiveMaximum"], bool)
            ):
                value = node["exclusiveMaximum"]
                if "maximum" not in node:
                    node["maximum"] = value
                node["exclusiveMaximum"] = True

            for child in node.values():
                _walk(child)
        elif isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(schema)
    return schema


def _run_oasdiff_breakage_check(
    prev_spec: Path, cur_spec: Path
) -> tuple[list[dict], int]:
    """Run oasdiff breaking check between two OpenAPI specs.

    Returns (list of breaking changes, exit code from oasdiff).
    """
    try:
        result = subprocess.run(
            [
                "oasdiff",
                "breaking",
                "-f",
                "json",
                "--fail-on",
                "ERR",
                str(prev_spec),
                str(cur_spec),
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print(
            "::warning title=oasdiff not found::"
            "Please install oasdiff: https://github.com/oasdiff/oasdiff"
        )
        return [], 0

    breaking_changes = []
    if result.stdout:
        try:
            breaking_changes = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass

    return breaking_changes, result.returncode


def main() -> int:
    current_version = _read_version_from_pyproject(AGENT_SERVER_PYPROJECT)
    baseline_version = _get_baseline_version(PYPI_DISTRIBUTION, current_version)

    if baseline_version is None:
        print(
            f"::warning title={PYPI_DISTRIBUTION} REST API::Unable to find baseline "
            f"version for {current_version}; skipping breakage checks."
        )
        return 0

    baseline_git_ref = f"v{baseline_version}"

    static_policy_errors = _find_sdk_deprecated_fastapi_routes(REPO_ROOT)
    for error in static_policy_errors:
        print(f"::error title={PYPI_DISTRIBUTION} REST API::{error}")

    current_schema = _generate_current_openapi()
    if current_schema is None:
        return 1

    deprecation_policy_errors = _find_deprecation_policy_errors(current_schema)
    for error in deprecation_policy_errors:
        print(f"::error title={PYPI_DISTRIBUTION} REST API::{error}")

    prev_schema = _generate_openapi_for_git_ref(baseline_git_ref)
    if prev_schema is None:
        return 0 if not (static_policy_errors or deprecation_policy_errors) else 1

    prev_schema = _normalize_openapi_for_oasdiff(prev_schema)
    current_schema = _normalize_openapi_for_oasdiff(current_schema)

    with tempfile.TemporaryDirectory(prefix="oasdiff-specs-") as tmp:
        tmp_path = Path(tmp)
        prev_spec_file = tmp_path / "prev_spec.json"
        cur_spec_file = tmp_path / "cur_spec.json"

        prev_spec_file.write_text(json.dumps(prev_schema, indent=2))
        cur_spec_file.write_text(json.dumps(current_schema, indent=2))

        breaking_changes, exit_code = _run_oasdiff_breakage_check(
            prev_spec_file, cur_spec_file
        )

    if not breaking_changes:
        if exit_code == 0:
            print("No breaking changes detected.")
        else:
            print(
                f"oasdiff returned exit code {exit_code} but no breaking changes "
                "in JSON format. There may be warnings only."
            )
    else:
        (
            removed_operations,
            removed_schema_properties,
            additive_response_oneof,
            optional_request_changes,
            other_breaking_changes,
        ) = _split_breaking_changes(breaking_changes)
        removal_errors = _validate_removed_operations(
            removed_operations,
            prev_schema,
            current_version,
        )
        property_removal_errors = _validate_removed_schema_properties(
            removed_schema_properties,
            prev_schema,
            current_version,
        )

        for error in removal_errors + property_removal_errors:
            print(f"::error title={PYPI_DISTRIBUTION} REST API::{error}")

        if additive_response_oneof:
            print(
                f"\n::notice title={PYPI_DISTRIBUTION} REST API::"
                "Additive oneOf/anyOf expansion detected in response schemas. "
                "This is expected for extensible discriminated-union APIs and "
                "does not break backward compatibility."
            )
            for item in additive_response_oneof:
                print(f"  - {item.get('text', str(item))}")

        if optional_request_changes:
            print(
                f"\n::notice title={PYPI_DISTRIBUTION} REST API::"
                "Optional request property changes detected. Making agent/workspace "
                "optional in StartConversationRequest is backward-compatible: existing "
                "clients that provide these fields continue to work."
            )
            for item in optional_request_changes:
                print(f"  - {item.get('text', str(item))}")

        if other_breaking_changes:
            print(
                "::error "
                f"title={PYPI_DISTRIBUTION} REST API::Detected breaking REST API "
                "changes other than removing previously-deprecated operations/"
                "properties or additive response oneOf expansions. "
                "REST contract changes must preserve compatibility for 5 minor "
                "releases; keep the old contract available until its scheduled "
                "removal version."
            )

        print("\nBreaking REST API changes detected compared to baseline release:")
        for text in breaking_changes:
            print(f"- {text.get('text', str(text))}")

        if not (removal_errors or property_removal_errors or other_breaking_changes):
            print(
                "Breaking changes are limited to previously-deprecated operations "
                "or properties whose scheduled removal versions have been reached, "
                "and/or additive response oneOf expansions."
            )
        else:
            return 1

    return 1 if (static_policy_errors or deprecation_policy_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
