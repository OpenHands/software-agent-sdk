"""Grouping keys and the aggregated view of a recurring error.

The emission-side ``error_fingerprint`` hashes ``module:lineno`` pairs, so the
*same logical bug* produces a *different* fingerprint after any unrelated edit
that shifts a line. This module therefore derives a second, **line-agnostic**
key -- :func:`dedup_key` -- from just the class, origin module and category.
Tracking, dedup and cooldown all key on it, so one bug maps to one tracking
issue across releases; the exact fingerprints are retained only as evidence.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, Self

from sanitize import (
    UNKNOWN_ERROR_CLASS,
    UNKNOWN_TOKEN,
    safe_bool,
    safe_digest,
    safe_identifier,
    safe_lineno,
    safe_token,
    safe_version,
)


#: Bumping this changes every dedup_key, so raise it only on a deliberate
#: regrouping (it would orphan existing tracking issues onto new keys).
DEDUP_KEY_VERSION: Final[int] = 1


def dedup_key(error_class: str, error_origin_module: str | None, category: str) -> str:
    """A stable, line-agnostic grouping key for one logical error.

    Excludes ``error_origin_lineno`` and the exact ``error_fingerprint`` so the
    key survives refactors that only move code.
    """
    module = error_origin_module or UNKNOWN_TOKEN
    preimage = "|".join([str(DEDUP_KEY_VERSION), error_class, module, category])
    return hashlib.blake2s(preimage.encode("utf-8"), digest_size=8).hexdigest()


class Disposition(StrEnum):
    """Lifecycle of a tracked fingerprint. The label on the tracking issue."""

    NEW = "new"
    INVESTIGATING = "investigating"
    PR_OPEN = "pr-open"


@dataclass(frozen=True, slots=True)
class SanitizedError:
    """One telemetry event, reduced to validated, PII-free scalars.

    Constructed only via :meth:`from_row`, which runs every field through the
    ``sanitize`` coercions, so an instance can never hold free-form text.
    """

    error_fingerprint: str
    error_class: str
    error_category: str
    error_origin_module: str | None
    error_origin_lineno: int | None
    is_first_party: bool
    release_sha: str
    release_version: str
    occurred_at: datetime | None

    @classmethod
    def from_row(
        cls, row: dict[str, object], *, occurred_at: datetime | None = None
    ) -> Self | None:
        """Validate a raw PostHog row, or ``None`` if it lacks a usable class.

        Note what is *not* read: no message, no traceback, no ``distinct_id``.
        """
        error_class = safe_identifier(row.get("error_class"), default="")
        if not error_class:
            return None
        module = safe_identifier(row.get("error_origin_module"), default="")
        return cls(
            error_fingerprint=safe_digest(row.get("error_fingerprint")),
            error_class=error_class,
            error_category=safe_token(row.get("error_category")),
            error_origin_module=module or None,
            error_origin_lineno=safe_lineno(row.get("error_origin_lineno")),
            is_first_party=safe_bool(row.get("is_first_party")),
            release_sha=safe_version(row.get("build_git_sha"), default=""),
            release_version=safe_version(row.get("server_version"), default=""),
            occurred_at=occurred_at,
        )

    @property
    def dedup_key(self) -> str:
        return dedup_key(
            self.error_class, self.error_origin_module, self.error_category
        )


@dataclass(slots=True)
class FingerprintGroup:
    """All occurrences of one logical error, collapsed onto its ``dedup_key``.

    This is what triage turns into (at most) one tracking issue.
    """

    dedup_key: str
    error_class: str
    error_category: str
    error_origin_module: str | None
    is_first_party: bool
    count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    #: Distinct exact fingerprints seen under this key (evidence only).
    fingerprints: set[str] = field(default_factory=set)
    #: Distinct releases affected, as PII-free sha/version tokens.
    releases: set[str] = field(default_factory=set)
    #: The most recent resolvable commit sha, used as the remediation base.
    latest_sha: str = ""

    def observe(self, error: SanitizedError) -> None:
        """Fold one event into the group."""
        self.count += 1
        if error.error_fingerprint:
            self.fingerprints.add(error.error_fingerprint)
        for release in (error.release_version, error.release_sha):
            if release and release != UNKNOWN_TOKEN:
                self.releases.add(release)
        if error.release_sha:
            self.latest_sha = error.release_sha
        if error.occurred_at is not None:
            if self.first_seen is None or error.occurred_at < self.first_seen:
                self.first_seen = error.occurred_at
            if self.last_seen is None or error.occurred_at > self.last_seen:
                self.last_seen = error.occurred_at

    def to_prompt_context(self) -> dict[str, object]:
        """The complete, injection-safe context handed to the agent prompt.

        Every value has passed a ``safe_*`` coercion, so none of it can carry an
        instruction; there is deliberately no message, stack trace, user
        content, ``distinct_id`` or raw payload.
        """
        return {
            "dedup_key": self.dedup_key,
            "error_class": self.error_class or UNKNOWN_ERROR_CLASS,
            "error_category": self.error_category or UNKNOWN_TOKEN,
            "error_origin_module": self.error_origin_module or UNKNOWN_TOKEN,
            "is_first_party": self.is_first_party,
            "occurrence_count": self.count,
            "affected_releases": sorted(self.releases),
            "example_fingerprints": sorted(self.fingerprints)[:5],
        }


def aggregate(errors: list[SanitizedError]) -> list[FingerprintGroup]:
    """Collapse validated events into one group per ``dedup_key``."""
    groups: dict[str, FingerprintGroup] = {}
    for error in errors:
        key = error.dedup_key
        group = groups.get(key)
        if group is None:
            group = FingerprintGroup(
                dedup_key=key,
                error_class=error.error_class,
                error_category=error.error_category,
                error_origin_module=error.error_origin_module,
                is_first_party=error.is_first_party,
            )
            groups[key] = group
        group.observe(error)
    return sorted(groups.values(), key=lambda g: g.count, reverse=True)
