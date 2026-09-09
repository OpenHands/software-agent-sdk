from pathlib import Path

from flight_recorder.diagnostics.analyzer import AnalyzerFailure, InterpretiveAnalyzer
from flight_recorder.diagnostics.rules import deterministic_findings
from flight_recorder.diagnostics.schemas import EvidencePacket
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import Finding


class DiagnosticService:
    def __init__(
        self,
        index: TraceIndex,
        analyzer: InterpretiveAnalyzer | None = None,
        findings_path: Path | None = None,
    ) -> None:
        self.index = index
        self.analyzer = analyzer
        self.findings_path = findings_path
        self.analyzer_failure: AnalyzerFailure | None = None

    def analyze(
        self, trace_id: str, include_interpretive: bool = False
    ) -> list[Finding]:
        records = self.index.iter_records(trace_id)
        findings = deterministic_findings(records)
        packet = EvidencePacket(
            trace_id=trace_id,
            records=tuple(records),
            deterministic_findings=tuple(findings),
        )
        self.analyzer_failure = None
        if include_interpretive and self.analyzer is not None:
            try:
                findings.extend(self.analyzer.analyze(packet))
            except AnalyzerFailure as exc:
                self.analyzer_failure = exc
        valid = self._valid_findings(trace_id, records, findings)
        self._persist(valid)
        return valid

    @staticmethod
    def _valid_findings(
        trace_id: str, records: list, findings: list[Finding]
    ) -> list[Finding]:
        record_ids = {record.record_id for record in records}
        return [
            finding
            for finding in findings
            if finding.trace_id == trace_id
            and set(finding.evidence_ids).issubset(record_ids)
        ]

    def _persist(self, findings: list[Finding]) -> None:
        if self.findings_path is None:
            return
        self.findings_path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(f"{finding.model_dump_json()}\n" for finding in findings)
        temporary = self.findings_path.with_suffix(".tmp")
        temporary.write_text(content)
        temporary.replace(self.findings_path)
