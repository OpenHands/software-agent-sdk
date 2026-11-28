# Failed undo retains its pending history

Base: `722e5d403cf75d4e032b79296cfaf178f8678a93` (v1.63.1).
Runtime: Linux, unprivileged user, CPython 3.13.18. Dependencies were installed
using `UV_FROZEN=1 make build` with the committed lock. No model or GPU is used.

Run from the checkout with the workspace environment:

```sh
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True uv run python .pr/failed-undo/reproduce.py
uv run pytest tests/tools/file_editor tests/tools/planning_file_editor tests/tools/test_path_rules_file_editor.py -q
```

The identical reproduction script was run before and after the production change.
It calls the public `FileEditorExecutor` with `FileEditorAction`: two successful
edits produce versions 0, 1, 2; a real directory permission change then prevents
the atomic undo write. After restoring directory permissions, it retries undo
and then undoes once more. It must run without root privileges.

| Observation | Base | Candidate |
| --- | --- | --- |
| Failed undo reports an error, current value remains 2 | Yes | Yes |
| Retry restores immediately preceding value | 0 (wrong) | 1 |
| Next undo restores original value 0 | No history (wrong) | Yes |
| Reproduction exit status | 1 | 0 |
| New regression test | 1 failed | 1 passed |
| Related candidate regression suite | Not rerun | 185 passed |

The permanent regression invokes the public `FileEditorTool`, injecting a
`PermissionError` at `Path.replace` so it also works under root-based CI.
Per-file pre-commit checks passed for all three changed source/test files.
The broader suite emitted one existing anyio/pytest deprecation warning.

This is a controlled real filesystem failure, not a reported production incident.
It validates retry after a failed restoration write; it does not establish an
atomic transaction between file contents and cached history across process death
or history-cache storage failures. Same-file serialization remains owned by
`FileEditorTool.declared_resources`; no new locking mechanism is introduced.

Logs replace private checkout/environment paths and strip ANSI styling.
`report.json` records hashes of both raw and public copies. This directory is
temporary reviewer evidence, following repository `.pr/` policy.
