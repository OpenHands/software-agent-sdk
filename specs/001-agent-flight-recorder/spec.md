# Feature Specification: Agent Flight Recorder

**Feature Branch**: `001-agent-flight-recorder`

**Created**: 2026-09-01

**Status**: Draft

**Input**: User description: "Based on the Agent Flight Recorder design, write a
specification."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Record and Replay an Agent Run (Priority: P1)

As an agent developer, I can record an OpenHands run and replay its ordered
activity so that I can understand what happened without reconstructing the run
from terminal logs.

**Why this priority**: A reliable record is the foundation for every
investigation and diagnostic capability.

**Independent Test**: Record a representative local run, reopen the resulting
trace in a clean recorder installation, and verify that its activity, ordering,
relationships, status, and usage totals are preserved.

**Acceptance Scenarios**:

1. **Given** recording is enabled for an OpenHands run, **When** the run produces
   agent, model, tool, workspace, test, or status activity, **Then** each observed
   activity appears once in a stable order with its source and relationships.
2. **Given** a completed trace, **When** a user exports and reimports it, **Then**
   the replay contains the same ordered activity, relationships, run result, and
   usage totals.
3. **Given** the recorder encounters a failure, **When** the observed run
   continues, **Then** the recorder reports its failure without changing the
   observed run's result.

---

### User Story 2 - Investigate a Run Visually (Priority: P2)

As a run investigator, I can navigate a desktop timeline of agents, model calls,
tools, tests, and changes so that I can identify what happened, when it happened,
and where time and cost were spent.

**Why this priority**: A synchronized visual investigation turns recorded data
into a practical debugging tool.

**Independent Test**: Open a trace containing concurrent agent activity, model
calls, tools, failures, and usage data, then answer a fixed set of investigation
questions using only the trace workspace.

**Acceptance Scenarios**:

1. **Given** a recorded run, **When** the user opens it, **Then** the application
   shows stable agent lanes, elapsed time, parallel activity, run status, errors,
   token usage, and cost where available.
2. **Given** an activity is selected in the hierarchy, timeline, or findings,
   **When** the selection changes, **Then** all linked views show the same
   activity and its available input, output, metrics, changes, and evidence.
3. **Given** recording is still active, **When** new activity is committed,
   **Then** it appears without requiring the trace to be reopened or disrupting
   the current investigation.

---

### User Story 3 - Inspect Model Context Changes (Priority: P3)

As a context engineer, I can inspect what was visible to a model call and compare
it with earlier calls so that I can identify context growth, loss, and
condensation effects.

**Why this priority**: Context changes frequently explain otherwise confusing
agent decisions and repeated work.

**Independent Test**: Open a run that contains multiple model calls and a
condensation event, then verify the call context, previous-call differences,
forgotten activity, and replacement summary.

**Acceptance Scenarios**:

1. **Given** an authoritative model-call record is available, **When** the user
   inspects that call, **Then** the application shows its visible input, visible
   output, usage, latency, cost, finish reason, and contributing activity where
   known.
2. **Given** authoritative input is unavailable, **When** the application shows
   reconstructed context, **Then** it labels the view as reconstructed and
   identifies missing evidence.
3. **Given** a condensation occurred, **When** the user inspects it, **Then** the
   application shows which activity was forgotten and what summary replaced it.

---

### User Story 4 - Trace Agent Delegation (Priority: P4)

As an orchestration designer, I can follow parent and child agents, handoffs, and
parallel branches so that I can determine whether delegated work contributed to
the final result.

**Why this priority**: Multi-agent runs cannot be understood accurately from a
single flat event sequence.

**Independent Test**: Open a seeded run with parallel delegated agents and verify
that each branch, handoff, returned result, duration, and contribution is
navigable from one trace.

**Acceptance Scenarios**:

1. **Given** a run with delegated agents, **When** the trace is opened, **Then**
   verified parent-child relationships and parallel execution are visible in one
   hierarchy and timeline.
2. **Given** a relationship cannot be verified, **When** it is displayed,
   **Then** the application marks it as unresolved or inferred rather than fact.
3. **Given** a delegated agent is selected, **When** its details are opened,
   **Then** the user can inspect its handoff, returned result, activity, duration,
   and usage totals.

---

### User Story 5 - Diagnose Unproductive Behavior (Priority: P5)

As an agent developer, I can review evidence-backed findings for loops, recurring
failures, context problems, poor handoffs, and premature completion so that I can
make a focused improvement to the next run.

**Why this priority**: Findings reduce investigation time, but they depend on a
trustworthy trace and must never replace the underlying evidence.

**Independent Test**: Analyze labeled traces containing known productive and
unproductive patterns, then verify finding accuracy and evidence navigation.

**Acceptance Scenarios**:

1. **Given** a trace contains a supported unproductive pattern, **When** analysis
   runs, **Then** the resulting finding identifies its category, severity,
   confidence, explanation, recommendation, and supporting evidence.
2. **Given** a finding is selected, **When** the user follows its evidence,
   **Then** each cited record or activity opens in the trace workspace.
3. **Given** a proposed finding cites missing evidence, **When** results are
   validated, **Then** the unsupported finding is rejected.
4. **Given** optional interpretive analysis is unavailable, **When** the trace is
   opened, **Then** recording, replay, and deterministic findings remain usable.

### Edge Cases

- A process stops before a run finishes; committed activity remains available and
  the run is identified as incomplete.
- Duplicate activity is received during retry or replay; it does not create
  duplicate records or inflate totals.
- Parent activity is missing; the child remains visible with an unresolved
  relationship.
- An unfamiliar future activity type is recorded; it remains inspectable without
  preventing the rest of the trace from loading.
- Activity volume exceeds the live buffer; essential lifecycle, error, final
  response, and loss-counter records remain available.
- The viewer closes or becomes unavailable while recording continues; reopening
  resumes from the last committed activity.
- A trace contains incomplete spans or inconsistent timestamps; ordering remains
  deterministic and uncertainty is visible.
- A very large trace is opened; the initial overview remains responsive while
  additional detail becomes available.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST record the visible execution history of a local
  OpenHands run.
- **FR-002**: The recorded history MUST include available conversation, agent,
  model, tool, workspace, test, condensation, retry, error, usage, and termination
  activity.
- **FR-003**: Every recorded activity MUST have a stable identity, ordering value,
  source, timestamp, type, and relationship information when available.
- **FR-004**: The system MUST distinguish observed facts, derived measurements,
  reconstructed views, and interpretive findings.
- **FR-005**: The system MUST expose missing or uncertain relationships and MUST
  NOT present inferred relationships as verified facts.
- **FR-006**: Recorder failure MUST be isolated from the observed run and MUST NOT
  alter that run's behavior, result, or termination reason.
- **FR-007**: Users MUST be able to export a complete trace and import it into an
  installation with no prior data.
- **FR-008**: Exported traces MUST include integrity information and enough data
  to reproduce the same ordered history, relationships, findings, and usage
  totals after import.
- **FR-009**: Reprocessing the same recorded activity MUST be idempotent.
- **FR-010**: Users MUST be able to browse runs by status, date, repository,
  model, and finding severity.
- **FR-011**: The trace workspace MUST provide a synchronized hierarchy,
  time-based view, detail inspector, workspace-change track, and findings view.
- **FR-012**: The time-based view MUST preserve stable agent lanes and represent
  duration, concurrency, retries, state changes, failures, and incomplete work.
- **FR-013**: Selecting an item or evidence reference in one view MUST select and
  reveal the same item in all applicable views.
- **FR-014**: The application MUST display input, visible output, context,
  workspace changes, usage, cost, latency, retries, and evidence when available
  for the selected activity.
- **FR-015**: The application MUST distinguish authoritative model input from
  reconstructed context and identify the evidence used for reconstruction.
- **FR-016**: The application MUST show context differences between consecutive
  model calls and the recorded effects of condensation.
- **FR-017**: The system MUST represent parent-child agent relationships,
  delegation handoffs, parallel branches, returned results, and per-agent totals.
- **FR-018**: The system MUST detect repeated tool calls, recurring failures, and
  iterations without progress using repeatable rules.
- **FR-019**: Every finding MUST cite existing evidence and identify its origin,
  confidence, explanation, and recommended next action.
- **FR-020**: Optional interpretive analysis MUST use only recorded evidence, MUST
  remain separate from the observed run, and MUST NOT modify its workspace.
- **FR-021**: The system MUST continue recording when no viewer is attached and
  MUST resume display from the last committed activity when a viewer reconnects.
- **FR-022**: Users MUST be able to navigate primary investigation flows by
  keyboard, and status MUST NOT depend on color alone.
- **FR-023**: Unknown activity types and incomplete runs MUST remain inspectable
  without preventing known activity from loading.
- **FR-024**: Under recording pressure, the system MUST preserve run lifecycle,
  errors, final model activity, and counts describing omitted activity.

### Key Entities

- **Trace**: The complete recorded history of one observed run, including its
  lifecycle, aggregate usage, result, and integrity state.
- **Activity**: One ordered observation from the run, with identity, source,
  timing, type, relationships, and associated data.
- **Span**: A duration-based unit of work, such as a run, agent, iteration, model
  call, tool call, test, delegation, or condensation.
- **Agent Relationship**: A verified, inferred, or unresolved connection between
  parent and child agent work.
- **Artifact**: A workspace change, test result, or other output associated with
  recorded activity.
- **Finding**: An evidence-backed diagnosis with category, severity, confidence,
  explanation, recommendation, and references to supporting trace data.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In replay tests, 100% of observed activity is represented exactly
  once and in the same order after export and import.
- **SC-002**: Injected recorder, storage, viewer, and analyzer failures change 0%
  of observed run outcomes.
- **SC-003**: At least 90% of target developers can identify the final stop
  reason, the most expensive model call, and the cause of a seeded failure within
  five minutes without reading raw logs.
- **SC-004**: New committed activity becomes visible to an attached local viewer
  within 500 milliseconds for at least 95% of updates.
- **SC-005**: A trace containing 100,000 activities presents an interactive
  overview within three seconds, and at least 95% of item selections update
  linked details within 100 milliseconds on the reference workstation.
- **SC-006**: Recording adds less than 5% median completion time across the
  reference run corpus when live token display is disabled.
- **SC-007**: Each supported deterministic detector achieves at least 90%
  precision and 90% recall on its labeled trace corpus.
- **SC-008**: 100% of displayed findings cite valid evidence that users can open
  directly from the finding.
- **SC-009**: In accessibility tests, all primary investigation actions can be
  completed using only a keyboard and without relying on color identification.

## Assumptions

- The first release records local OpenHands runs; remote runs are a later feature
  that will use the same trace concepts.
- The product observes and explains runs but does not launch, pause, resume, or
  otherwise control them.
- The first release supports one developer investigating traces on the same
  workstation where they are stored.
- Deterministic findings are available without optional interpretive analysis.
- Exact model context is shown only when an authoritative record exists;
  otherwise, the application presents a clearly labeled reconstruction.
- Trace comparison, remote collaboration, and generalized log-management use
  cases are outside the initial scope.
