#!/usr/bin/env python3
"""REST API breakage detection for openhands-agent-server using oasdiff.

This script compares the current OpenAPI schema for the agent-server REST API against
the previous published version on PyPI, using oasdiff for breaking change detection.

Policies enforced (mirrors the SDK's Griffe checks, but for REST):

1) Deprecation-before-removal
   - If a REST operation (path + HTTP method) is removed, it must have been marked
     `deprecated: true` in the previous release.

2) MINOR version bump
   - If a breaking REST change is detected, the current version must be at least a
     MINOR bump compared to the previous release.

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
from pathlib import Path

from packaging import version as pkg_version


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_SERVER_PYPROJECT = REPO_ROOT / "openhands-agent-server" / "pyproject.toml"
PYPI_DISTRIBUTION = "openhands-agent-server"


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
        return 0

    removed_operations = []
    other_breakage = []

    for change in breaking_changes:
        change_id = change.get("id", "")
        text = change.get("text", "")
        details = change.get("details", {})

        if "removed" in change_id.lower() and "operation" in change_id.lower():
            path = details.get("path", "")
            method = details.get("method", "")
            operation_id = details.get("operationId", "")
            deprecated = details.get("deprecated", False)

            removed_operations.append(
                {
                    "path": path,
                    "method": method,
                    "operationId": operation_id,
                    "deprecated": deprecated,
                }
            )
        else:
            other_breakage.append(text)

    undeprecated_removals = [
        op for op in removed_operations if not op.get("deprecated", False)
    ]

    if undeprecated_removals:
        for op in undeprecated_removals:
            print(
                f"::error "
                f"title={PYPI_DISTRIBUTION} REST API::Removed {op['method'].upper()} "
                f"{op['path']} without prior deprecation (deprecated=true)."
            )

    has_breaking = bool(breaking_changes)

    if has_breaking and not _is_minor_or_major_bump(current_version, prev_version):
        print(
            "::error "
            f"title={PYPI_DISTRIBUTION} REST API::Breaking REST API change detected "
            f"without MINOR version bump ({prev_version} -> {current_version})."
        )

    if has_breaking:
        print("\nBreaking REST API changes detected compared to previous release:")
        for text in breaking_changes:
            print(f"- {text.get('text', str(text))}")

    errors = bool(undeprecated_removals) or (
        has_breaking and not _is_minor_or_major_bump(current_version, prev_version)
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
