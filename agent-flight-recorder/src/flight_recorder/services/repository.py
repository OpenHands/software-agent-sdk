"""Read-only trace query service."""

from collections.abc import Iterable

from flight_recorder.errors import SpanNotFound, TraceNotFound
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import (
    Artifact,
    Provenance,
    ProvenanceKind,
    Record,
    RelationshipStatus,
    Span,
    TokenUsage,
)
from flight_recorder.models.view_models import (
    AgentDetail,
    AgentRelationship,
    CondensationView,
    ContextView,
    RunQuery,
    RunSummary,
    SpanDetail,
    TimelineView,
)


def _recorder_shutdown_record_ids(records: tuple[Record, ...]) -> frozenset[str]:
    if len(records) < 2:
        return frozenset()
    agent_finished, run_finished = records[-2:]
    if (
        agent_finished.kind != "agent.finished"
        or agent_finished.parent_span_id is not None
        or agent_finished.span_id != agent_finished.agent_span_id
        or run_finished.kind != "run.finished"
        or run_finished.payload.get("stop_reason") != "recorder_closed"
        or agent_finished.producer_id != run_finished.producer_id
        or agent_finished.sequence is None
        or run_finished.sequence != agent_finished.sequence + 1
    ):
        return frozenset()
    return frozenset((agent_finished.record_id, run_finished.record_id))


class TraceRepository:
    def __init__(self, index: TraceIndex) -> None:
        self.index = index

    def iter_records(self, trace_id: str, after_sequence: int = 0) -> Iterable[Record]:
        return self.index.iter_records(trace_id, after_sequence)

    def get_record(self, trace_id: str, record_id: str) -> Record:
        record = next(
            (
                item
                for item in self.index.iter_records(trace_id)
                if item.record_id == record_id
            ),
            None,
        )
        if record is None:
            raise TraceNotFound(trace_id)
        return record

    def get_next_user_message(
        self,
        trace_id: str,
        *,
        after_sequence: int,
        agent_span_id: str | None,
    ) -> Record | None:
        return next(
            (
                record
                for record in self.index.iter_records(trace_id, after_sequence)
                if record.agent_span_id == agent_span_id
                and record.payload.get("event_type") == "MessageEvent"
                and record.payload.get("source") == "user"
            ),
            None,
        )

    def list_runs(self, query: RunQuery) -> list[RunSummary]:
        runs = []
        for trace_id in self.index.list_trace_ids():
            records = tuple(self.index.iter_records(trace_id))
            run = self.get_run(trace_id)
            if query.status is not None and run.status != query.status:
                continue
            if query.repository is not None and not any(
                record.payload.get("repository") == query.repository
                for record in records
            ):
                continue
            if query.model is not None and not self._has_model(records, query.model):
                continue
            runs.append(run)
        return sorted(
            runs,
            key=lambda run: run.started_at.isoformat() if run.started_at else "",
            reverse=True,
        )

    def get_timeline(self, trace_id: str) -> TimelineView:
        records = tuple(self.index.iter_records(trace_id))
        if not records:
            raise TraceNotFound(trace_id)
        span_ids = dict.fromkeys(
            record.span_id for record in records if record.span_id is not None
        )
        spans = tuple(self.get_span(trace_id, span_id).span for span_id in span_ids)
        return TimelineView(trace_id=trace_id, records=records, spans=spans)

    def get_run(self, trace_id: str) -> RunSummary:
        records = tuple(self.index.iter_records(trace_id))
        if not records:
            raise TraceNotFound(trace_id)
        total_usage, total_cost = self._aggregate_usage(records)
        return RunSummary(
            trace_id=trace_id,
            status="completed"
            if any(item.kind == "run.finished" for item in records)
            else "running",
            started_at=records[0].observed_at,
            completed_at=records[-1].observed_at,
            record_count=len(records),
            total_usage=total_usage,
            total_cost=total_cost,
        )

    def get_span(self, trace_id: str, span_id: str) -> SpanDetail:
        trace_records = tuple(self.index.iter_records(trace_id))
        records = tuple(record for record in trace_records if record.span_id == span_id)
        if not records:
            raise SpanNotFound(span_id)
        terminal = len(records) > 1
        parent_resolves = any(
            record.span_id == records[0].parent_span_id for record in trace_records
        )
        if records[0].parent_span_id is not None and not parent_resolves:
            relationship_status = RelationshipStatus.UNRESOLVED
        elif records[0].provenance.kind is ProvenanceKind.DERIVED:
            relationship_status = RelationshipStatus.INFERRED
        else:
            relationship_status = RelationshipStatus.VERIFIED
        span = Span(
            span_id=span_id,
            trace_id=trace_id,
            span_type=records[0].kind.partition(".")[0],
            name=str(records[0].payload.get("name", span_id)),
            parent_span_id=records[0].parent_span_id,
            agent_span_id=records[0].agent_span_id,
            relationship_status=relationship_status,
            start_sequence=records[0].sequence or 1,
            end_sequence=records[-1].sequence if terminal else None,
            started_at=records[0].observed_at,
            ended_at=records[-1].observed_at if terminal else None,
            status="completed" if terminal else "incomplete",
        )
        return SpanDetail(span=span, records=records)

    def get_workspace_changes(
        self, trace_id: str, span_id: str | None = None
    ) -> list[Artifact]:
        changes = []
        for record in self.index.iter_records(trace_id):
            if not record.kind.startswith("workspace.") or (
                span_id is not None and record.span_id != span_id
            ):
                continue
            changes.append(
                Artifact(
                    artifact_id=record.record_id,
                    trace_id=trace_id,
                    span_id=record.span_id,
                    kind=record.kind,
                    path=record.payload.get("path"),
                    content_hash=record.payload.get("content_hash"),
                    size=record.payload.get("size"),
                    created_sequence=record.sequence or 1,
                    content_ref=record.content_ref,
                )
            )
        return changes

    def get_model_context(self, trace_id: str, record_id: str) -> ContextView:
        records = tuple(self.index.iter_records(trace_id))
        target = next(
            (record for record in records if record.record_id == record_id), None
        )
        if target is None:
            raise TraceNotFound(trace_id)
        completion = target.payload.get("completion")
        if isinstance(completion, dict):
            messages = completion.get("messages")
            if isinstance(messages, list):
                return ContextView(
                    record_id=record_id,
                    messages=tuple(
                        message for message in messages if isinstance(message, dict)
                    ),
                    provenance=Provenance(),
                )

        source_records = [
            record
            for record in records
            if (record.sequence or 0) < (target.sequence or 0)
            and record.payload.get("event_type")
            in {"MessageEvent", "ActionEvent", "ObservationEvent", "SystemPromptEvent"}
        ]
        messages = []
        source_ids = []
        for record in source_records:
            message = record.payload.get("llm_message")
            if isinstance(message, dict):
                messages.append(message)
                source_ids.append(record.record_id)
        return ContextView(
            record_id=record_id,
            messages=tuple(messages),
            provenance=Provenance(
                kind=ProvenanceKind.RECONSTRUCTED,
                source_ids=tuple(source_ids),
                description="Reconstructed from conversation events",
                complete=False,
            ),
        )

    def get_agent_relationships(self, trace_id: str) -> list[AgentRelationship]:
        records = tuple(self.index.iter_records(trace_id))
        span_ids = {record.span_id for record in records if record.span_id is not None}
        relationships = []
        seen_children = set()
        for record in records:
            if (
                record.kind != "agent.started"
                or record.span_id is None
                or record.parent_span_id is None
                or record.span_id in seen_children
            ):
                continue
            seen_children.add(record.span_id)
            if record.parent_span_id not in span_ids:
                status = RelationshipStatus.UNRESOLVED
            elif record.provenance.kind is ProvenanceKind.DERIVED:
                status = RelationshipStatus.INFERRED
            else:
                status = RelationshipStatus.VERIFIED
            relationships.append(
                AgentRelationship(
                    parent_span_id=record.parent_span_id,
                    child_span_id=record.span_id,
                    status=status,
                    provenance=record.provenance,
                )
            )
        return relationships

    def get_agent_usage(
        self, trace_id: str, agent_span_id: str
    ) -> tuple[TokenUsage, float]:
        records = tuple(self.index.iter_records(trace_id))
        direct_records = tuple(
            record for record in records if record.agent_span_id == agent_span_id
        )
        if any(record.kind == "metrics.snapshot" for record in direct_records):
            return self._aggregate_usage(direct_records, include_delegated=False)

        started = next(
            (
                record
                for record in records
                if record.kind == "agent.started" and record.span_id == agent_span_id
            ),
            None,
        )
        task_id = started.payload.get("task_id") if started is not None else None
        task_usage_id = f"task:{task_id}" if isinstance(task_id, str) else None
        task_snapshots = {}
        for record in records:
            if record.kind != "metrics.snapshot":
                continue
            usage_to_metrics = record.payload.get("usage_to_metrics", {})
            if isinstance(usage_to_metrics, dict):
                snapshot = (
                    usage_to_metrics.get(task_usage_id)
                    if task_usage_id is not None
                    else None
                )
                if isinstance(snapshot, dict):
                    assert task_usage_id is not None
                    task_snapshots[task_usage_id] = snapshot
        if task_snapshots:
            return self._aggregate_snapshots(task_snapshots)
        return TokenUsage(), 0.0

    def get_agent_detail(self, trace_id: str, agent_span_id: str) -> AgentDetail:
        relationship = next(
            (
                item
                for item in self.get_agent_relationships(trace_id)
                if item.child_span_id == agent_span_id
            ),
            None,
        )
        if relationship is None:
            detail = self.get_span(trace_id, agent_span_id)
            if detail.span.parent_span_id is not None:
                raise SpanNotFound(agent_span_id)
            relationship = AgentRelationship(
                parent_span_id=None,
                child_span_id=agent_span_id,
                status=RelationshipStatus.VERIFIED,
                provenance=Provenance(),
            )
        trace_records = tuple(self.index.iter_records(trace_id))
        records = tuple(
            record
            for record in trace_records
            if record.agent_span_id == agent_span_id or record.span_id == agent_span_id
        )
        started = next(
            (record for record in records if record.kind == "agent.started"), None
        )
        finished = next(
            (record for record in reversed(records) if record.kind == "agent.finished"),
            None,
        )
        duration = None
        if started is not None and finished is not None:
            ended = finished
            shutdown_record_ids = _recorder_shutdown_record_ids(trace_records)
            if finished.record_id in shutdown_record_ids:
                ended = max(
                    (
                        record
                        for record in trace_records
                        if record.record_id not in shutdown_record_ids
                    ),
                    key=lambda record: record.observed_at,
                    default=started,
                )
            duration = (ended.observed_at - started.observed_at).total_seconds()
        usage, cost = self.get_agent_usage(trace_id, agent_span_id)
        return AgentDetail(
            relationship=relationship,
            handoff=started.payload if started is not None else {},
            returned_result=finished.payload.get("result") if finished else None,
            duration_seconds=duration,
            usage=usage,
            cost=cost,
        )

    @staticmethod
    def _has_model(records: tuple[Record, ...], model: str) -> bool:
        for record in records:
            if record.payload.get("model") == model:
                return True
            metrics = record.payload.get("usage_to_metrics")
            if isinstance(metrics, dict) and any(
                isinstance(snapshot, dict) and snapshot.get("model_name") == model
                for snapshot in metrics.values()
            ):
                return True
        return False

    @staticmethod
    def _aggregate_usage(
        records: tuple[Record, ...], *, include_delegated: bool = True
    ) -> tuple[TokenUsage, float]:
        snapshots: dict[str, dict[str, object]] = {}
        for record in records:
            if record.kind == "metrics.snapshot":
                usage_to_metrics = record.payload.get("usage_to_metrics", {})
                if isinstance(usage_to_metrics, dict):
                    snapshots.update(
                        {
                            usage_id: snapshot
                            for usage_id, snapshot in usage_to_metrics.items()
                            if isinstance(snapshot, dict)
                            and (include_delegated or not usage_id.startswith("task:"))
                        }
                    )

        return TraceRepository._aggregate_snapshots(snapshots)

    @staticmethod
    def _aggregate_snapshots(
        snapshots: dict[str, dict[str, object]],
    ) -> tuple[TokenUsage, float]:
        totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }
        total_cost = 0.0
        for snapshot in snapshots.values():
            usage = snapshot.get("accumulated_token_usage")
            if isinstance(usage, dict):
                for field in totals:
                    totals[field] += int(usage.get(field, 0))
            accumulated_cost = snapshot.get("accumulated_cost", 0)
            if isinstance(accumulated_cost, int | float):
                total_cost += float(accumulated_cost)
        return TokenUsage(**totals), total_cost


def resolve_condensation(
    payload: dict[str, object], known_record_ids: set[str]
) -> CondensationView:
    forgotten = payload.get("forgotten_event_ids", [])
    ids = [str(item) for item in forgotten] if isinstance(forgotten, list) else []
    summary = payload.get("summary")
    return CondensationView(
        resolved_ids=tuple(item for item in ids if item in known_record_ids),
        unresolved_ids=tuple(item for item in ids if item not in known_record_ids),
        summary=summary if isinstance(summary, str) else None,
    )
