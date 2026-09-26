#!/usr/bin/env python3
"""Verify that an unlocked dependency resolution for openhands-tools does not
pick a browser-use version that is incompatible with the fastmcp/mcp versions
that openhands-sdk resolves to.

This is the CI gate for issue #5152: before the fix, an unlocked resolve
produced browser-use==0.11.13 + mcp==2.x, which caused the browser-use MCP
server to raise AttributeError at import time, silently dropping all browser
tools from the LLM.

The check extracts the browser-use constraint directly from the local
openhands-tools/pyproject.toml, then runs `uv pip compile` without the
workspace lockfile and asserts:
  1. browser-use is pinned below 0.12 in pyproject (our explicit cap).
  2. The resolved browser-use + mcp combination is not the #5152 broken pair.

Exit 0 = OK, exit 1 = drift detected.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_PYPROJECT = REPO_ROOT / "openhands-tools" / "pyproject.toml"


def _check_pyproject_constraint() -> None:
    """Assert browser-use<0.12 is declared in openhands-tools/pyproject.toml."""
    text = TOOLS_PYPROJECT.read_text()
    # Look for browser-use with an upper bound < 0.12
    m = re.search(r'"browser-use[^"]*"', text)
    if not m:
        print("ERROR: browser-use dependency not found in openhands-tools/pyproject.toml",
              file=sys.stderr)
        sys.exit(1)
    spec = m.group(0)
    # Must contain <0.12 (or tighter)
    if "<0.12" not in spec and "<0.11" not in spec:
        print(
            f"ERROR: browser-use spec '{spec}' in openhands-tools/pyproject.toml "
            "does not contain an upper bound <0.12. "
            "The #5152 regression guard requires 'browser-use<0.12'.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  pyproject constraint: {spec.strip('\"')} ✓")


def _compile(python_version: str = "3.12") -> str:
    """Run `uv pip compile` without lockfile and return stdout.

    Reads constraints from a temporary requirements file pointing at the local
    pyproject.toml files so the check reflects the working-tree constraints,
    not the last-published PyPI release.
    """
    import tempfile

    # Write a minimal requirements.in that lists the two packages whose combined
    # dependency set triggered #5152.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".in", delete=False
    ) as req_file:
        req_path = Path(req_file.name)
        req_file.write(
            f"openhands-tools @ {TOOLS_PYPROJECT.parent.as_uri()}\n"
            f"openhands-agent-server @ "
            f"{(REPO_ROOT / 'openhands-agent-server').as_uri()}\n"
        )

    try:
        result = subprocess.run(
            [
                "uv",
                "pip",
                "compile",
                "--python-version",
                python_version,
                "--no-header",
                "--quiet",
                str(req_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
        )
    finally:
        req_path.unlink(missing_ok=True)

    if result.returncode != 0:
        print("uv pip compile failed:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout


def _parse_version(output: str, package: str) -> str | None:
    """Extract the pinned version for *package* from compile output."""
    m = re.search(
        rf"^{re.escape(package)}==([^\s]+)",
        output,
        re.MULTILINE | re.IGNORECASE,
    )
    return m.group(1) if m else None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")[:3])


def main() -> None:
    print("Checking unlocked browser-use / mcp resolve (issue #5152 guard)...")

    # Step 1: static constraint check (fast, no network)
    _check_pyproject_constraint()

    # Step 2: runtime resolve check (requires network)
    output = _compile()

    browser_use_ver = _parse_version(output, "browser-use")
    mcp_ver = _parse_version(output, "mcp")

    print(f"  resolved browser-use: {browser_use_ver or 'not found'}")
    print(f"  resolved mcp:         {mcp_ver or 'not found'}")

    errors: list[str] = []

    if browser_use_ver is not None:
        bv = _version_tuple(browser_use_ver)
        if bv >= (0, 12):
            errors.append(
                f"browser-use {browser_use_ver} >= 0.12 was resolved; "
                "the 'browser-use<0.12' cap in openhands-tools/pyproject.toml "
                "was not effective. Check if the constraint was removed."
            )

        if mcp_ver is not None:
            mv = _version_tuple(mcp_ver)
            if bv < (0, 12) and mv >= (2, 0):
                errors.append(
                    f"browser-use {browser_use_ver} (mcp 1.x only) resolved "
                    f"together with mcp {mcp_ver} — this is the #5152 "
                    "silent-disappearance combination. "
                    "Check fastmcp and browser-use upper bounds."
                )

    if errors:
        print(
            "\nERROR: Unlocked resolve detected incompatible combination:",
            file=sys.stderr,
        )
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        sys.exit(1)

    print("✓ Unlocked resolve is safe.")


if __name__ == "__main__":
    main()
