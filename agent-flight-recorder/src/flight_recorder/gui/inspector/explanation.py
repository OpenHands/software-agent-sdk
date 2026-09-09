import json
from datetime import datetime
from typing import Any

from flight_recorder.gui.timeline.rows import (
    ACTIVITY_ROW_TYPES_BY_KEY,
    classify_record,
)
from flight_recorder.models.envelopes import Finding, Record, TokenUsage
from flight_recorder.models.view_models import AgentDetail, RunSummary


def explain_run(run: RunSummary) -> str:
    timing = _run_timing(run)
    return (
        "Run overview\n\n"
        "WHAT THIS REPRESENTS\n"
        "This selection represents one recorded OpenHands run. A run is the "
        "outer container for the primary agent, delegated agents, messages, "
        "tool activity, model context changes, metrics, and diagnostics stored "
        "in the trace. The trace ID identifies this recording, not an agent or "
        "a single model request.\n\n"
        "CURRENT INTERPRETATION\n"
        f"Trace ID: {run.trace_id}\n"
        f"Status: {run.status}\n"
        f"Recorded entries: {run.record_count}\n"
        f"Diagnostic findings: {run.finding_count}\n"
        f"{timing}\n\n"
        "USAGE AND COST\n"
        f"{_usage_text(run.total_usage, run.total_cost)}\n"
        "These are aggregate values reconstructed from the metrics snapshots in "
        "the trace. They describe attributed model usage; they do not measure "
        "agent quality, useful work, or elapsed time.\n\n"
        "HOW TO READ THIS\n"
        "The timeline groups recorded entries by agent and activity category. "
        "Lifecycle bars show agent boundaries, while markers show messages, "
        "actions, results, delegation, context changes, state changes, metrics, "
        "and errors. Selecting an agent or marker replaces this explanation with "
        "details about that evidence.\n\n"
        "EVIDENCE LIMITS\n"
        "The recorder reports what was captured. A missing entry can mean the "
        "activity did not occur, was outside the recorder's scope, or was omitted "
        "under collection pressure. Status and totals should therefore be read "
        "together with warnings and findings."
    )


def explain_agent(detail: AgentDetail) -> str:
    relationship = detail.relationship
    is_primary = relationship.parent_span_id is None
    role = "primary agent" if is_primary else "delegated agent"
    name = detail.handoff.get("name")
    display_name = (
        name if isinstance(name, str) and name else relationship.child_span_id
    )
    parent_text = relationship.parent_span_id or "none; this is the root agent"
    duration_text = _agent_duration_text(detail, is_primary=is_primary)
    outcome_text = _agent_outcome_text(detail.returned_result, is_primary=is_primary)
    handoff_label = "Startup metadata" if is_primary else "Recorded handoff"
    return (
        "Agent lifecycle\n\n"
        "WHAT THIS REPRESENTS\n"
        f"This selection is the lifecycle of the {role} named {display_name!r}. "
        "A lifecycle bar groups the records attributed to one agent and shows "
        "where that agent's recorded activity sits on the shared timeline. It is "
        "not a model call, tool call, operating-system process, or CPU-usage graph.\n\n"
        "IDENTITY AND HIERARCHY\n"
        f"Agent span ID: {relationship.child_span_id}\n"
        f"Parent agent span ID: {parent_text}\n"
        f"Role: {role}\n"
        f"Relationship: {relationship.status.value}\n"
        f"{_relationship_text(detail)}\n\n"
        f"{handoff_label.upper()}\n"
        f"{_json_text(detail.handoff)}\n"
        + (
            "For the primary agent this is metadata captured when recording "
            "started; it is not a delegation request.\n\n"
            if is_primary
            else "This is the task metadata passed from the parent agent.\n\n"
        )
        + "TIMING\n"
        f"{duration_text}\n\n"
        "OUTCOME\n"
        f"{outcome_text}\n\n"
        "USAGE AND COST\n"
        f"{_usage_text(detail.usage, detail.cost)}\n"
        "Prompt tokens are model-input tokens attributed to this agent. Cache "
        "and reasoning counts are shown separately when the provider reports "
        "them. Cost is the captured provider or metrics estimate; it is not "
        "computed from duration.\n\n"
        "EVIDENCE LIMITS\n"
        "This explanation is derived from captured evidence. The lifecycle shows "
        "recorded boundaries and attribution, not whether the agent's reasoning "
        "was correct or its work was valuable. Inspect this agent's Messages, "
        "Actions, Results, Context, Errors, and the evidence attached to findings "
        "before drawing a causal conclusion."
    )


def explain_finding(finding: Finding) -> str:
    origin = (
        "a deterministic recorder rule"
        if finding.origin == "rule"
        else "an interpretive model analysis"
    )
    affected = ", ".join(finding.affected_span_ids) or "No span explicitly listed"
    return (
        "Diagnostic finding\n\n"
        "WHAT THIS REPRESENTS\n"
        f"This is a diagnostic conclusion produced by {origin}. It is derived "
        "from trace evidence and is not itself a raw event emitted by the agent.\n\n"
        "CLASSIFICATION\n"
        f"Title: {finding.title}\n"
        f"Severity: {finding.severity}\n"
        f"Category: {finding.category}\n"
        f"Detector: {finding.detector_id}\n"
        f"Confidence: {finding.confidence:.0%}\n"
        f"Affected spans: {affected}\n\n"
        "INTERPRETATION\n"
        f"{finding.explanation}\n\n"
        "RECOMMENDED RESPONSE\n"
        f"{finding.recommendation}\n\n"
        "SUPPORTING EVIDENCE\n"
        f"{', '.join(finding.evidence_ids)}\n"
        "The evidence IDs identify the records or spans used to support the "
        "finding. Open them to verify the conclusion against captured data.\n\n"
        "EVIDENCE LIMITS\n"
        "Severity describes potential impact, while confidence describes support "
        "for this interpretation; neither guarantees causation. Model-originated "
        "findings require particular care because they may contain interpretive "
        "errors even when their evidence references resolve."
    )


def explain_record(record: Record) -> str:
    category = ACTIVITY_ROW_TYPES_BY_KEY[classify_record(record)]
    source_time = (
        _format_datetime(record.source_timestamp)
        if record.source_timestamp is not None
        else "Not supplied; recorder observation time is used for placement"
    )
    return (
        "Recorded activity\n\n"
        "WHAT THIS REPRESENTS\n"
        f"This is one immutable trace record of kind {record.kind!r}. It appears "
        f"in the {category.label} row because {category.description.lower()} "
        f"{_record_kind_text(record)}\n\n"
        "ORDER AND TIME\n"
        f"Sequence: {record.sequence if record.sequence is not None else 'unknown'}\n"
        f"Observed by recorder: {_format_datetime(record.observed_at)}\n"
        f"Source timestamp: {source_time}\n"
        "Sequence is the authoritative order within this trace. Source time is "
        "preferred for timeline placement when available; observed time records "
        "when the recorder received the activity. Small differences between them "
        "are expected.\n\n"
        "ATTRIBUTION\n"
        f"Record ID: {record.record_id}\n"
        f"Agent span ID: {record.agent_span_id or 'not attributed'}\n"
        f"Span ID: {record.span_id or 'no separate span'}\n"
        f"Parent span ID: {record.parent_span_id or 'none'}\n"
        f"Producer ID: {record.producer_id}\n\n"
        "PAYLOAD INTERPRETATION\n"
        f"{_payload_text(record.payload)}\n\n"
        "PROVENANCE\n"
        f"Kind: {record.provenance.kind.value}\n"
        f"Complete: {'yes' if record.provenance.complete else 'no'}\n"
        f"Source evidence IDs: "
        f"{', '.join(record.provenance.source_ids) or 'none'}\n"
        f"{record.provenance.description or 'No additional provenance note.'}\n\n"
        "EVIDENCE LIMITS\n"
        "A record proves that this payload was captured at this position in the "
        "trace. It does not by itself prove intent, correctness, or causation. "
        "Correlate tool-call IDs, span IDs, neighboring records, and findings "
        "before interpreting why the activity occurred."
    )


def _run_timing(run: RunSummary) -> str:
    if run.started_at is None:
        return "Start and completion times were not captured."
    if run.completed_at is None:
        return f"Started: {_format_datetime(run.started_at)}\nCompleted: not recorded"
    elapsed = (run.completed_at - run.started_at).total_seconds()
    return (
        f"Started: {_format_datetime(run.started_at)}\n"
        f"Completed: {_format_datetime(run.completed_at)}\n"
        f"Trace lifetime: {elapsed:.3f} seconds ({_clock_duration(elapsed)})\n"
        "Trace lifetime spans the first through last record and can include idle time."
    )


def _agent_duration_text(detail: AgentDetail, *, is_primary: bool) -> str:
    if detail.duration_seconds is None:
        return (
            "Duration: incomplete. A matching finish boundary was not captured, "
            "so the recorder cannot calculate a completed lifecycle."
        )
    duration = detail.duration_seconds
    interpretation = (
        "For the primary agent, synthetic recorder-shutdown idle time is excluded; "
        "the value ends at the last recorded activity before shutdown."
        if is_primary
        else "For a delegated agent, this is elapsed time between its captured start "
        "and finish boundaries and may include time spent waiting inside the task."
    )
    return (
        f"Duration: {duration:.3f} seconds ({_clock_duration(duration)}).\n"
        f"{interpretation} This is wall-clock timing, not CPU time or continuous "
        "model-execution time."
    )


def _agent_outcome_text(result: Any, *, is_primary: bool) -> str:
    if result is None:
        if is_primary:
            return (
                "Returned result: none. The primary recorder boundary does not carry "
                "a delegated-task return value, so this does not imply failure."
            )
        return (
            "Returned result: none captured. This alone does not distinguish an empty "
            "successful result from interruption, failure, or incomplete capture; "
            "inspect the agent's state and error records."
        )
    return (
        f"Returned result captured from this delegated lifecycle:\n{_json_text(result)}"
    )


def _relationship_text(detail: AgentDetail) -> str:
    relationship = detail.relationship
    meanings = {
        "verified": (
            "Captured records explicitly connect this agent to the displayed parent."
        ),
        "inferred": (
            "The parent-child connection was reconstructed from available evidence "
            "rather than captured directly."
        ),
        "unresolved": (
            "The trace names a parent, but that parent span is missing from the "
            "available captured evidence. The hierarchy may therefore be incomplete."
        ),
    }
    provenance = relationship.provenance
    return (
        f"{meanings[relationship.status.value]} Provenance is "
        f"{provenance.kind.value} and marked "
        f"{'complete' if provenance.complete else 'incomplete'}."
    )


def _usage_text(usage: TokenUsage, cost: float) -> str:
    return (
        f"Prompt tokens: {usage.prompt_tokens}\n"
        f"Completion tokens: {usage.completion_tokens}\n"
        f"Cache-read tokens: {usage.cache_read_tokens}\n"
        f"Cache-write tokens: {usage.cache_write_tokens}\n"
        f"Reasoning tokens: {usage.reasoning_tokens}\n"
        f"Cost: ${cost:.4f}"
    )


def _record_kind_text(record: Record) -> str:
    event_type = record.payload.get("event_type")
    event_meanings = {
        "MessageEvent": "It captures a user or agent message.",
        "SystemPromptEvent": "It captures system instructions supplied to the agent.",
        "ActionEvent": "It captures an action or tool call requested by the agent.",
        "ObservationEvent": "It captures the result returned by an action or tool.",
        "ConversationStateUpdateEvent": (
            "It captures a transition in conversation state."
        ),
        "Condensation": "It captures a model-context condensation event.",
    }
    if isinstance(event_type, str) and event_type in event_meanings:
        return event_meanings[event_type]
    kind_meanings = {
        "run.started": "It marks the beginning of trace collection.",
        "run.finished": "It marks the end of trace collection.",
        "agent.started": "It marks the beginning of an agent lifecycle.",
        "agent.finished": "It marks the end of an agent lifecycle.",
        "metrics.snapshot": "It captures cumulative usage and cost metrics.",
        "recorder.warning": "It reports a collection limitation or omission.",
    }
    return kind_meanings.get(
        record.kind,
        "Its exact semantics are defined by the producer and payload.",
    )


def _payload_text(payload: dict[str, Any]) -> str:
    if not payload:
        return "The record has no type-specific payload fields."
    interpretations = []
    field_meanings = {
        "event_type": "OpenHands event class",
        "tool_name": "tool selected for the action or result",
        "tool_call_id": "correlation key pairing an action with its result",
        "status": "state reported by the producer",
        "result": "returned output",
        "error": "reported failure detail",
        "model": "model associated with this activity",
        "repository": "repository attributed by the producer",
    }
    for key, meaning in field_meanings.items():
        if key in payload:
            interpretations.append(f"- {key}: {meaning}; value={payload[key]!r}")
    unknown = sorted(set(payload) - set(field_meanings))
    if unknown:
        interpretations.append("- Other captured fields: " + ", ".join(unknown) + ".")
    return "\n".join(interpretations)


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _format_datetime(value: datetime) -> str:
    return value.isoformat(sep=" ", timespec="milliseconds")


def _clock_duration(seconds: float) -> str:
    whole_seconds = max(0, round(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"
