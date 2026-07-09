"""Release security check #2 — supply-chain dependency diff.

Compares the resolved dependency set (``uv.lock``) between the last release tag
and HEAD, then for everything new or bumped:

  * looks it up in **OSV** for known vulnerabilities (blocking on a hit), and
  * flags packages resolved from a **non-PyPI source** — git URLs, direct
    URLs, or alternative indexes — which is the classic supply-chain and
    dependency-confusion surface (blocking; a release should pin to the
    trusted registry).

Deterministic: parses the two lockfile revisions from git and queries the
public OSV API. It never installs or executes any dependency.

Env:
  SECURITY_SCAN_BASELINE_TAG  (optional; overrides the auto-detected last tag)
  SECURITY_SCAN_OUTPUT        (optional; append the rendered report here too)
"""

from __future__ import annotations

import os
import sys
import tomllib

from security_scan_common import (
    Report,
    last_release_tag,
    osv_query_batch,
    run_git,
)


LOCKFILE = "uv.lock"


def _load_lock(ref: str) -> dict[str, dict[str, object]]:
    """Return {name: {version, source}} for every package in ``ref``'s lock."""
    try:
        raw = run_git("show", f"{ref}:{LOCKFILE}")
    except Exception:  # noqa: BLE001
        return {}
    data = tomllib.loads(raw)
    packages: dict[str, dict[str, object]] = {}
    for pkg in data.get("package", []):
        name = pkg.get("name")
        if not name:
            continue
        packages[name] = {
            "version": pkg.get("version", "?"),
            "source": pkg.get("source", {}),
        }
    return packages


def _source_label(source: object) -> str:
    if not isinstance(source, dict):
        return str(source)
    # uv encodes the source as {registry: ...} | {git: ...} | {url: ...} |
    # {editable: ...} | {virtual: ...} | {path: ...}
    for key in ("git", "url", "path", "editable", "virtual"):
        if key in source:
            return f"{key}:{source[key]}"
    if "registry" in source:
        return f"registry:{source['registry']}"
    return str(source)


def _is_trusted_registry(source: object) -> bool:
    """A first-party workspace member or a plain PyPI registry pin is trusted."""
    if not isinstance(source, dict):
        return False
    if "registry" in source:
        return "pypi.org" in str(source["registry"])
    # Workspace members / virtual roots are the SDK's own packages.
    return "virtual" in source or "editable" in source


def main() -> int:
    baseline = os.environ.get("SECURITY_SCAN_BASELINE_TAG") or last_release_tag()
    report = Report("📦 Supply-chain dependency diff")

    if not baseline:
        report.add("No previous `v*` release tag found; nothing to compare against.")
        print(report.render())
        return 0

    try:
        run_git("rev-parse", "--verify", f"{baseline}^{{commit}}")
    except Exception:  # noqa: BLE001
        report.warn("baseline unavailable")
        report.add(
            f"Baseline tag `{baseline}` is not present locally "
            "(fetch tags with `fetch-depth: 0`). Skipping."
        )
        print(report.render())
        return 0

    before = _load_lock(baseline)
    after = _load_lock("HEAD")
    if not after:
        report.warn("no lockfile")
        report.add(f"Could not read `{LOCKFILE}` at HEAD. Skipping.")
        print(report.render())
        return 0

    added = {n: after[n] for n in after.keys() - before.keys()}
    removed = sorted(before.keys() - after.keys())
    bumped = {
        n: (before[n]["version"], after[n]["version"])
        for n in after.keys() & before.keys()
        if before[n]["version"] != after[n]["version"]
    }

    report.add(f"Baseline: `{baseline}`")
    report.add(
        f"Added: **{len(added)}**, bumped: **{len(bumped)}**, "
        f"removed: **{len(removed)}**."
    )
    report.add("")

    # --- non-registry sources on new/changed packages (supply-chain surface) --
    suspicious_source = []
    for name in list(added) + list(bumped):
        source = after[name]["source"]
        if not _is_trusted_registry(source):
            suspicious_source.append((name, _source_label(source)))
    if suspicious_source:
        report.add("**Non-PyPI sources on new/changed packages:**")
        for name, label in sorted(suspicious_source):
            report.add(f"- `{name}` ← `{label}`")
            report.block(f"{name} resolved from non-registry source ({label})")
        report.add("")

    # --- OSV known-vulnerability lookup on new + bumped versions ---------------
    to_scan = [(n, str(after[n]["version"])) for n in list(added) + list(bumped)]
    if to_scan:
        queries = [
            {"package": {"ecosystem": "PyPI", "name": n}, "version": v}
            for n, v in to_scan
        ]
        results = osv_query_batch(queries)
        if results is None:
            report.warn("OSV lookup failed")
            report.add(
                "> OSV lookup failed (network); reported as a warning, not a "
                "blocker. Re-run to retry."
            )
        else:
            vuln_hits = []
            for (name, version), res in zip(to_scan, results):
                vulns = res.get("vulns") if isinstance(res, dict) else None
                if vulns:
                    ids = ", ".join(v.get("id", "?") for v in vulns)
                    vuln_hits.append((name, version, ids))
            if vuln_hits:
                report.add("**Known vulnerabilities (OSV):**")
                for name, version, ids in vuln_hits:
                    report.add(f"- `{name}=={version}` → {ids}")
                    report.block(f"{name}=={version} has known vuln(s): {ids}")
                report.add("")
            else:
                report.add(
                    f"OSV: no known vulns across {len(to_scan)} new/bumped dep(s)."
                )
                report.add("")

    # --- readable inventory ---------------------------------------------------
    if added:
        report.add("<details><summary>Added dependencies</summary>\n")
        for name in sorted(added):
            report.add(f"- `{name}=={added[name]['version']}`")
        report.add("\n</details>")
    if bumped:
        report.add("<details><summary>Bumped dependencies</summary>\n")
        for name in sorted(bumped):
            old, new = bumped[name]
            report.add(f"- `{name}`: {old} → {new}")
        report.add("\n</details>")

    sys.stdout.write(report.render())
    out = os.environ.get("SECURITY_SCAN_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(report.render())
            handle.write("\n")
    return 1 if report.blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
