"""Shared helpers for the release security-scan checks.

Deterministic-first by design: these helpers only read git history and query
read-only HTTP APIs (GitHub REST, OSV). They never execute the code under
review. That is the whole point of a security gate — a scanner that runs (or
asks an LLM to reason over) untrusted PR content is itself an injection target.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field


GITHUB_API = "https://api.github.com"


def run_git(*args: str) -> str:
    """Run a git command and return stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def last_release_tag() -> str | None:
    """Most recent ``vX.Y.Z`` tag reachable from HEAD, or None if there is none.

    The release scan compares "what ships now" against the last thing that
    shipped, so the previous release tag is the natural, immutable baseline.
    """
    try:
        # --merged HEAD keeps us on this line of history; sort by version.
        tags = run_git(
            "tag", "--list", "v*", "--merged", "HEAD", "--sort=-v:refname"
        ).splitlines()
    except subprocess.CalledProcessError:
        return None
    return tags[0].strip() if tags and tags[0].strip() else None


def github_request(path_or_url: str, token: str | None) -> object:
    """GET a GitHub REST endpoint and parse JSON. Raises on HTTP error."""
    url = path_or_url
    if url.startswith("/"):
        url = f"{GITHUB_API}{url}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def osv_query_batch(
    queries: list[dict[str, object]],
) -> list[dict[str, object]] | None:
    """Query the OSV batched vulnerability API. Returns None on network failure.

    OSV is public and needs no auth. Network failure is treated as a warning by
    callers (do not block a release on a flaky lookup), while an actual
    known-vulnerable hit is a hard finding.
    """
    payload = json.dumps({"queries": queries}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    results = data.get("results")
    return results if isinstance(results, list) else []


@dataclass
class Report:
    """Accumulates a markdown report plus a blocking/ok verdict."""

    title: str
    lines: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, line: str = "") -> None:
        self.lines.append(line)

    def block(self, reason: str) -> None:
        self.blocking.append(reason)

    def warn(self, reason: str) -> None:
        self.warnings.append(reason)

    def render(self) -> str:
        if self.blocking:
            status = f"❌ {len(self.blocking)} blocking finding(s)"
        elif self.warnings:
            status = f"⚠️ {len(self.warnings)} warning(s), nothing blocking"
        else:
            status = "✅ no findings"
        out = [f"### {self.title}", "", f"**{status}**", ""]
        out.extend(self.lines)
        return "\n".join(out).rstrip() + "\n"


def resolve_repo() -> str:
    """`owner/name` from GITHUB_REPOSITORY, falling back to the git remote."""
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    if env_repo:
        return env_repo
    url = run_git("config", "--get", "remote.origin.url")
    slug = url.rsplit("github.com", 1)[-1].lstrip(":/")
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug
