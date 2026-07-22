"""Query the sanitized OSS-5715 diagnostic events from PostHog.

This is the *only* module that talks to PostHog, and it is written so that no
untrusted byte can steer the query and no PII column can be selected:

* The HogQL is **fixed** apart from a single integer time window; there is no
  string interpolation of any external value (the old prototype interpolated a
  ``--query`` argument straight into the SQL).
* The projection is an explicit **column allowlist**
  (:data:`sanitize.ALLOWED_EVENT_PROPERTY_NAMES`); ``distinct_id``,
  ``person_id`` and the raw ``properties`` blob are never named.
* Every returned row is re-validated through :meth:`SanitizedError.from_row`
  and checked by :func:`assert_no_pii_keys` before it is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import requests
from fingerprint import FingerprintGroup, SanitizedError, aggregate
from sanitize import (
    ALLOWED_EVENT_PROPERTY_NAMES,
    assert_no_pii_keys,
)


#: The sanitized error events emitted by the agent-server telemetry exporter.
ERROR_EVENT_NAMES: Final[tuple[str, ...]] = (
    "agent_server.conversation_error",
    "agent_server.conversation_failed",
    "agent_server.request_failed",
)

#: Schema version this workflow understands (matches TELEMETRY_SCHEMA_VERSION).
SUPPORTED_SCHEMA_VERSION: Final[int] = 1

_TIMESTAMP_COLUMN: Final[str] = "timestamp"


@dataclass(frozen=True, slots=True)
class TelemetryQueryConfig:
    api_key: str
    project_id: str
    host: str = "us.posthog.com"
    days_back: int = 7
    limit: int = 1000
    timeout: int = 120


def _build_hogql(days_back: int, limit: int) -> str:
    """Assemble the fixed projection query.

    ``days_back`` and ``limit`` are the only variables and are coerced to
    non-negative ints by the caller, so no attacker-controlled text reaches the
    query text. Event names come from :data:`ERROR_EVENT_NAMES`, not from input.
    """
    days = int(days_back)
    row_limit = int(limit)
    columns = ", ".join(
        f"properties.{name} AS {name}" for name in ALLOWED_EVENT_PROPERTY_NAMES
    )
    event_list = ", ".join(f"'{name}'" for name in ERROR_EVENT_NAMES)
    return (
        f"SELECT {_TIMESTAMP_COLUMN}, {columns} "
        f"FROM events "
        f"WHERE event IN ({event_list}) "
        f"AND properties.schema_version = {SUPPORTED_SCHEMA_VERSION} "
        f"AND timestamp > now() - INTERVAL {days} DAY "
        f"ORDER BY timestamp DESC "
        f"LIMIT {row_limit}"
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rows_from_response(payload: dict[str, Any]) -> list[dict[str, object]]:
    """Turn PostHog's columnar HogQL response into plain dict rows.

    Only the columns we asked for come back, but we still route every row
    through :func:`assert_no_pii_keys` as defence in depth.
    """
    columns = payload.get("columns") or []
    results = payload.get("results") or []
    rows: list[dict[str, object]] = []
    for raw in results:
        row = {col: raw[idx] for idx, col in enumerate(columns) if idx < len(raw)}
        assert_no_pii_keys(row)
        rows.append(row)
    return rows


def fetch_error_groups(
    config: TelemetryQueryConfig,
    *,
    session: requests.Session | None = None,
) -> list[FingerprintGroup]:
    """Fetch sanitized error events and collapse them into fingerprint groups.

    Raises on transport/HTTP errors so the caller can fail the run loudly; a
    partial or malformed row simply degrades to ``None`` and is dropped.
    """
    http = session or requests.Session()
    url = f"https://{config.host}/api/projects/{config.project_id}/query/"
    body = {
        "query": {
            "kind": "HogQLQuery",
            "query": _build_hogql(config.days_back, config.limit),
        },
        "refresh": "blocking",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }
    response = http.post(url, headers=headers, json=body, timeout=config.timeout)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"PostHog query error: {payload['error']}")

    errors: list[SanitizedError] = []
    for row in _rows_from_response(payload):
        occurred_at = _parse_timestamp(row.get(_TIMESTAMP_COLUMN))
        error = SanitizedError.from_row(row, occurred_at=occurred_at)
        if error is not None:
            errors.append(error)
    return aggregate(errors)
