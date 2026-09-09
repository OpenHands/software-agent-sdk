# Data Model: Agent Flight Recorder

## Model Rules

- The `.afr` bundle is authoritative; SQLite is a disposable projection.
- `record_id` is immutable and unique within a trace.
- `sequence` is assigned by the collector and strictly increases within a trace.
- Source timestamps describe occurrence time; sequence determines replay order.
- `monotonic_ns` is valid only for durations from the same producer.
- Unknown record kinds and fields are preserved during import and export.
- Every derived object retains the record or span IDs from which it was produced.

## Trace

Represents one observed OpenHands run.

| Field | Type | Rules |
| --- | --- | --- |
| `trace_id` | UUID string | Stable bundle identity |
| `format_version` | positive integer | Major incompatibility requires reader rejection |
| `status` | RunStatus | Follows the state machine below |
| `started_at` | timestamp | Required after recording starts |
| `completed_at` | timestamp or null | Required for terminal states |
| `stop_reason` | StopReason or null | Required for terminal states |
| `repository_identity` | string or null | Descriptive only |
| `recorder_version` | semantic version | Required |
| `record_count` | non-negative integer | Finalized traces only |
| `finding_count` | non-negative integer | Finalized traces only |
| `total_usage` | UsageTotals | Derived from correlated metrics |
| `integrity_state` | IntegrityState | `active`, `verified`, `incomplete`, or `invalid` |

### Run state transitions

```text
created -> running -> completed
                   -> failed
                   -> interrupted
                   -> paused -> running
```

Only `paused` may transition back to `running`. A terminal state requires both
`completed_at` and `stop_reason`. A stale `running` trace discovered after process
restart becomes `incomplete` for display but retains its last recorded run status.

## Span

Represents a duration-based activity projected from one or more records.

| Field | Type | Rules |
| --- | --- | --- |
| `span_id` | UUID string | Stable within trace |
| `trace_id` | UUID string | Must identify owning trace |
| `parent_span_id` | UUID string or null | Must resolve or carry unresolved status |
| `agent_span_id` | UUID string or null | Owning swimlane; required for agent work |
| `span_type` | SpanType | `run`, `agent`, `iteration`, `llm`, `tool`, `test`, `delegation`, `condensation`, `evaluation` |
| `name` | string | Non-empty display name |
| `relationship_status` | RelationshipStatus | `verified`, `inferred`, or `unresolved` |
| `start_sequence` | positive integer | References starting record |
| `end_sequence` | positive integer or null | Null means incomplete |
| `started_at` | timestamp | Required |
| `ended_at` | timestamp or null | Must not precede start |
| `status` | SpanStatus | `running`, `completed`, `failed`, `interrupted`, or `incomplete` |
| `attributes` | object | Kind-specific searchable metadata |

Starting and terminal records use the same `span_id`. Instantaneous activities may
use a containing span without creating a new duration span.

## Record

Immutable observed or recorder-generated activity.

| Field | Type | Rules |
| --- | --- | --- |
| `schema_version` | positive integer | Required |
| `record_id` | UUID string | Unique in bundle |
| `producer_id` | string | Stable for one recorder process |
| `trace_id` | UUID string | Must match bundle trace |
| `span_id` | UUID string or null | Owning or represented span |
| `parent_span_id` | UUID string or null | Preserves hierarchy |
| `agent_span_id` | UUID string or null | Denormalized lane identity |
| `sequence` | positive integer | Strictly increasing and unique per trace |
| `observed_at` | timestamp | Recorder observation time |
| `source_timestamp` | timestamp or null | Original event time |
| `monotonic_ns` | non-negative integer or null | Same-producer duration only |
| `kind` | string | Known or preserved unknown kind |
| `openhands_event_id` | string or null | Source event correlation |
| `llm_response_id` | string or null | Model/metrics correlation |
| `payload` | object | Kind-specific data |
| `content_ref` | ContentRef or null | Large payload reference |
| `provenance` | Provenance | Captured, reconstructed, derived, or interpreted |

Known lifecycle pairs are:

| Span | Start | Terminal |
| --- | --- | --- |
| Run | `run.started` | `run.finished` |
| Agent | `agent.started` | `agent.finished` |
| Iteration | `iteration.started` | `iteration.finished` |
| LLM | `llm.request` | `llm.response` or `llm.error` |
| Tool | `tool.request` | `tool.result` or `tool.error` |
| Test | `test.started` | `test.finished` |
| Delegation | `delegation.started` | `delegation.finished` |

## Provenance

Distinguishes facts from projections and interpretation.

| Field | Type | Rules |
| --- | --- | --- |
| `kind` | enum | `captured`, `reconstructed`, `derived`, or `interpreted` |
| `source_ids` | list of IDs | Required for non-captured data |
| `description` | string or null | Explains reconstruction or derivation |
| `complete` | boolean | False when evidence is known to be missing |

Authoritative completion-log requests use `captured`. Context projected from
conversation events uses `reconstructed` and identifies contributing event IDs.

## CondensationPayload

Payload for `context.condensed`.

| Field | Type | Rules |
| --- | --- | --- |
| `forgotten_event_ids` | unique list of strings | Required; may be empty |
| `summary` | string or null | Preserves SDK value |
| `summary_offset` | integer or null | Non-negative when present |
| `llm_response_id` | string | Required |

The context view resolves forgotten event IDs to records when possible and exposes
unresolved IDs rather than dropping them.

## LLMCall

Queryable projection joining request, response, and metrics records.

| Field | Type | Rules |
| --- | --- | --- |
| `span_id` | UUID string | Primary identity |
| `usage_id` | string | Identifies configured LLM usage |
| `response_id` | string or null | Primary correlation key when available |
| `model` | string | Required |
| `request_record_id` | UUID string | Required |
| `response_record_id` | UUID string or null | Null while incomplete |
| `context_provenance` | Provenance | Exact or reconstructed status |
| `finish_reason` | string or null | Preserved provider value |
| `usage` | TokenUsage | Non-negative counts |
| `cost` | decimal or null | Must be non-negative |
| `latency_ms` | non-negative number or null | Derived from telemetry |
| `retry_count` | non-negative integer | Defaults to zero |

## TokenUsage and UsageTotals

Contains prompt, completion, cache-read, cache-write, reasoning, and total tokens.
Per-call usage may also include context-window size. Counts are non-negative. Trace,
agent, and span totals are derived only from correlated per-call metrics to prevent
double counting.

## Artifact

Represents an output or workspace change associated with activity.

| Field | Type | Rules |
| --- | --- | --- |
| `artifact_id` | UUID string | Unique within trace |
| `trace_id` | UUID string | Required |
| `span_id` | UUID string or null | Producing span when known |
| `kind` | string | Examples: `workspace.diff`, `test.result`, `file` |
| `path` | string or null | Slash-normalized when present |
| `content_hash` | SHA-256 or null | Content identity |
| `size` | non-negative integer or null | Bytes when applicable |
| `created_sequence` | positive integer | Links artifact to replay order |
| `content_ref` | ContentRef or null | Optional payload |

A workspace-diff record may project to one or more artifacts; the source record
remains authoritative.

## ContentRef

A string of the form `sha256:<64 lowercase hexadecimal characters>`. The importer
verifies the digest after decompression. Content files must remain below the bundle's
`content/` directory and use a supported media suffix.

## Finding

Evidence-backed diagnostic output.

| Field | Type | Rules |
| --- | --- | --- |
| `finding_id` | UUID string | Unique within trace |
| `trace_id` | UUID string | Required |
| `origin` | enum | `rule` or `model` |
| `detector_id` | string | Required |
| `category` | enum | `loop`, `context`, `delegation`, `tool`, `cost`, `termination` |
| `severity` | enum | `info`, `warning`, or `error` |
| `confidence` | number | Inclusive range 0 through 1 |
| `title` | string | Non-empty |
| `explanation` | string | Non-empty |
| `recommendation` | string | Non-empty |
| `evidence_ids` | unique list of IDs | At least one; every ID must resolve |
| `affected_span_ids` | unique list of span IDs | Every ID must resolve |
| `created_at` | timestamp | Required |

Findings with missing evidence are rejected. Finding records use `derived` or
`interpreted` provenance according to origin.

## CommittedBatch

Notification unit delivered after durable append and index commit.

| Field | Type | Rules |
| --- | --- | --- |
| `trace_id` | UUID string | Required |
| `first_sequence` | positive integer | First included record |
| `last_sequence` | positive integer | At least `first_sequence` |
| `record_ids` | ordered list of UUID strings | Must match inclusive batch order |

Subscribers may resume with the last processed sequence. Repeated batch delivery is
safe because record identity is idempotent.

## SQLite Projection

The index contains `runs`, `spans`, `records`, `llm_calls`, `artifacts`, `findings`,
and `content_blobs`. It enforces unique `(trace_id, sequence)` and `record_id`, indexes
parent/agent span IDs and common filters, and can be deleted and rebuilt from the
bundle without loss of authoritative data.
