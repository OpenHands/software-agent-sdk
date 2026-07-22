"""Operational safety rails: kill switch, cooldown, rate limit.

The hard guarantees (draft-only, verify-before-PR, scoped tokens) live in the
workflow topology. These are the *soft* rails that keep a recurring loop from
misbehaving at scale, written as pure functions over
:class:`issue_tracker.SelfHealState` so they are unit-testable without hitting
GitHub. Duplicate-run protection is handled by the workflow's ``concurrency:``
group, so it needs no code here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fingerprint import Disposition
from issue_tracker import SelfHealState


KILL_SWITCH_ENV = "POSTHOG_SELFHEAL_ENABLED"


def kill_switch_enabled(env: Mapping[str, str]) -> bool:
    """The loop is *off* unless explicitly enabled. Fails safe (disabled)."""
    return env.get(KILL_SWITCH_ENV, "").strip().lower() == "true"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def in_cooldown(state: SelfHealState, now: datetime, window_hours: float) -> bool:
    """True if this fingerprint was remediated too recently to retry."""
    last = _parse_iso(state.last_remediation_at)
    if last is None:
        return False
    return now - last < timedelta(hours=window_hours)


def is_terminal(state: SelfHealState) -> bool:
    """A fingerprint with an open PR is never re-picked."""
    return state.disposition == Disposition.PR_OPEN.value


def select_within_budget(items: list[Any], max_per_run: int) -> tuple[list[Any], int]:
    """Truncate the eligible list to the per-run rate limit.

    Returns ``(kept, dropped_count)`` so the caller can log what was skipped --
    a silent cap would read as "nothing else to do".
    """
    if max_per_run < 0 or len(items) <= max_per_run:
        return items, 0
    return items[:max_per_run], len(items) - max_per_run
