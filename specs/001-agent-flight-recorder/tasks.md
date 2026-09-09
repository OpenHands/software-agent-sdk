---
description: "Implementation tasks for Agent Flight Recorder"
---

# Tasks: Agent Flight Recorder

**Input**: Design documents from `specs/001-agent-flight-recorder/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`,
`contracts/`, `quickstart.md`

**Tests**: Tests are included because the specification defines independent tests,
replay guarantees, failure-isolation requirements, accessibility outcomes, and
quantitative performance targets.

**Organization**: Tasks are grouped by user story so each story can be implemented
and validated as an independently useful increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes separate files and has no
  dependency on another incomplete task in the same phase.
- **[Story]**: Maps the task to a user story from `spec.md`.
- Every task names the file or directory it changes.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the distributable package and repository integration needed by
all implementation phases.

- [X] T001 Create package guidance from the approved plan and repository rules in agent-flight-recorder/AGENTS.md
- [X] T002 Create `openhands-agent-flight-recorder` metadata, Python 3.12 requirement, runtime dependencies, build backend, and command entry point in agent-flight-recorder/pyproject.toml
- [X] T003 Add agent-flight-recorder as a uv workspace member and add pytest-qt to development dependencies in pyproject.toml
- [X] T004 Create the `flight_recorder` package and typed-package marker in agent-flight-recorder/src/flight_recorder/__init__.py and agent-flight-recorder/src/flight_recorder/py.typed
- [X] T005 [P] Create package entry points and argument parser shell in agent-flight-recorder/src/flight_recorder/__main__.py and agent-flight-recorder/src/flight_recorder/cli.py
- [X] T006 [P] Create test-domain fixtures and headless Qt configuration in tests/agent_flight_recorder/conftest.py
- [X] T007 Regenerate uv.lock and verify `make build` resolves the new workspace package in uv.lock

**Checkpoint**: The empty package installs, imports, and exposes the planned command.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared records, bundle primitives, index schema, failures,
and bounded collection infrastructure used by every user story.

**Critical**: No user story implementation begins until this phase passes its unit
tests.

### Tests

- [X] T008 [P] Add model validation and lifecycle transition tests in tests/agent_flight_recorder/unit/test_envelopes.py
- [X] T009 [P] Add bounded queue priority and non-blocking behavior tests in tests/agent_flight_recorder/unit/test_queue.py
- [X] T010 [P] Add SQLite schema, uniqueness, and rebuild transaction tests in tests/agent_flight_recorder/unit/test_database.py
- [X] T011 [P] Add bundle path, JSONL truncation, digest, and format-version tests in tests/agent_flight_recorder/unit/test_bundle_primitives.py

### Implementation

- [X] T012 [P] Implement typed recorder failures and machine-readable failure codes in agent-flight-recorder/src/flight_recorder/errors.py
- [X] T013 [P] Implement Trace, Span, Record, Provenance, usage, artifact, finding, and committed-batch models in agent-flight-recorder/src/flight_recorder/models/envelopes.py
- [X] T014 [P] Implement immutable run, timeline, span, selection, and query view models in agent-flight-recorder/src/flight_recorder/models/view_models.py
- [X] T015 Implement run and span lifecycle validation, including terminal stop-reason rules, in agent-flight-recorder/src/flight_recorder/models/envelopes.py
- [X] T016 Implement the non-blocking bounded record queue and critical-record drop policy in agent-flight-recorder/src/flight_recorder/collector/queue.py
- [X] T017 Implement normalized producer records and stable identifiers in agent-flight-recorder/src/flight_recorder/collector/normalize.py
- [X] T018 Implement SQLite WAL schema creation, constraints, indexes, and transactions in agent-flight-recorder/src/flight_recorder/models/database.py
- [X] T019 Implement bundle path validation, JSONL parsing, content digest verification, and checksum helpers in agent-flight-recorder/src/flight_recorder/services/bundles.py
- [X] T020 Implement platform-correct data, cache, and trace directory resolution in agent-flight-recorder/src/flight_recorder/config.py

**Checkpoint**: Shared models reject invalid state, collection remains bounded and
non-blocking, and bundle/index primitives are independently testable.

---

## Phase 3: User Story 1 - Record and Replay an Agent Run (Priority: P1) MVP

**Goal**: Record one local OpenHands run without influencing it, persist a portable
trace, validate/import/export it, and replay the same ordered activity.

**Independent Test**: Record a representative local run, import it into an empty
data directory, export it again, and verify identical record order, relationships,
run result, and usage totals.

### Tests for User Story 1

- [X] T021 [P] [US1] Add OpenHands event, completion-log, metrics, and condensation normalization tests in tests/agent_flight_recorder/unit/test_normalize.py
- [X] T022 [P] [US1] Add collector ordering, deduplication, batching, shutdown, and omission-counter tests in tests/agent_flight_recorder/unit/test_collector.py
- [X] T023 [P] [US1] Add portable bundle contract and round-trip tests in tests/agent_flight_recorder/integration/test_bundle_roundtrip.py
- [X] T024 [P] [US1] Add injected callback, collector, and storage failure-isolation tests in tests/agent_flight_recorder/integration/test_failure_isolation.py
- [X] T025 [P] [US1] Add a deterministic local conversation fixture driver in tests/agent_flight_recorder/fixtures/conversation.py
- [X] T026 [US1] Add end-to-end local recording and replay coverage using the fixture driver in tests/agent_flight_recorder/e2e/test_record_replay.py

### Implementation for User Story 1

- [X] T027 [P] [US1] Implement Event callback normalization with source IDs, timestamps, parent IDs, and discriminated payloads in agent-flight-recorder/src/flight_recorder/adapter/conversation.py
- [X] T028 [P] [US1] Implement completion-log parsing and response/usage correlation in agent-flight-recorder/src/flight_recorder/adapter/llm.py
- [X] T029 [P] [US1] Implement condensation payload normalization from forgotten IDs, summary, offset, and response ID in agent-flight-recorder/src/flight_recorder/collector/normalize.py
- [X] T030 [US1] Implement the Recorder facade with contained callbacks, metrics snapshots, idempotent close, and warning counters in agent-flight-recorder/src/flight_recorder/recorder.py
- [X] T031 [US1] Implement the single-writer collector with canonical sequence assignment, durable append, SQLite projection, and committed-batch publication in agent-flight-recorder/src/flight_recorder/collector/persistence.py
- [X] T032 [US1] Implement trace finalization, validation, transactional import, index rebuild, and ZIP export in agent-flight-recorder/src/flight_recorder/services/bundles.py
- [X] T033 [US1] Implement ordered record iteration, run lookup, and usage aggregation in agent-flight-recorder/src/flight_recorder/services/repository.py
- [X] T034 [US1] Implement `record`, `validate`, `import`, and `export` command behavior and exit codes in agent-flight-recorder/src/flight_recorder/cli.py
- [X] T035 [US1] Wire the command entry point to recorder, bundle, and repository services in agent-flight-recorder/src/flight_recorder/__main__.py

**Checkpoint**: User Story 1 is a complete headless MVP. The recorder can be used and
validated without the desktop UI or diagnostic engine.

---

## Phase 4: User Story 2 - Investigate a Run Visually (Priority: P2)

**Goal**: Open recorded or active traces in a native desktop workspace with a run
list, hierarchy, stable swimlanes, synchronized inspector, and live updates.

**Independent Test**: Open a seeded concurrent trace and answer the prescribed run
status, ordering, failure, duration, token, and cost questions using only the GUI.

### Tests for User Story 2

- [X] T036 [P] [US2] Add deterministic timeline projection, stable lane, incomplete span, and visible-range tests in tests/agent_flight_recorder/unit/test_timeline_projection.py
- [X] T037 [P] [US2] Add repository run-filter, span-detail, workspace-change, and generic-record query tests in tests/agent_flight_recorder/unit/test_repository.py
- [X] T038 [P] [US2] Add selection synchronization and circular-update prevention tests in tests/agent_flight_recorder/gui/test_selection.py
- [X] T039 [P] [US2] Add run table, trace tree, timeline hit-testing, zoom, and keyboard tests in tests/agent_flight_recorder/gui/test_trace_workspace.py
- [X] T040 [P] [US2] Add active-trace subscription resume, duplicate-delivery, and slow-subscriber tests in tests/agent_flight_recorder/integration/test_subscriptions.py
- [X] T041 [US2] Add desktop trace investigation end-to-end coverage in tests/agent_flight_recorder/e2e/test_desktop_investigation.py

### Implementation for User Story 2

- [X] T042 [P] [US2] Implement run list filtering and trace-tree models in agent-flight-recorder/src/flight_recorder/gui/models/run_table.py and agent-flight-recorder/src/flight_recorder/gui/models/trace_tree.py
- [X] T043 [P] [US2] Implement deterministic lane and span layout projection in agent-flight-recorder/src/flight_recorder/gui/timeline/layout.py
- [X] T044 [P] [US2] Implement virtualized timeline graphics items and visual states in agent-flight-recorder/src/flight_recorder/gui/timeline/items.py
- [X] T045 [US2] Implement timeline scene, hit testing, zoom, minimap, and keyboard navigation in agent-flight-recorder/src/flight_recorder/gui/timeline/scene.py and agent-flight-recorder/src/flight_recorder/gui/timeline/view.py
- [X] T046 [P] [US2] Implement the canonical cross-view selection controller in agent-flight-recorder/src/flight_recorder/gui/controllers/selection.py
- [X] T047 [P] [US2] Implement summary, input, output, metrics, changes, and evidence inspector tabs in agent-flight-recorder/src/flight_recorder/gui/inspector/widget.py and agent-flight-recorder/src/flight_recorder/gui/inspector/metrics.py
- [X] T048 [US2] Implement resumable committed-batch subscriptions that cannot block collection in agent-flight-recorder/src/flight_recorder/services/subscriptions.py
- [X] T049 [US2] Implement cancellable Qt workers for repository, bundle, and subscription operations in agent-flight-recorder/src/flight_recorder/gui/workers.py
- [X] T050 [US2] Assemble the run list, hierarchy, swimlane timeline, inspector, and workspace-change track in agent-flight-recorder/src/flight_recorder/gui/main_window.py
- [X] T051 [US2] Implement desktop startup, trace opening, settings restoration, and clean detach behavior in agent-flight-recorder/src/flight_recorder/app.py

**Checkpoint**: User Story 2 can be demonstrated entirely with a checked-in trace
fixture and does not require context analysis, delegation support, or diagnostics.

---

## Phase 5: User Story 3 - Inspect Model Context Changes (Priority: P3)

**Goal**: Show authoritative or reconstructed model context, previous-call
differences, and recorded condensation effects with explicit provenance.

**Independent Test**: Open a trace with multiple model calls and condensation, then
verify context provenance, differences, forgotten events, and replacement summary.

### Tests for User Story 3

- [X] T052 [P] [US3] Add authoritative versus reconstructed context provenance tests in tests/agent_flight_recorder/unit/test_context_projection.py
- [X] T053 [P] [US3] Add previous-call diff and unresolved condensation-event tests in tests/agent_flight_recorder/unit/test_context_diff.py
- [X] T054 [P] [US3] Add context inspector interaction and provenance badge tests in tests/agent_flight_recorder/gui/test_context_inspector.py
- [X] T055 [US3] Add condensation investigation end-to-end coverage in tests/agent_flight_recorder/e2e/test_condensation_investigation.py

### Implementation for User Story 3

- [X] T056 [US3] Implement exact completion-log context lookup and event-based reconstructed context projection in agent-flight-recorder/src/flight_recorder/services/repository.py
- [X] T057 [P] [US3] Implement previous-call structured context differences in agent-flight-recorder/src/flight_recorder/gui/inspector/diff.py
- [X] T058 [P] [US3] Implement exact/reconstructed provenance and condensation before/after views in agent-flight-recorder/src/flight_recorder/gui/inspector/context.py
- [X] T059 [US3] Connect context source events and condensation IDs to timeline navigation in agent-flight-recorder/src/flight_recorder/gui/controllers/selection.py

**Checkpoint**: User Story 3 works against fixture bundles even if no live recorder or
diagnostic analyzer is running.

---

## Phase 6: User Story 4 - Trace Agent Delegation (Priority: P4)

**Goal**: Represent verified, inferred, and unresolved parent-child agents, parallel
branches, handoffs, returns, duration, and per-agent usage.

**Independent Test**: Open a seeded parallel delegation trace and navigate every
agent branch, handoff, return, relationship status, duration, and usage total.

### Tests for User Story 4

- [X] T060 [P] [US4] Add relationship status, trace propagation, and unresolved-parent model tests in tests/agent_flight_recorder/unit/test_lineage.py
- [X] T061 [P] [US4] Add parallel branch ordering and per-agent aggregation tests in tests/agent_flight_recorder/unit/test_agent_projection.py
- [X] T062 [P] [US4] Add orchestration hierarchy, lineage style, and handoff inspector tests in tests/agent_flight_recorder/gui/test_orchestration.py
- [X] T063 [US4] Add parallel delegated-run investigation coverage in tests/agent_flight_recorder/e2e/test_delegation_trace.py

### Implementation for User Story 4

- [X] T064 [P] [US4] Implement trace and parent-agent context propagation helpers in agent-flight-recorder/src/flight_recorder/adapter/propagation.py
- [X] T065 [US4] Implement verified, inferred, and unresolved relationship projection in agent-flight-recorder/src/flight_recorder/services/repository.py
- [X] T066 [P] [US4] Add nested child-agent lanes, parallel branches, and lineage styles in agent-flight-recorder/src/flight_recorder/gui/timeline/layout.py and agent-flight-recorder/src/flight_recorder/gui/timeline/items.py
- [X] T067 [P] [US4] Add handoff, returned result, per-agent metrics, and relationship provenance details in agent-flight-recorder/src/flight_recorder/gui/inspector/widget.py
- [X] T068 [US4] Add orchestration navigation and parent-child expansion to agent-flight-recorder/src/flight_recorder/gui/main_window.py

**Checkpoint**: User Story 4 is independently verifiable with a seeded multi-agent
trace and labels every non-authoritative relationship.

---

## Phase 7: User Story 5 - Diagnose Unproductive Behavior (Priority: P5)

**Goal**: Produce and navigate evidence-backed findings for repeated calls,
recurring failures, no-progress iterations, context issues, delegation issues, cost
hotspots, and premature completion.

**Independent Test**: Analyze labeled positive and negative trace corpora, meet the
specified precision/recall thresholds, and open every cited evidence item.

### Tests for User Story 5

- [X] T069 [P] [US5] Add normalized fingerprint and repeated-tool-call detector boundary tests in tests/agent_flight_recorder/unit/test_repeated_tool_rule.py
- [X] T070 [P] [US5] Add recurring-failure and no-progress detector boundary tests in tests/agent_flight_recorder/unit/test_progress_rules.py
- [X] T071 [P] [US5] Add finding schema, missing-evidence rejection, and optional analyzer failure tests in tests/agent_flight_recorder/unit/test_diagnostics.py
- [X] T072 [P] [US5] Create the labeled positive and negative trace corpus manifest and fixtures from tests/agent_flight_recorder/fixtures/traces/diagnostics/manifest.json
- [X] T073 [US5] Add detector precision, recall, and evidence-validity evaluation in tests/agent_flight_recorder/integration/test_diagnostic_quality.py
- [X] T074 [P] [US5] Add findings filtering, selection, and evidence navigation tests in tests/agent_flight_recorder/gui/test_findings.py
- [X] T075 [US5] Add unproductive-run diagnosis end-to-end coverage in tests/agent_flight_recorder/e2e/test_diagnostic_investigation.py

### Implementation for User Story 5

- [X] T076 [P] [US5] Implement stable tool, error, iteration, context, and delegation fingerprints in agent-flight-recorder/src/flight_recorder/diagnostics/rules.py
- [X] T077 [US5] Implement repeated-tool-call, recurring-failure, and no-progress detectors in agent-flight-recorder/src/flight_recorder/diagnostics/rules.py
- [X] T078 [US5] Implement context, delegation, premature-finish, and budget detectors in agent-flight-recorder/src/flight_recorder/diagnostics/rules.py
- [X] T079 [P] [US5] Implement strict diagnostic input and finding schemas in agent-flight-recorder/src/flight_recorder/diagnostics/schemas.py
- [X] T080 [P] [US5] Implement bounded read-only interpretive analysis and structured result parsing in agent-flight-recorder/src/flight_recorder/diagnostics/analyzer.py
- [X] T081 [US5] Implement diagnostic orchestration, evidence resolution, rejection, and finding persistence in agent-flight-recorder/src/flight_recorder/services/diagnostics.py
- [X] T082 [P] [US5] Implement findings list, filters, severity states, and evidence links in agent-flight-recorder/src/flight_recorder/gui/models/findings.py
- [X] T083 [US5] Integrate findings and evidence navigation into agent-flight-recorder/src/flight_recorder/gui/main_window.py

**Checkpoint**: User Story 5 remains useful with interpretive analysis disabled and
never displays a finding whose evidence cannot be opened.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Validate behavior spanning stories, package the Linux application, and
document supported use.

- [X] T084 [P] Add 100,000-record trace fixture generation in tests/agent_flight_recorder/fixtures/generate_large_trace.py
- [X] T085 [P] Add callback latency, run overhead, live-update latency, first-render, and selection benchmarks in tests/agent_flight_recorder/performance/test_performance_targets.py
- [X] T086 Optimize measured collector, index, and projection bottlenecks in agent-flight-recorder/src/flight_recorder/collector/persistence.py and agent-flight-recorder/src/flight_recorder/services/repository.py
- [X] T087 [P] Add Linux PyInstaller specification and packaged-resource declarations in agent-flight-recorder/agent-flight-recorder.spec and agent-flight-recorder/pyproject.toml
- [X] T088 [P] Document installation, recording, validation, trace viewing, limitations, and troubleshooting in agent-flight-recorder/README.md
- [X] T089 Add package checks and headless GUI tests to the existing test workflow in .github/workflows/tests.yml
- [X] T090 Run every scenario in specs/001-agent-flight-recorder/quickstart.md and record any environment-specific corrections in specs/001-agent-flight-recorder/quickstart.md
- [X] T091 Run focused tests, the complete agent-flight-recorder suite, and pre-commit on every changed file listed by git status

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 — Setup**: Starts immediately.
- **Phase 2 — Foundation**: Depends on Phase 1 and blocks all stories.
- **Phase 3 — US1**: Depends on Phase 2; produces the MVP and real trace bundles.
- **Phase 4 — US2**: Depends on Phase 2 and can use seeded bundles; integration with
  live traces uses US1 subscriptions.
- **Phase 5 — US3**: Depends on Phase 2 and can use fixtures; full live context uses
  US1 completion records and US2 inspector/navigation.
- **Phase 6 — US4**: Depends on Phase 2 and can use fixtures; live delegated capture
  extends US1 adapters and US2 timeline views.
- **Phase 7 — US5**: Depends on Phase 2 and can run headlessly on fixtures; GUI
  navigation integrates with US2.
- **Phase 8 — Polish**: Depends on every story selected for release.

### User Story Completion Order

```text
Setup -> Foundation -> US1 (MVP)
                    -> US2 -> US3
                           -> US4
                           -> US5
```

US2 through US5 can start from seeded bundle fixtures after Foundation. The
recommended integration order is US1, US2, US3, US4, US5 because later stories add
views to the trace workspace rather than changing the canonical record contract.

### Within Each User Story

1. Add story tests and confirm they fail for the missing behavior.
2. Implement or extend story-specific models and projections.
3. Implement services before GUI integration.
4. Run the story's unit, integration, GUI, and end-to-end tests.
5. Stop at the checkpoint and demonstrate the independent user outcome.

## Parallel Opportunities

- Phase 1: T005 and T006 can run in parallel after metadata paths are agreed.
- Phase 2: T008-T011 and T012-T014 can run in parallel; lifecycle integration then
  proceeds through T015-T020.
- US1: Adapter tests and implementations T021-T029 can be split by event,
  completion, metrics, and bundle concerns.
- US2: Timeline projection, query, selection, widget, and subscription work in
  T036-T049 touches separate modules and can proceed concurrently.
- US3: Context projection, diff, and inspector work in T052-T058 can proceed in
  parallel after the shared context model is fixed.
- US4: Propagation, projection, timeline, and inspector work in T060-T067 can be
  split among independent modules.
- US5: Detector families, schemas, analyzer, fixtures, and GUI work in T069-T082
  provide the largest parallel workstream.
- Phase 8: Performance fixture, packaging, and documentation tasks can proceed in
  parallel before final workflow and quickstart validation.

## Parallel Examples

### User Story 1

```text
Task T021: Normalize SDK events and telemetry tests.
Task T023: Portable bundle round-trip contract tests.
Task T024: Recorder failure-isolation integration tests.
Task T027: Conversation event adapter.
Task T028: Completion-log adapter.
```

### User Story 2

```text
Task T042: Run table and trace tree models.
Task T043: Timeline layout projection.
Task T046: Selection controller.
Task T047: Inspector tabs.
Task T048: Committed-batch subscriptions.
```

### User Story 3

```text
Task T052: Context provenance tests.
Task T053: Context diff tests.
Task T057: Structured context differences.
Task T058: Context and condensation inspector.
```

### User Story 4

```text
Task T060: Lineage model tests.
Task T062: Orchestration GUI tests.
Task T064: Trace propagation helpers.
Task T067: Handoff inspector details.
```

### User Story 5

```text
Task T069: Repeated-tool detector tests.
Task T070: Progress detector tests.
Task T072: Labeled diagnostic corpus.
Task T079: Diagnostic schemas.
Task T080: Optional analyzer.
Task T082: Findings GUI model.
```

## Implementation Strategy

### MVP First

1. Complete Setup and Foundation.
2. Complete User Story 1 through T035.
3. Run US1 tests and bundle round-trip validation.
4. Stop and demonstrate headless recording, validation, import, export, and replay.

### Incremental Delivery

1. Add US2 for visual investigation using the same bundle contract.
2. Add US3 for context and condensation analysis.
3. Add US4 for orchestration traces.
4. Add US5 for evidence-backed diagnostics.
5. Complete release-wide performance, packaging, workflow, and documentation tasks.

### Task Discipline

- Keep source comments limited to non-obvious invariants and external workarounds.
- Follow the closest AGENTS.md before modifying each package.
- Run `uv run pre-commit run --files` after each changed file.
- Do not begin a dependent task until its model or service prerequisite is complete.
- Do not mark a story complete until its independent test passes.
