#!/usr/bin/env python3
"""REST API breakage detection for openhands-agent-server.

This script compares the current OpenAPI schema for the agent-server REST API against
the previous published version on PyPI.

Policies enforced (mirrors the SDK's Griffe checks, but for REST):

1) Deprecation-before-removal
   - If a REST operation (path + HTTP method) is removed, it must have been marked
     `deprecated: true` in the previous release.

2) MINOR version bump
   - If a breaking REST change is detected, the current version must be at least a
     MINOR bump compared to the previous release.

The breakage detection currently focuses on compatibility rules that are robust to
OpenAPI generation changes:
- Removed operations
- New required parameters
- Request bodies that became required
- New required fields in JSON request bodies (best-effort)

If the previous release schema can't be fetched (e.g., network / PyPI issues), the
script emits a warning and exits successfully to avoid flaky CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from packaging import version as pkg_version


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_SERVER_PYPROJECT = REPO_ROOT / "openhands-agent-server" / "pyproject.toml"
PYPI_DISTRIBUTION = "openhands-agent-server"


_HTTP_METHODS = (
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
)


@dataclass(frozen=True, slots=True)
class OperationKey:
    method: str
    path: str


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


def _get_previous_version(distribution: str, current: str) -> str | None:
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

    current_parsed = pkg_version.parse(current)
    older = [rv for rv in releases if pkg_version.parse(rv) < current_parsed]
    if not older:
        return None

    return max(older, key=pkg_version.parse)


def _generate_current_openapi() -> dict:
    from openhands.agent_server.api import create_app

    return create_app().openapi()


def _generate_openapi_for_version(version: str) -> dict | None:
    """Generate OpenAPI schema for a published agent-server version.

    Returns None on failure so callers can treat it as a best-effort comparison.
    """

    with tempfile.TemporaryDirectory(prefix="agent-server-openapi-") as tmp:
        venv_dir = Path(tmp) / ".venv"
        python = venv_dir / "bin" / "python"

        try:
            subprocess.run(
                [
                    "uv",
                    "venv",
                    str(venv_dir),
                    "--python",
                    sys.executable,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            openhands_packages = (
                "openhands-agent-server",
                "openhands-sdk",
                "openhands-tools",
                "openhands-workspace",
            )
            packages = [f"{name}=={version}" for name in openhands_packages]

            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    *packages,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            program = (
                "import json; "
                "from openhands.agent_server.api import create_app; "
                "print(json.dumps(create_app().openapi()))"
            )
            result = subprocess.run(
                [str(python), "-c", program],
                check=True,
                capture_output=True,
                text=True,
            )
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as exc:
            output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
            excerpt = output.strip()[-1000:]
            print(
                f"::warning title={PYPI_DISTRIBUTION} REST API::Failed to generate "
                f"OpenAPI schema for v{version}: {exc}\n{excerpt}"
            )
            return None
        except Exception as exc:
            print(
                f"::warning title={PYPI_DISTRIBUTION} REST API::Failed to generate "
                f"OpenAPI schema for v{version}: {exc}"
            )
            return None


def _iter_operations(schema: dict) -> Iterable[tuple[OperationKey, dict]]:
    paths: dict = schema.get("paths", {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict):
                yield OperationKey(method=method, path=path), operation


def _required_parameters(operation: dict) -> set[tuple[str, str]]:
    required: set[tuple[str, str]] = set()
    for param in operation.get("parameters", []) or []:
        if not isinstance(param, dict):
            continue
        if not param.get("required"):
            continue
        name = param.get("name")
        location = param.get("in")
        if isinstance(name, str) and isinstance(location, str):
            required.add((name, location))
    return required


def _resolve_ref(schema: dict, spec: dict, *, max_depth: int = 50) -> dict:
    current = schema
    seen: set[str] = set()
    depth = 0

    while isinstance(current, dict) and "$ref" in current:
        ref = current["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return current
        if ref in seen or depth >= max_depth:
            return current

        seen.add(ref)
        depth += 1

        target: object = spec
        for part in ref.removeprefix("#/").split("/"):
            if not isinstance(target, dict) or part not in target:
                return current
            target = target[part]
        if not isinstance(target, dict):
            return current
        current = target

    return current


def _required_json_fields(operation: dict, spec: dict) -> set[str]:
    request_body = operation.get("requestBody") or {}
    if not isinstance(request_body, dict):
        return set()

    content = request_body.get("content") or {}
    if not isinstance(content, dict):
        return set()

    json_content = content.get("application/json")
    if not isinstance(json_content, dict):
        return set()

    schema = json_content.get("schema")
    if not isinstance(schema, dict):
        return set()

    return _required_json_fields_from_schema(schema, spec)


def _required_json_fields_from_schema(schema: dict, spec: dict) -> set[str]:
    resolved = _resolve_ref(schema, spec)

    if "allOf" in resolved and isinstance(resolved["allOf"], list):
        required: set[str] = set()
        for item in resolved["allOf"]:
            if isinstance(item, dict):
                required |= _required_json_fields_from_schema(item, spec)
        return required

    if resolved.get("type") != "object":
        return set()

    required = resolved.get("required")
    if not isinstance(required, list):
        return set()

    return {field for field in required if isinstance(field, str)}


def _is_request_body_required(operation: dict) -> bool:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return False
    return bool(request_body.get("required"))


def _is_minor_or_major_bump(current: str, previous: str) -> bool:
    cur = pkg_version.parse(current)
    prev = pkg_version.parse(previous)
    if cur <= prev:
        return False
    return (cur.major, cur.minor) != (prev.major, prev.minor)


def _compute_breakages(
    prev_schema: dict, current_schema: dict
) -> tuple[list[str], list[OperationKey]]:
    prev_ops = dict(_iter_operations(prev_schema))
    cur_ops = dict(_iter_operations(current_schema))

    removed = set(prev_ops).difference(cur_ops)

    undeprecated_removals: list[OperationKey] = []
    for key in sorted(removed, key=lambda k: (k.path, k.method)):
        if not prev_ops[key].get("deprecated"):
            undeprecated_removals.append(key)

    breaking_reasons: list[str] = []

    if removed:
        breaking_reasons.append(f"Removed operations: {len(removed)}")

    for key, prev_op in prev_ops.items():
        cur_op = cur_ops.get(key)
        if cur_op is None:
            continue

        new_required_params = _required_parameters(cur_op) - _required_parameters(
            prev_op
        )
        if new_required_params:
            formatted = ", ".join(
                sorted(f"{n}({loc})" for n, loc in new_required_params)
            )
            breaking_reasons.append(
                f"{key.method.upper()} {key.path}: new required params: {formatted}"
            )

        if _is_request_body_required(cur_op) and not _is_request_body_required(prev_op):
            breaking_reasons.append(
                f"{key.method.upper()} {key.path}: request body became required"
            )

        prev_required_fields = _required_json_fields(prev_op, prev_schema)
        cur_required_fields = _required_json_fields(cur_op, current_schema)
        new_required_fields = cur_required_fields - prev_required_fields
        if new_required_fields:
            formatted = ", ".join(sorted(new_required_fields))
            breaking_reasons.append(
                f"{key.method.upper()} {key.path}: "
                f"new required JSON fields: {formatted}"
            )

    return breaking_reasons, undeprecated_removals


def main() -> int:
    current_version = _read_version_from_pyproject(AGENT_SERVER_PYPROJECT)
    prev_version = _get_previous_version(PYPI_DISTRIBUTION, current_version)

    if prev_version is None:
        print(
            f"::warning title={PYPI_DISTRIBUTION} REST API::Unable to find previous "
            f"version for {current_version}; skipping breakage checks."
        )
        return 0

    prev_schema = _generate_openapi_for_version(prev_version)
    if prev_schema is None:
        return 0

    current_schema = _generate_current_openapi()

    breaking_reasons, undeprecated_removals = _compute_breakages(
        prev_schema, current_schema
    )

    if undeprecated_removals:
        for key in undeprecated_removals:
            print(
                "::error "
                f"title={PYPI_DISTRIBUTION} REST API::Removed {key.method.upper()} "
                f"{key.path} without prior deprecation (deprecated=true)."
            )

    breaking = bool(breaking_reasons)

    if breaking and not _is_minor_or_major_bump(current_version, prev_version):
        print(
            "::error "
            f"title={PYPI_DISTRIBUTION} REST API::Breaking REST API change detected "
            f"without MINOR version bump ({prev_version} -> {current_version})."
        )

    if breaking:
        print("Breaking REST API changes detected compared to previous release:")
        for reason in breaking_reasons:
            print(f"- {reason}")

    errors = bool(undeprecated_removals) or (
        breaking and not _is_minor_or_major_bump(current_version, prev_version)
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
