"""Append-only pilot metrics, written to an artifact for human review.

The pilot is judged on *safety*, not fix rate (the sanitized events are too thin
to reproduce most bugs). So each run records the outcome of every fingerprint it
touched, and the README explains how to read the aggregate: precision =
verified-and-correct PRs / PRs opened, duplicate rate, remediation rate, and any
false-positive (a PR that a human judged wrong). Everything written here is a
validated token or a scalar -- never event content.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


Outcome = Literal[
    "issue_created",
    "issue_updated",
    "skipped_ineligible",
    "skipped_cooldown",
    "skipped_locked",
    "skipped_rate_limited",
    "verified_pr_opened",
    "verification_failed",
    "false_positive",
]


@dataclass(frozen=True, slots=True)
class MetricRecord:
    run_id: str
    occurred_at: str
    dedup_key: str
    error_class: str
    outcome: Outcome
    detail: str = ""


def record(path: Path, entry: MetricRecord) -> None:
    """Append one JSON line. Creates the file/parent dir if needed.

    The pilot rates (precision, duplicate, remediation, false-positive) are
    computed by eye from these rows -- the volume is small and the outcomes are
    the ones the issue asks to record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
