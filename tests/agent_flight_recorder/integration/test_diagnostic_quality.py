import json
from pathlib import Path

from flight_recorder.diagnostics.rules import deterministic_findings
from flight_recorder.models.envelopes import Record


CORPUS = Path(__file__).parents[1] / "fixtures" / "traces" / "diagnostics"


def test_deterministic_detector_quality_and_evidence_validity() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    outcomes: dict[str, list[tuple[bool, bool]]] = {}
    for case in manifest["cases"]:
        records = [
            Record.model_validate_json(line)
            for line in (CORPUS / case["file"]).read_text().splitlines()
        ]
        findings = deterministic_findings(records)
        detected = any(finding.detector_id == case["detector"] for finding in findings)
        outcomes.setdefault(case["detector"], []).append((case["positive"], detected))
        record_ids = {record.record_id for record in records}
        assert all(set(finding.evidence_ids) <= record_ids for finding in findings)

    for results in outcomes.values():
        true_positives = sum(expected and detected for expected, detected in results)
        false_positives = sum(
            not expected and detected for expected, detected in results
        )
        false_negatives = sum(
            expected and not detected for expected, detected in results
        )
        precision = true_positives / (true_positives + false_positives)
        recall = true_positives / (true_positives + false_negatives)
        assert precision >= 0.9
        assert recall >= 0.9
