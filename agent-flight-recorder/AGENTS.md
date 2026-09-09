# Agent Flight Recorder Guidance

- Preserve the recorder constitution in `.specify/memory/constitution.md`.
- Callback paths must remain bounded, non-blocking, and exception-contained.
- Treat `.afr` records as authoritative; SQLite is a rebuildable index.
- Keep capture, diagnostics, services, and Qt presentation separate.
- Derived data and findings must retain resolvable evidence identifiers.
- Put tests under `tests/agent_flight_recorder/` and use `pytest-qt` for GUI behavior.
- Run pre-commit on every changed file and focused tests after each logical change.