# Workspace registry lock responsiveness

Fixes #5974. Tested on main at `6e7a8532ef4426da08b5c06dd57cf4795303be01`
with the proposed working-tree changes, in the existing `sdk-audit` Linux Docker
container. Python 3.13.16; SDK and Agent Server 1.54.0. No model calls.

The production change makes the four workspace mutation handlers synchronous
FastAPI routes so their file-lock waits run in worker threads. Locking, the
read-modify-save transaction, error mappings, and HTTP models are unchanged.

## Results

- The four new default-suite regression cases fail on original handlers and
  pass after the fix. The three related API test modules pass: **43 tests**.
- The maintained `F17.lock-responsive` check fails twice on original handlers
  and passes twice after the fix. Each run covers all four mutations plus both
  missing-item deletes. A separate process acknowledges lock acquisition, a
  nonblocking acquisition confirms exclusion, and `/proc/locks` confirms the
  server waiter on the exact inode before probes are sent.
- All **36 final probes** to `/alive`, `/api/workspaces`, and `/server_info`
  return HTTP 200 within two seconds while the mutation remains pending.
  The slowest response is **2.52 ms** using an already-open HTTP client.
- All twelve final cases preserve registry bytes until release, then return
  the expected 200 or 404 and persist exactly the expected lists. Every holder
  exits successfully and cleanup reacquires the lock.
- All **22 selected F17 recipes** pass, plus Preconditions. These include
  concurrent updates on one server, two servers sharing the registry,
  TypeScript client calls, auth, validation, corrupted/future-schema responses,
  I/O errors, persistence, and the new responsiveness check.
- Complete generated OpenAPI documents before and after the fix are identical.
- Doctor passes before and after both final live runs. All six recorded
  baseline/final/integrity servers, including the second shared-directory
  server, are stopped and their ports are closed.
- Per-file pre-commit checks pass, including Ruff, pycodestyle, Pyright, and
  repository gates. Feature-map validation passes for 34 files and 936 IDs.

## Repeat

```sh
uv run pytest -q tests/agent_server/test_workspaces_router.py \
  tests/agent_server/test_workspace_router.py \
  tests/agent_server/test_workspace_cookie_auth.py
.venv/bin/python .agents/skills/verify-agent-server/scripts/control_agent_server.py \
  map run --file F17 --only F17.lock-responsive --fresh --repeat 2 --record
```

The F17 prerequisites require Node/npm for the TypeScript client. Missing npm
initially blocked that setup; checksum-verified Node 22/npm were installed only
in the audit container before the successful integrity run. No repository
dependency or lockfile changed. The three existing no-op-write recipes are
outside this issue's scope and were not selected. Windows locking and lock
timeouts remain unchanged and outside the agreed scope.

[Sanitized raw evidence](workspace-lock-investigation/acceptance-evidence.tar.gz)
contains baseline/final measurements, kernel-wait confirmations, doctor results,
HTTP transcripts, the JUnit report, OpenAPI documents, map validation, and quality
checks. Its combined ledger includes two expected baseline failures. Private
keys, HOME, server/worktree/fixture state, and shell environment snapshots are
excluded. This `.pr/` evidence is temporary reviewer context.
