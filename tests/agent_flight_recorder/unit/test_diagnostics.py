from datetime import datetime

import pytest
from flight_recorder.diagnostics.analyzer import AnalyzerFailure, InterpretiveAnalyzer
from flight_recorder.diagnostics.rules import repeated_tool_calls
from flight_recorder.diagnostics.schemas import EvidencePacket
from flight_recorder.models.envelopes import Finding, Record
from flight_recorder.services.diagnostics import DiagnosticService


def test_repeated_tool_call_finding_links_evidence() -> None:
    records = [
        Record(
            record_id=f"record-{index}",
            producer_id="producer",
            trace_id="trace",
            sequence=index,
            kind="tool.request",
            payload={"tool": "terminal", "command": "pytest"},
        )
        for index in (1, 2)
    ]

    findings = repeated_tool_calls(records)

    assert len(findings) == 1
    assert findings[0].evidence_ids == ("record-1", "record-2")


def test_evidence_packet_rejects_cross_trace_records() -> None:
    record = Record(
        record_id="record",
        producer_id="producer",
        trace_id="other",
        sequence=1,
        kind="tool.request",
    )

    with pytest.raises(ValueError, match="same trace"):
        EvidencePacket(trace_id="trace", records=(record,))


def test_service_rejects_missing_evidence_and_keeps_rules_on_analyzer_failure(
    trace_index,
) -> None:
    records = [
        Record(
            record_id=f"record-{index}",
            producer_id="producer",
            trace_id="trace",
            sequence=index,
            observed_at=datetime(2026, 9, 1, 12),
            kind="tool.request",
            payload={"tool": "terminal", "command": "pytest"},
        )
        for index in (1, 2)
    ]
    trace_index.add_records(records)

    class FailingAnalyzer(InterpretiveAnalyzer):
        def analyze(self, packet: EvidencePacket):
            raise AnalyzerFailure("offline")

    service = DiagnosticService(trace_index, analyzer=FailingAnalyzer())

    findings = service.analyze("trace", include_interpretive=True)

    assert [finding.detector_id for finding in findings] == ["repeated-tool-call"]
    assert service.analyzer_failure is not None

    class UnsupportedAnalyzer(InterpretiveAnalyzer):
        def analyze(self, packet: EvidencePacket):
            return [
                Finding(
                    finding_id="unsupported",
                    trace_id=packet.trace_id,
                    origin="model",
                    detector_id="interpretive",
                    category="loop",
                    severity="warning",
                    confidence=0.5,
                    title="Unsupported claim",
                    explanation="No matching evidence exists.",
                    recommendation="Do not display this.",
                    evidence_ids=("missing",),
                )
            ]

    service = DiagnosticService(trace_index, analyzer=UnsupportedAnalyzer())

    findings = service.analyze("trace", include_interpretive=True)

    assert [finding.detector_id for finding in findings] == ["repeated-tool-call"]
