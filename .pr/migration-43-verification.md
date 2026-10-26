# Verification maintenance migration — 2026-10-24

This migration keeps the verification mechanism from #5617 current and records
new product findings under the existing #5653 umbrella. It does not close either
tracker or implement the product fixes.

## Revisions and scope

- Source: [enyst/agent-sdk#43](https://github.com/enyst/agent-sdk/pull/43),
  `abb95804ad49679efdec04669122fdd38efa5253`; source commits `fe5ee276fb7959ed81b2edc4f6742c6f5cb70d5b`
  and `abb95804ad49679efdec04669122fdd38efa5253` are retained with cherry-pick provenance.
- Upstream base: `acd96e6fa424c02432304395b9208f99be24b54d`.
- Live verification: `8acddbd3ac679746a15ac4e96560b9d9535a95d6`. Subsequent changes add regression tests
  and this report; the CLI and map bytes remain the ones exercised live.
- Accepted baseline remains `8ba966d1d3af49f08e04349c055009e1614b48d6`.
- Changes are limited to the verification skill, its CLI regression tests and
  temporary PR evidence. Product code, services already running on the machine,
  and source issues/PRs remain unchanged.

## Upstream reconciliation

The five conflicting map files were reconciled against upstream rather than
restoring stale known-bug markers. This preserves the trajectory init-path fix,
missing profile-pointer validation, `.json` profile identity, Playwright browser
path fix, and the extra upstream settings recipe. The resulting map has 950
entries: the source's 948, the newer upstream settings entry, and a credential-free
recipe for `GET /api/skills/catalog` added by #6098. The catalog recipe uses a local
git-backed public catalog plus user/project fixtures, verifies `public` and `user`
origins, and excludes a project-only skill after a workspace-catalog positive control.

Active bug links now refer to #6184 (skill install subpaths), #6185 (quota errors),
#6183 (TypeScript public skills), and #6186 (child launch on an unborn repository).
Historical reports retain their original source issue URLs and tested revisions.
PR #6032 remains open and covers the independent `map dependents` consumer lookup;
its functionality is neither replaced nor claimed as part of this migration.
All-state upstream PR searches for the verification skill, recipes, doctor and
provider behavior, plus a source-PR provenance search, found no equivalent migration.

## Current migration verification

- `control-agent-server map check`: 34 family files, 950 entries, no problems.
- `control-agent-server map coverage`: 207 routes; 197 owned and driven, 10
  explicit exclusions; no unowned routes, duplicate owners or stale exclusions.
- `uv run --no-sync pytest tests/cross/test_verify_agent_server_skill.py -q`:
  74 passed. Five added cases cover successful/failed health gating with cleanup,
  and structured, empty-object and null HTTP error bodies.
- Pre-commit passes on the CLI, changed maps and regression tests; `git diff --check BASE...HEAD -- .agents tests`
  is clean. The imported historical `.pr/` ledger retains source quotation whitespace.
- `map run --file F01 --only F01.alive --fresh --repeat 2 --record` and
  `map run --file F03 --only F03.dormant-boot --fresh --repeat 2 --record` pass.
  Each fresh server saves a successful doctor report before replay.
- On the same real dormant server, the base CLI doctor fails because it expects
  missing-key 401 and an accepted WebSocket. The migrated CLI passes the actual
  API 503 and WebSocket handshake 403 checks. After initialization, both doctors
  pass, but the base incorrectly reports `dormant: true`; the migrated CLI reports
  `dormant: false`. The first test driver incorrectly expected the base to fail
  after initialization; that expectation was corrected and the comparison rerun.
- The base CLI rejects a `status:429:JSON` fixture step. The migrated fixture
  returns the exact supplied quota-error JSON over HTTP, and retains explicit
  `null` and `{}` bodies instead of replacing them with a generic error.
- `map run --file F25 --only F25.profile-catalog --fresh --record` passes its
  setup, local sync prerequisite and catalog recipe.
- All nine migration-owned server runs were stopped. No model credentials were
  acquired, no real model was called, and no existing service was restarted.

The compact, sanitized results and verified file hashes are in
[migration-43-evidence.json](migration-43-evidence.json).

## Evidence limits

The source's full 948-entry pass, 130-PR intent ledger and follow-up fix report
are historical evidence for their recorded revisions. This migration does not
claim a full 950-entry pass on current upstream. Docker, browser, real-model and
human subscription authorization lanes were not repeated. The source's remaining
human-authentication limitation still prevents advancing the accepted baseline.
Published-head CI must run after publication.
