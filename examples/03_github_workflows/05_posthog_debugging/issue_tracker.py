"""One tracking issue per ``dedup_key``, and its embedded machine-readable state.

GitHub Actions has no shared mutable store, so the tracking issue *is* the
durable state: a hidden marker locates it (never the title, which is
human-editable), and a JSON comment carries the disposition, attempt count and
cooldown timestamp.

The rendered body is redaction-safe by construction -- built only from the
validated tokens on a :class:`FingerprintGroup`, so no ``distinct_id``,
``person_id``, message or raw payload can appear (the prototype leaked all of
these).
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Final, Self

import requests
from fingerprint import Disposition, FingerprintGroup


_GITHUB_API: Final[str] = "https://api.github.com"

#: Locates the issue for a dedup_key. Searched via the code/issue search API.
_KEY_MARKER: Final[str] = "<!-- selfheal-key:{key} -->"
#: Carries the JSON state block.
_STATE_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--\s*selfheal-state:(\{.*?\})\s*-->", re.DOTALL
)


@dataclass(slots=True)
class SelfHealState:
    """Durable per-fingerprint state, serialized into the issue body."""

    disposition: str = Disposition.NEW.value
    attempt_count: int = 0
    last_remediation_at: str | None = None

    def to_comment(self) -> str:
        return f"<!-- selfheal-state:{json.dumps(asdict(self), sort_keys=True)} -->"

    @classmethod
    def parse(cls, body: str | None) -> Self:
        """Recover state from an issue body, tolerating a missing/garbled block."""
        if not body:
            return cls()
        match = _STATE_RE.search(body)
        if not match:
            return cls()
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def key_marker(dedup_key: str) -> str:
    return _KEY_MARKER.format(key=dedup_key)


def replace_state_in_body(body: str, state: SelfHealState) -> str:
    """Swap only the state block, leaving the rest of the body untouched.

    Lets a cooldown update rewrite state without re-rendering the whole body
    from a group. Appends the block if none is present yet.
    """
    comment = state.to_comment()
    if _STATE_RE.search(body):
        return _STATE_RE.sub(lambda _: comment, body, count=1)
    return f"{body}\n\n{comment}"


def _fmt_dt(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "unknown"


def render_body(group: FingerprintGroup, state: SelfHealState) -> str:
    """Render the redaction-safe issue body from validated tokens only."""
    ctx = group.to_prompt_context()
    releases = ", ".join(f"`{r}`" for r in sorted(group.releases)) or "_none_"
    lines = [
        key_marker(group.dedup_key),
        f"# Self-heal tracking: `{ctx['error_class']}`",
        "",
        "> Automated tracking issue. Do not edit the HTML comments — the "
        "self-healing workflow reads them as state.",
        "",
        "## Summary",
        f"- **Dedup key:** `{group.dedup_key}`",
        f"- **Error class:** `{ctx['error_class']}`",
        f"- **Category:** `{ctx['error_category']}`",
        f"- **Origin module:** `{ctx['error_origin_module']}`",
        f"- **First party:** {ctx['is_first_party']}",
        f"- **Occurrences (window):** {ctx['occurrence_count']}",
        f"- **First seen:** {_fmt_dt(group.first_seen)}",
        f"- **Last seen:** {_fmt_dt(group.last_seen)}",
        f"- **Affected releases:** {releases}",
        f"- **Disposition:** `{state.disposition}`",
        f"- **Remediation attempts:** {state.attempt_count}",
        "",
        "## Evidence fingerprints",
        ", ".join(f"`{fp}`" for fp in sorted(group.fingerprints)[:5]) or "_none_",
        "",
        "---",
        "*No user identifiers, messages, stack traces, or raw payloads are "
        "stored here by policy.*",
        "",
        state.to_comment(),
    ]
    return "\n".join(lines)


def issue_title(group: FingerprintGroup) -> str:
    ctx = group.to_prompt_context()
    return f"[self-heal] {ctx['error_class']} ({group.dedup_key})"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def find_issue(
    repo: str, dedup_key: str, token: str, *, session: requests.Session | None = None
) -> dict[str, Any] | None:
    """Locate the tracking issue by its hidden marker, not its title."""
    http = session or requests.Session()
    marker = key_marker(dedup_key)
    query = f'repo:{repo} is:issue "{marker}" in:body'
    resp = http.get(
        f"{_GITHUB_API}/search/issues",
        headers=_headers(token),
        params={"q": query},
        timeout=30,
    )
    resp.raise_for_status()
    for item in resp.json().get("items", []):
        # Confirm the marker really is present (search is fuzzy).
        if marker in (item.get("body") or ""):
            return item
    return None


def create_issue(
    repo: str,
    group: FingerprintGroup,
    state: SelfHealState,
    token: str,
    *,
    labels: list[str] | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    http = session or requests.Session()
    payload = {
        "title": issue_title(group),
        "body": render_body(group, state),
        "labels": labels or ["self-heal"],
    }
    resp = http.post(
        f"{_GITHUB_API}/repos/{repo}/issues",
        headers=_headers(token),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def update_issue(
    repo: str,
    issue_number: int,
    body: str,
    token: str,
    *,
    session: requests.Session | None = None,
) -> None:
    http = session or requests.Session()
    resp = http.patch(
        f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}",
        headers=_headers(token),
        json={"body": body},
        timeout=30,
    )
    resp.raise_for_status()


def comment(
    repo: str,
    issue_number: int,
    body: str,
    token: str,
    *,
    session: requests.Session | None = None,
) -> None:
    http = session or requests.Session()
    resp = http.post(
        f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}/comments",
        headers=_headers(token),
        json={"body": body},
        timeout=30,
    )
    resp.raise_for_status()
