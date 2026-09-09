from collections.abc import Callable

from flight_recorder.diagnostics.schemas import EvidencePacket
from flight_recorder.errors import DiagnosticFailure
from flight_recorder.models.envelopes import Finding


class AnalyzerFailure(DiagnosticFailure):
    pass


class InterpretiveAnalyzer:
    def __init__(
        self,
        analyze_packet: Callable[[EvidencePacket], list[Finding]] | None = None,
        *,
        record_limit: int = 1000,
    ) -> None:
        self._analyze_packet = analyze_packet
        self.record_limit = record_limit

    def analyze(self, packet: EvidencePacket) -> list[Finding]:
        if self._analyze_packet is None:
            return []
        bounded = packet.model_copy(
            update={"records": packet.records[-self.record_limit :]}
        )
        try:
            return self._analyze_packet(bounded)
        except Exception as exc:
            raise AnalyzerFailure(str(exc)) from exc
