# Quickstart Validation: Agent Flight Recorder

This guide validates the repository implementation before packaging or release.

## Prerequisites

- Linux desktop or Linux CI runner
- Python 3.12 or newer
- `uv` compatible with the repository
- Repository dependencies configured with `make build`
- A test LLM configuration for live recording, or the checked-in deterministic
  conversation fixture

For headless GUI checks, set `QT_QPA_PLATFORM=offscreen`.

## 1. Validate the Package

From the repository root:

```bash
make build
uv run pre-commit run --files \
  $(find agent-flight-recorder tests/agent_flight_recorder -type f)
uv run pytest tests/agent_flight_recorder
```

Expected outcome:

- Package imports resolve from the uv workspace.
- Static checks pass.
- Model, lifecycle, queue, SQLite, bundle, collector, diagnostic, and GUI
  tests pass.

## 2. Inspect the Current CLI

From the repository root:

```bash
uv run agent-flight-recorder --help
uv run agent-flight-recorder validate --help
```

Expected outcome:

- The executable resolves from the workspace package.
- The `record`, `validate`, `import`, and `export` commands are listed.

## 3. Validate Bundle Behavior

```bash
uv run pytest \
  tests/agent_flight_recorder/unit/test_bundle_primitives.py \
  tests/agent_flight_recorder/integration/test_bundle_roundtrip.py
```

Expected outcome:

- Unsupported formats and unsafe ZIP paths are rejected.
- A truncated final JSONL record is ignored.
- Repeated imports do not duplicate indexed records.
- Content checksums are deterministic.

## 4. Validate Failure Isolation and Collection

```bash
uv run pytest \
  tests/agent_flight_recorder/integration/test_failure_isolation.py \
  tests/agent_flight_recorder/unit/test_queue.py \
  tests/agent_flight_recorder/unit/test_collector.py
```

Expected outcome:

- Invalid callback data does not escape into the observed run.
- Queue pressure discards stream deltas before lifecycle records.
- The collector assigns canonical sequence numbers and publishes committed batches.

## 5. Open the Current Desktop Shell

```bash
uv run agent-flight-recorder
```

Expected outcome:

- A native desktop window opens without starting a browser or local server.
- The three-panel trace workspace shell is visible.
- Close the window to end the command.

For automated headless verification:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/agent_flight_recorder/gui
```

## 6. Validate Investigation Workflows

```bash
QT_QPA_PLATFORM=offscreen uv run pytest \
  tests/agent_flight_recorder/e2e \
  tests/agent_flight_recorder/integration/test_diagnostic_quality.py
```

Expected outcome:

- Recorded bundles replay with preserved usage.
- Context, condensation, delegation, and finding evidence are navigable.
- Every deterministic detector meets the labeled-corpus quality threshold.

## 7. Validate Performance Targets

```bash
QT_QPA_PLATFORM=offscreen uv run pytest \
  tests/agent_flight_recorder/performance/test_performance_targets.py
```

To generate a portable 100,000-record fixture manually:

```bash
uv run python \
  tests/agent_flight_recorder/fixtures/generate_large_trace.py \
  /tmp/large-trace.afr
```

## 8. Build The Linux Application

```bash
uv run pyinstaller agent-flight-recorder/agent-flight-recorder.spec
```

Expected outcome: `dist/agent-flight-recorder` is created.

On headless Linux, smoke-test the binary with:

```bash
QT_QPA_PLATFORM=offscreen ./dist/agent-flight-recorder --help
```

PyInstaller may warn about unresolved Qt XCB libraries on a headless build host.
Native X11 launch requires the distribution packages providing `libxcb-cursor`,
`libxcb-keysyms`, `libxcb-render-util`, `libxcb-xkb`, and `libxkbcommon-x11`.

## Contract References

- [Data model](data-model.md)
- [Portable trace bundle](contracts/trace-bundle.md)
- [Python and command services](contracts/python-services.md)
