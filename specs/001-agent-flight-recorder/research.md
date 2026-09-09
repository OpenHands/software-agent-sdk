# Phase 0 Research: Agent Flight Recorder

## Package and Runtime Boundary

**Decision**: Develop `agent-flight-recorder/` as a uv workspace member named
`openhands-agent-flight-recorder`, versioned initially at `0.1.0`, with a
`src/flight_recorder` import package and Python 3.12 minimum.

**Rationale**: The recorder is independently distributable and has large desktop
runtime dependencies that do not belong in the SDK. Python 3.12 matches the SDK's
minimum. A `src` layout prevents accidental imports from the checkout, while the
existing design already establishes `flight_recorder` as the import namespace.

**Alternatives considered**:

- Add recorder modules to `openhands-sdk`: rejected because GUI and persistence
  dependencies would expand the core SDK and blur product boundaries.
- Use `openhands.agent_flight_recorder`: consistent with other monorepo packages,
  but rejected for the first release because the approved design uses a standalone
  package and entry point. This can only change before public release.
- Keep the recorder outside the uv workspace: rejected because it would bypass the
  repository's lockfile, build, lint, and test workflow.

## OpenHands Event Capture

**Decision**: Attach a lightweight callable through
`LocalConversation(callbacks=[recorder.on_event])`. Convert each `Event` into a
producer record using `id`, `timestamp`, `source`, `parent_id`, discriminated event
type, and serialized payload. Never read conversation state from inside this
callback.

**Rationale**: `ConversationCallbackType` is `Callable[[Event], None]` and is the
stable event observation surface. Local conversation callbacks run while state is
locked and before default persistence, so blocking or re-entering state would risk
influencing the observed run.

**Alternatives considered**:

- Poll `conversation.state.events`: rejected because it introduces ordering races
  and misses the callback's authoritative observation point.
- Modify the agent loop to emit recorder records: rejected because recording would
  control or couple to the observed execution.

## LLM Requests, Responses, and Metrics

**Decision**: Register a completion-log callback on each observed LLM telemetry
instance and parse its `(filename, log_data)` JSON as the authoritative provider-call
record. Correlate it with actions and metrics through response IDs and `usage_id`.
Use `ConversationStats` snapshots outside event callbacks to capture accumulated and
per-call token, cost, latency, cache, context-window, and reasoning-token counts.

**Rationale**: Conversation events do not contain the final provider request after
projection, merging, tool-schema insertion, and condensation. Completion telemetry
is therefore the only exact request source. `ActionEvent.llm_response_id`, metrics
`response_id`, and `LLMCompletionLogEvent` identifiers provide explicit correlation.

**Alternatives considered**:

- Reconstruct every request from conversation events: retained only as a clearly
  labeled fallback because it cannot be authoritative.
- Persist streaming chunks individually: rejected by default because they increase
  volume without improving durable replay; the completed response is authoritative.

## Condensation

**Decision**: Normalize each SDK `Condensation` into one `context.condensed` record
containing forgotten event IDs, summary, summary offset, and producing LLM response
ID. The context service applies these recorded facts to build before/after views.

**Rationale**: These SDK fields directly encode what was removed and inserted, so
no semantic guess is needed.

**Alternatives considered**:

- Diff projected contexts only: rejected because it loses the SDK's explicit
  forgotten-event identity and can misattribute unrelated context changes.

## Agent and Delegation Lineage

**Decision**: Preserve SDK event `parent_id` exactly and assign recorder
`parent_span_id`, `agent_span_id`, and a relationship status of `verified`,
`inferred`, or `unresolved`. The first release supports one local process. Trace
context propagation across delegated conversations is a later compatible extension.

**Rationale**: Explicit relationship status satisfies the requirement not to present
inference as fact. Deferring multi-process collection keeps the first collector's
sequence ordering unambiguous.

**Alternatives considered**:

- Infer all lineage from timestamps and names: rejected as unsupported causality.
- Implement distributed ordering in the first release: rejected as unnecessary for
  the local MVP and likely to complicate deterministic replay.

## Canonical Ordering and Queue Pressure

**Decision**: The adapter performs bounded normalization and a non-blocking enqueue.
One collector assigns a strictly increasing sequence per trace at commit time.
Queue policy preserves lifecycle, errors, final model records, and omission counters;
it discards stream deltas first and duplicate oversized content second.

**Rationale**: A single writer gives deterministic order and simple SQLite behavior.
Explicit omission records keep the trace honest under pressure while recorder work
remains isolated from the run.

**Alternatives considered**:

- Block producers when full: rejected because it changes run timing and behavior.
- Unbounded buffering: rejected because large tool output can exhaust memory.
- Sequence at each producer: rejected because multi-producer sequences cannot form a
  canonical total order without additional coordination.

## Portable Storage and Query Index

**Decision**: Treat a versioned `.afr` bundle as the source of truth. Store ordered
records as append-only JSONL, findings separately, large payloads as SHA-256-addressed
zstandard blobs, and finalized integrity hashes in `checksums.sha256`. Build a
SQLite WAL index with Python's `sqlite3`; never include the database in exports.

**Rationale**: JSONL supports append and crash recovery, content addressing avoids
duplication, and an expendable index keeps the portable contract independent from a
database engine. One writer plus read-only query connections does not require an ORM
or async database abstraction.

**Alternatives considered**:

- SQLite as the portable source of truth: rejected because it couples compatibility
  to schema migrations and complicates partial-run portability.
- SQLAlchemy and Alembic: rejected for the first release because direct SQL is
  smaller and the database can always be regenerated.
- OpenTelemetry export as the canonical format: rejected because required context,
  condensation, workspace, and finding data exceed current semantic conventions;
  compatible field mapping remains possible later.

## Desktop UI and Concurrency

**Decision**: Use PySide6 with model/view widgets, `QGraphicsView` for the virtualized
swimlane timeline, and one selection controller. Execute indexing, bundle I/O,
diagnostics, decompression, and large queries in workers; deliver immutable results
to the GUI thread through Qt signals.

**Rationale**: PySide6 satisfies the native Python desktop requirement. Qt's
model/view and scene architecture supports large, synchronized, accessible views
without a browser runtime.

**Alternatives considered**:

- Browser-based UI: rejected by product scope.
- Custom painting all widgets directly: rejected because it weakens accessibility,
  testing, and virtualization.
- Let widgets query SQLite directly: rejected because it couples presentation to
  persistence and risks GUI stalls.

## Service Boundaries

**Decision**: Define typed Python protocols for trace queries, bundle import/export,
subscriptions, and diagnostics. Services contain no Qt imports. Qt workers adapt
service results for presentation.

**Rationale**: Headless services make the contracts testable and reusable by both
CLI and GUI while keeping diagnostics separate from capture.

**Alternatives considered**:

- Local HTTP API: rejected because an in-process protocol is simpler and the first
  release has no remote client.
- Put query logic in view models: rejected because it prevents headless validation
  and mixes UI state with domain behavior.

## Diagnostics

**Decision**: Implement repeated-tool-call, recurring-failure, and no-progress rules
first. Each finding includes detector identity, severity, confidence, explanation,
recommendation, affected spans, and resolvable evidence IDs. Optional model analysis
accepts a bounded evidence packet and returns the same validated finding schema with
no tools or workspace access.

**Rationale**: Deterministic detectors establish measurable value and a stable
finding contract. Evidence validation enforces the constitution regardless of the
finding producer.

**Alternatives considered**:

- Start with model-only diagnosis: rejected because results would be less repeatable
  and capture would appear dependent on interpretation.
- Store unsupported findings with warnings: rejected because displayed claims must
  have evidence; invalid findings are rejected.

## Packaging and Validation

**Decision**: Keep PySide6, zstandard, platformdirs, Pydantic, and `openhands-sdk` in
the recorder package dependencies. Add pytest-qt to repository development
dependencies. Expose `agent-flight-recorder` as the GUI command and a `validate`
subcommand for headless bundle checks. Use PyInstaller for the first Linux package.

**Rationale**: Dependency ownership stays with the product that needs it. A headless
validator proves portability independently from Qt rendering, and PyInstaller is
already used in this repository.

**Alternatives considered**:

- Add PySide6 to root runtime packages: rejected because unrelated SDK consumers do
  not need Qt.
- Ship all three desktop platforms initially: rejected until trace and GUI contracts
  stabilize on Linux.

## Resolved Clarifications

No planning unknowns remain. The plan fixes the first-release boundary at one local
process, chooses the `.afr` bundle as canonical storage, uses explicit context
provenance and relationship status, and keeps optional interpretive analysis outside
capture.
