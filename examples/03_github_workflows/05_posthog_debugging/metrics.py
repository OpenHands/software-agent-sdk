"""Append-only pilot metrics, written to an artifact for human review.

The pilot is judged on *safety*, not fix rate, so each run records the outcome
of every fingerprint it touched; the README explains how to read the aggregate
(precision, duplicate, remediation and false-positive rates). Everything written
here is a validated token or a scalar -- never event content.
"""

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
    """Append one JSON line, creating the file and parent dir if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
