#!/usr/bin/env python3
"""REST API breakage detection for openhands-agent-server using oasdiff.

This script compares the current OpenAPI schema for the agent-server REST API against
an already-published release. The baseline version is selected from PyPI, but the
baseline schema is generated from the matching git tag under the current workspace's
locked dependency set. This keeps the comparison focused on API changes in our code,
not schema drift from newer FastAPI/Pydantic releases.

Policies enforced:

1) REST deprecations must use FastAPI/OpenAPI metadata
   - FastAPI route handlers must not use `openhands.sdk.utils.deprecation.deprecated`.
   - Endpoints documented as deprecated in their OpenAPI description must also be
     marked `deprecated: true` in the generated schema.

2) Deprecation-before-removal
   - If a REST operation (path + HTTP method) is removed, it must have been marked
     `deprecated: true` in the baseline release.

3) MINOR version bump
   - If a breaking REST change is detected, the current version must be at least a
     MINOR bump compared to the baseline release.

If the baseline release schema can't be generated (e.g., missing tag / repo issues),
the script emits a warning and exits successfully to avoid flaky CI.
"""

from __future__ import annotations

import ast
import json
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
            rel_path = file_path.relative_to(repo_root)
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


def _is_minor_or_major_bump(current: str, previous: str) -> bool:
    cur = pkg_version.parse(current)
    prev = pkg_version.parse(previous)
    if cur <= prev:
        return False
    return (cur.major, cur.minor) != (prev.major, prev.minor)


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
        removed_operations = []

        for change in breaking_changes:
            change_id = change.get("id", "")
            details = change.get("details", {})

            if "removed" in change_id.lower() and "operation" in change_id.lower():
                removed_operations.append(
                    {
                        "path": details.get("path", ""),
                        "method": details.get("method", ""),
                        "deprecated": details.get("deprecated", False),
                    }
                )

        undeprecated_removals = [
            op for op in removed_operations if not op.get("deprecated", False)
        ]

        for op in undeprecated_removals:
            print(
                f"::error title={PYPI_DISTRIBUTION} REST API::Removed "
                f"{op['method'].upper()} {op['path']} without prior deprecation "
                "(deprecated=true)."
            )

        if not _is_minor_or_major_bump(current_version, baseline_version):
            print(
                "::error "
                f"title={PYPI_DISTRIBUTION} REST API::Breaking REST API change "
                f"detected without MINOR version bump ({baseline_version} -> "
                f"{current_version})."
            )

        print("\nBreaking REST API changes detected compared to baseline release:")
        for text in breaking_changes:
            print(f"- {text.get('text', str(text))}")

        if undeprecated_removals or not _is_minor_or_major_bump(
            current_version, baseline_version
        ):
            return 1

    return 1 if (static_policy_errors or deprecation_policy_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
