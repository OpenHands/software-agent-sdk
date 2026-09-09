# Implementation Plan: Agent Flight Recorder

**Branch**: `001-agent-flight-recorder` | **Date**: 2026-09-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-agent-flight-recorder/spec.md`

**Note**: This plan covers research and design. Implementation tasks are generated separately by `/speckit-tasks`.

## Summary

Build a local desktop recorder and trace viewer for OpenHands runs. An in-process
adapter converts SDK events, completion logs, and metrics into immutable versioned
records, then hands them to a bounded queue. A single background collector assigns
canonical sequence numbers and appends records to a portable `.afr` bundle while
maintaining a rebuildable SQLite query index. UI-agnostic services project those
records into trace trees, agent swimlanes, context comparisons, and evidence-backed
findings consumed by a PySide6 desktop application.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: `openhands-sdk`, PySide6, Pydantic 2, zstandard,
platformdirs; Python standard-library `sqlite3`, `json`, `zipfile`, `hashlib`,
`queue`, and `threading`

**Storage**: Versioned `.afr` directory/ZIP bundle as source of truth; SQLite in
WAL mode as a local, rebuildable query index; content-addressed compressed blobs
for large payloads

**Testing**: pytest, pytest-qt with `QT_QPA_PLATFORM=offscreen`, seeded trace
fixtures, integration replay tests, and focused performance benchmarks

**Target Platform**: Linux desktop first; Windows and macOS after trace and GUI
contracts stabilize

**Project Type**: Distributable Python package with recorder library, CLI
validation commands, and native desktop application

**Performance Goals**: Less than 5% median run overhead with token streaming
disabled; callback p99 below 50 ms; committed records visible within 500 ms p95;
100,000-record overview interactive within 3 seconds; indexed selection updates
within 100 ms p95

**Constraints**: Recording cannot influence the observed run; callbacks perform no
database or network I/O; memory remains bounded; capture is deterministic;
diagnosis reads only committed records; SQLite, decompression, analysis, and bundle
import never execute on the GUI thread

**Scale/Scope**: One local developer and one local OpenHands run per trace in the
first release; traces up to 100,000 records; local conversations first, with the
data model retaining producer and lineage fields needed for later remote and
multi-process support

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Observe Without Influence — PASS**: SDK callbacks only normalize and enqueue;
  queue overflow and collector errors become recorder warnings. No recorder path
  invokes conversation control APIs or changes run state.
- **Facts Before Interpretation — PASS**: The append-only record is authoritative.
  Derived spans, projections, and findings retain evidence IDs and explicit
  provenance or relationship confidence.
- **Capture and Diagnosis Stay Separate — PASS**: Deterministic capture and storage
  have no dependency on diagnostics. Rules and optional interpretation consume
  committed records through read-only services and cannot access workspace tools.
- **Enduring Boundaries — PASS**: The product observes existing runs and does not
  replace conversation persistence or launch, pause, resume, or stop agents.

**Post-design re-check**: PASS. The data model encodes provenance and uncertainty;
the service contracts keep capture, querying, diagnosis, and Qt presentation
separate; bundle import reconstructs an index rather than replacing OpenHands state.

## Project Structure

### Documentation (this feature)

```text
specs/001-agent-flight-recorder/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── python-services.md
│   └── trace-bundle.md
└── tasks.md
```

### Source Code (repository root)

```text
agent-flight-recorder/
├── AGENTS.md
├── pyproject.toml
├── README.md
└── src/flight_recorder/
    ├── __init__.py
    ├── __main__.py
    ├── app.py
    ├── adapter/
    │   ├── conversation.py
    │   ├── llm.py
    │   └── propagation.py
    ├── collector/
    │   ├── normalize.py
    │   ├── persistence.py
    │   └── queue.py
    ├── diagnostics/
    │   ├── analyzer.py
    │   ├── rules.py
    │   └── schemas.py
    ├── models/
    │   ├── database.py
    │   ├── envelopes.py
    │   └── view_models.py
    ├── services/
    │   ├── bundles.py
    │   ├── diagnostics.py
    │   ├── repository.py
    │   └── subscriptions.py
    ├── gui/
    │   ├── controllers/
    │   ├── inspector/
    │   ├── models/
    │   ├── timeline/
    │   ├── main_window.py
    │   └── workers.py
    └── cli.py

tests/agent_flight_recorder/
├── unit/
├── integration/
├── gui/
├── e2e/
├── performance/
└── fixtures/traces/
```

**Structure Decision**: Extend the existing `agent-flight-recorder/` design
directory into an independent `src`-layout workspace package. Keep capture,
storage, diagnostics, services, and GUI as explicit boundaries. Mirror the package
under the repository-level `tests/agent_flight_recorder/` domain, consistent with
the monorepo test organization. Add the package to the root uv workspace, but keep
PySide6 and recorder-specific dependencies in its package metadata.

## Complexity Tracking

No constitution violations require justification.
