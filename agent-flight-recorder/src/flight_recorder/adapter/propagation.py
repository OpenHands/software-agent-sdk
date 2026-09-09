from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTraceContext:
    trace_id: str
    agent_span_id: str
    parent_agent_span_id: str | None = None

    def child(self, agent_span_id: str) -> "AgentTraceContext":
        return AgentTraceContext(
            trace_id=self.trace_id,
            agent_span_id=agent_span_id,
            parent_agent_span_id=self.agent_span_id,
        )
