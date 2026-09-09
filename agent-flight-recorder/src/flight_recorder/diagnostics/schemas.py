"""Diagnostic request schema."""

from flight_recorder.models.envelopes import Finding, Record
from pydantic import BaseModel, ConfigDict, model_validator


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    trace_id: str
    records: tuple[Record, ...]
    deterministic_findings: tuple[Finding, ...] = ()

    @model_validator(mode="after")
    def validate_evidence(self) -> "EvidencePacket":
        if any(record.trace_id != self.trace_id for record in self.records):
            raise ValueError("all records must belong to the same trace")
        record_ids = {record.record_id for record in self.records}
        for finding in self.deterministic_findings:
            if finding.trace_id != self.trace_id:
                raise ValueError("all findings must belong to the same trace")
            if not set(finding.evidence_ids).issubset(record_ids):
                raise ValueError("finding evidence must resolve in the packet")
        return self
