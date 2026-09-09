# Contract: Python Recorder and Application Services

## Principles

- Public callbacks return quickly and never raise into OpenHands.
- Domain services contain no Qt imports.
- Query and diagnostic services do not control an observed conversation.
- Returned view models are immutable snapshots.
- Missing records, invalid bundles, and unavailable traces use typed failures.

## Recorder

```python
class Recorder(Protocol):
    @property
    def trace_id(self) -> str: ...

    def on_event(self, event: Event) -> None: ...
    def on_completion_log(self, filename: str, log_data: str) -> None: ...
    def record_metrics(self, stats: ConversationStats) -> None: ...
    def close(self) -> None: ...
```

`on_event`, `on_completion_log`, and `record_metrics` normalize and attempt a
non-blocking enqueue. They contain recorder errors and expose them later as recorder
warnings/counters. `close` requests a bounded flush and is idempotent.

## TraceRepository

```python
class TraceRepository(Protocol):
    def list_runs(self, query: RunQuery) -> list[RunSummary]: ...
    def get_run(self, trace_id: str) -> RunDetail: ...
    def get_timeline(self, trace_id: str) -> TimelineView: ...
    def get_span(self, trace_id: str, span_id: str) -> SpanDetail: ...
    def iter_records(
        self, trace_id: str, after_sequence: int = 0
    ) -> Iterable[Record]: ...
    def get_findings(self, trace_id: str) -> list[Finding]: ...
    def get_workspace_changes(
        self, trace_id: str, span_id: str | None = None
    ) -> list[Artifact]: ...
```

Ordering is deterministic. `iter_records` returns records with sequence strictly
greater than `after_sequence`. Unknown record kinds appear as generic activities.
Missing trace/span identities raise `TraceNotFound`/`SpanNotFound`.

## BundleService

```python
class BundleService(Protocol):
    def validate_bundle(self, source: Path) -> BundleValidation: ...
    def import_bundle(self, source: Path) -> str: ...
    def export_bundle(self, trace_id: str, destination: Path) -> Path: ...
```

Validation performs no index mutation. Import validates first, is idempotent by
trace and record identity, builds a fresh index transactionally, and returns the
trace ID. Export finalizes counts and checksums before atomically placing the result.
Invalid input raises `InvalidBundle` with a machine-readable code and source path.

## TraceSubscription

```python
class TraceSubscription(Protocol):
    def subscribe(
        self,
        trace_id: str,
        after_sequence: int,
        callback: Callable[[CommittedBatch], None],
    ) -> SubscriptionHandle: ...
```

Only durably committed batches are published. Delivery may repeat, so consumers use
record identity and sequence for idempotency. A handle supports idempotent
`unsubscribe()`. Slow subscribers cannot block the collector.

## DiagnosticService

```python
class DiagnosticService(Protocol):
    def analyze(
        self, trace_id: str, include_interpretive: bool = False
    ) -> list[Finding]: ...
```

Deterministic rules always run. Interpretive analysis is optional and receives a
bounded immutable evidence packet. All returned findings pass the same schema and
evidence-resolution checks before storage or display. Analyzer failure returns a
typed diagnostic failure while existing traces and deterministic findings remain
available.

## SelectionController

```python
class SelectionController(Protocol):
    def select_record(self, record_id: str) -> None: ...
    def select_span(self, span_id: str) -> None: ...
    def current_selection(self) -> Selection: ...
```

The Qt implementation emits one canonical selection change. Tree, timeline,
inspector, findings, and workspace views subscribe to it and must not create
circular updates.

## Command Contract

```text
agent-flight-recorder [TRACE]
agent-flight-recorder record --output DESTINATION TARGET
agent-flight-recorder validate TRACE
agent-flight-recorder import TRACE
agent-flight-recorder export TRACE_ID DESTINATION
```

With no subcommand, the desktop application opens and optionally selects `TRACE`.
`record` runs a supported local target with recording attached and writes an active
bundle to `DESTINATION`. `validate` performs headless bundle validation and exits 0
for valid, 1 for invalid, and 2 for invocation errors. Import prints the trace ID.
Export prints the resulting bundle path. Errors go to stderr; successful
machine-readable values go to stdout.

## Typed Failures

| Failure | Meaning |
| --- | --- |
| `TraceNotFound` | Trace ID is absent from the index |
| `SpanNotFound` | Span ID is absent from the trace |
| `InvalidBundle` | Portable contract or integrity validation failed |
| `UnsupportedFormat` | Bundle major version is unsupported |
| `RecorderClosed` | Producer attempted capture after close |
| `DiagnosticFailure` | Analysis failed without affecting trace access |

Recorder callback methods contain these failures. User-initiated service and command
operations surface them explicitly.
