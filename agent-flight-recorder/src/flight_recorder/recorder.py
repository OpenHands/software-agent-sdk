"""Exception-contained recorder facade."""

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from flight_recorder.adapter.conversation import normalize_event
from flight_recorder.adapter.llm import (
    normalize_completion_log,
    normalize_metrics,
    normalize_request_log,
)
from flight_recorder.collector.normalize import make_record
from flight_recorder.collector.persistence import Collector
from flight_recorder.collector.queue import RecordQueue
from flight_recorder.errors import RecorderClosed
from flight_recorder.models.database import TraceIndex
from flight_recorder.models.envelopes import Record
from openhands.sdk.conversation.conversation_stats import ConversationStats


class Recorder:
    def __init__(self, bundle: Path, index: TraceIndex, max_queue: int = 4096) -> None:
        self.trace_id = str(uuid4())
        self.producer_id = str(uuid4())
        self.agent_span_id = str(uuid4())
        self.queue = RecordQueue(max_queue)
        self.collector = Collector(self.queue, bundle, index)
        self.errors = 0
        self._closed = False
        self.collector.start()
        self._capture(
            lambda: make_record(
                producer_id=self.producer_id,
                trace_id=self.trace_id,
                kind="run.started",
                payload={"status": "running"},
            )
        )
        self._record_agent_started(
            agent_span_id=self.agent_span_id,
            parent_agent_span_id=None,
            payload={"name": "Primary agent"},
        )

    @property
    def warning_counters(self) -> dict[str, int]:
        return {
            "callback_errors": self.errors,
            "dropped_records": self.queue.dropped,
            "collector_errors": self.collector.errors,
        }

    def on_event(self, event: object) -> None:
        self._on_agent_event(
            event,
            agent_span_id=self.agent_span_id,
            parent_agent_span_id=None,
        )

    def _on_agent_event(
        self,
        event: object,
        *,
        agent_span_id: str,
        parent_agent_span_id: str | None,
    ) -> None:
        self._capture(
            lambda: normalize_event(
                event,
                producer_id=self.producer_id,
                trace_id=self.trace_id,
                agent_span_id=agent_span_id,
                parent_agent_span_id=parent_agent_span_id,
            )
        )

    def __call__(self, event: object) -> None:
        self.on_event(event)

    def for_subagent(
        self,
        *,
        task_id: str,
        subagent_type: str,
        description: str | None,
    ) -> "_AgentRecorderCallback":
        return _AgentRecorderCallback(
            recorder=self,
            agent_span_id=str(uuid4()),
            parent_agent_span_id=self.agent_span_id,
            task_id=task_id,
            subagent_type=subagent_type,
            description=description,
        )

    def finish_subagent(
        self,
        *,
        status: str,
        result: str | None,
        error: str | None,
        stats: ConversationStats,
    ) -> None:
        self.record_metrics(stats)
        self._record_agent_finished(
            agent_span_id=self.agent_span_id,
            parent_agent_span_id=None,
            status=status,
            result=result,
            error=error,
        )

    def on_completion_log(self, filename: str, log_data: str) -> None:
        self._capture(
            lambda: normalize_completion_log(
                filename, log_data, producer_id=self.producer_id, trace_id=self.trace_id
            )
        )

    def on_llm_request(self, llm_call_id: str, log_data: str) -> None:
        self._capture(
            lambda: normalize_request_log(
                llm_call_id,
                log_data,
                producer_id=self.producer_id,
                trace_id=self.trace_id,
            )
        )

    def record_metrics(self, stats: ConversationStats) -> None:
        self._capture(
            lambda: normalize_metrics(
                stats,
                producer_id=self.producer_id,
                trace_id=self.trace_id,
                agent_span_id=self.agent_span_id,
            )
        )

    def _capture(self, factory: Callable[[], Record]) -> None:
        try:
            if self._closed:
                raise RecorderClosed()
            if not self.queue.put(factory()):
                self.errors += 1
        except Exception:
            self.errors += 1

    def _record_agent_started(
        self,
        *,
        agent_span_id: str,
        parent_agent_span_id: str | None,
        payload: dict[str, object],
    ) -> None:
        self._capture(
            lambda: make_record(
                producer_id=self.producer_id,
                trace_id=self.trace_id,
                kind="agent.started",
                payload=payload,
                span_id=agent_span_id,
                parent_span_id=parent_agent_span_id,
                agent_span_id=agent_span_id,
            )
        )

    def _record_agent_finished(
        self,
        *,
        agent_span_id: str,
        parent_agent_span_id: str | None,
        status: str,
        result: str | None,
        error: str | None,
    ) -> None:
        self._capture(
            lambda: make_record(
                producer_id=self.producer_id,
                trace_id=self.trace_id,
                kind="agent.finished",
                payload={"status": status, "result": result, "error": error},
                span_id=agent_span_id,
                parent_span_id=parent_agent_span_id,
                agent_span_id=agent_span_id,
            )
        )

    def close(self) -> None:
        if not self._closed:
            self._record_agent_finished(
                agent_span_id=self.agent_span_id,
                parent_agent_span_id=None,
                status="completed",
                result=None,
                error=None,
            )
            self._capture(
                lambda: make_record(
                    producer_id=self.producer_id,
                    trace_id=self.trace_id,
                    kind="run.finished",
                    payload={"status": "completed", "stop_reason": "recorder_closed"},
                )
            )
            self._closed = True
            self.collector.stop()


class _AgentRecorderCallback:
    def __init__(
        self,
        *,
        recorder: Recorder,
        agent_span_id: str,
        parent_agent_span_id: str,
        task_id: str,
        subagent_type: str,
        description: str | None,
    ) -> None:
        self.recorder = recorder
        self.agent_span_id = agent_span_id
        self.parent_agent_span_id = parent_agent_span_id
        self.task_id = task_id
        self.recorder._record_agent_started(
            agent_span_id=agent_span_id,
            parent_agent_span_id=parent_agent_span_id,
            payload={
                "task_id": task_id,
                "subagent_type": subagent_type,
                "name": description or subagent_type,
            },
        )

    def __call__(self, event: object) -> None:
        self.recorder._on_agent_event(
            event,
            agent_span_id=self.agent_span_id,
            parent_agent_span_id=self.parent_agent_span_id,
        )

    def for_subagent(
        self,
        *,
        task_id: str,
        subagent_type: str,
        description: str | None,
    ) -> "_AgentRecorderCallback":
        return _AgentRecorderCallback(
            recorder=self.recorder,
            agent_span_id=str(uuid4()),
            parent_agent_span_id=self.agent_span_id,
            task_id=task_id,
            subagent_type=subagent_type,
            description=description,
        )

    def finish_subagent(
        self,
        *,
        status: str,
        result: str | None,
        error: str | None,
        stats: ConversationStats,
    ) -> None:
        self.recorder._capture(
            lambda: normalize_metrics(
                stats,
                producer_id=self.recorder.producer_id,
                trace_id=self.recorder.trace_id,
                agent_span_id=self.agent_span_id,
            )
        )
        self.recorder._record_agent_finished(
            agent_span_id=self.agent_span_id,
            parent_agent_span_id=self.parent_agent_span_id,
            status=status,
            result=result,
            error=error,
        )
