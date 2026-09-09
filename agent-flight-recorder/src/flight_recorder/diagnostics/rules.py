"""Repeatable evidence-backed diagnostic rules."""

import json
from collections import defaultdict
from hashlib import sha256
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from flight_recorder.models.envelopes import Finding, Record


def fingerprint(record: Record) -> str:
    payload = {
        key: value
        for key, value in record.payload.items()
        if key not in {"timestamp", "id"}
    }
    normalized = json.dumps([record.kind, payload], sort_keys=True, default=str)
    return sha256(normalized.encode()).hexdigest()


def _finding(
    detector_id: str,
    records: list[Record],
    *,
    category: str,
    title: str,
    explanation: str,
    recommendation: str,
    severity: Literal["info", "warning", "error"] = "warning",
) -> Finding:
    evidence_ids = tuple(record.record_id for record in records)
    identity = f"{records[0].trace_id}:{detector_id}:{':'.join(evidence_ids)}"
    return Finding(
        finding_id=str(uuid5(NAMESPACE_URL, identity)),
        trace_id=records[0].trace_id,
        origin="rule",
        detector_id=detector_id,
        category=category,
        severity=severity,
        confidence=1.0,
        title=title,
        explanation=explanation,
        recommendation=recommendation,
        evidence_ids=evidence_ids,
        affected_span_ids=tuple(
            dict.fromkeys(record.span_id for record in records if record.span_id)
        ),
    )


def repeated_tool_calls(records: list[Record], threshold: int = 2) -> list[Finding]:
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        if record.kind == "tool.request":
            groups[fingerprint(record)].append(record)
    findings = []
    for matching in groups.values():
        if len(matching) < threshold:
            continue
        findings.append(
            _finding(
                "repeated-tool-call",
                matching,
                category="loop",
                title="Repeated tool call",
                explanation=f"Equivalent tool call repeated {len(matching)} times.",
                recommendation="Review the latest result before retrying.",
            )
        )
    return findings


def recurring_failures(records: list[Record], threshold: int = 2) -> list[Finding]:
    groups: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        if record.kind.endswith(".error") or record.payload.get("status") == "failed":
            groups[fingerprint(record)].append(record)
    return [
        _finding(
            "recurring-failure",
            matching,
            category="tool",
            title="Recurring failure",
            explanation=f"Equivalent failure recurred {len(matching)} times.",
            recommendation="Change the approach before retrying the failed activity.",
            severity="error",
        )
        for matching in groups.values()
        if len(matching) >= threshold
    ]


def no_progress_iterations(records: list[Record], threshold: int = 3) -> list[Finding]:
    findings = []
    unchanged = []
    for record in records:
        if record.kind != "iteration.finished":
            continue
        if record.payload.get("made_progress") is False:
            unchanged.append(record)
            continue
        if len(unchanged) >= threshold:
            findings.append(_no_progress_finding(unchanged))
        unchanged = []
    if len(unchanged) >= threshold:
        findings.append(_no_progress_finding(unchanged))
    return findings


def _no_progress_finding(records: list[Record]) -> Finding:
    return _finding(
        "no-progress",
        records,
        category="loop",
        title="Iterations without progress",
        explanation=f"{len(records)} consecutive iterations reported no progress.",
        recommendation="Reassess the plan or stop the repeated approach.",
    )


def context_issues(records: list[Record]) -> list[Finding]:
    affected = [
        record
        for record in records
        if record.kind == "context.reconstructed"
        and record.provenance.complete is False
    ]
    if not affected:
        return []
    return [
        _finding(
            "incomplete-context",
            affected,
            category="context",
            title="Incomplete reconstructed context",
            explanation="Model context is reconstructed with missing evidence.",
            recommendation="Capture authoritative completion request logs.",
        )
    ]


def delegation_issues(records: list[Record]) -> list[Finding]:
    affected = [
        record
        for record in records
        if record.kind == "agent.finished"
        and record.payload.get("result") in {None, ""}
    ]
    if not affected:
        return []
    return [
        _finding(
            "empty-delegation-result",
            affected,
            category="delegation",
            title="Delegation returned no result",
            explanation="Delegated work finished without a recorded result.",
            recommendation="Clarify the handoff and required return value.",
        )
    ]


def premature_finish(records: list[Record]) -> list[Finding]:
    failed_tests = [
        record
        for record in records
        if record.kind == "test.finished" and record.payload.get("status") == "failed"
    ]
    finished = [record for record in records if record.kind == "run.finished"]
    if not failed_tests or not finished:
        return []
    return [
        _finding(
            "premature-finish",
            [failed_tests[-1], finished[-1]],
            category="termination",
            title="Run finished after failed tests",
            explanation="The run ended while the latest recorded test was failing.",
            recommendation="Resolve or explicitly acknowledge the failed test.",
        )
    ]


def budget_hotspots(records: list[Record], cost_limit: float = 10.0) -> list[Finding]:
    affected = []
    for record in records:
        if record.kind != "metrics.snapshot":
            continue
        snapshots = record.payload.get("usage_to_metrics", {})
        if isinstance(snapshots, dict) and any(
            isinstance(value, dict)
            and isinstance(value.get("accumulated_cost"), int | float)
            and float(value["accumulated_cost"]) >= cost_limit
            for value in snapshots.values()
        ):
            affected.append(record)
    if not affected:
        return []
    return [
        _finding(
            "cost-hotspot",
            affected,
            category="cost",
            title="Cost hotspot",
            explanation=f"Recorded cost reached at least ${cost_limit:.2f}.",
            recommendation="Review high-cost model activity and context size.",
        )
    ]


def deterministic_findings(records: list[Record]) -> list[Finding]:
    detectors = (
        repeated_tool_calls,
        recurring_failures,
        no_progress_iterations,
        context_issues,
        delegation_issues,
        premature_finish,
        budget_hotspots,
    )
    return [finding for detector in detectors for finding in detector(records)]
