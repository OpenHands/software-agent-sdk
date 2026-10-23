# Validation and maintainer-style self-review

Date: 2026-10-23. Base: `60192ed54db73ac658ac1a07462916126c32d3a0` (main, v1.58.1).
Branch: `feat/6129-server-side-child-wait`.

## Scope and readiness

This is a **partial backend foundation**, not a completed fix for #6129. Use `Refs #6129`.
It provides a server-owned lifecycle wait, canonical REST operation, and typed TypeScript access.
It intentionally does not return/inject finish text through a second delivery channel.
Mailbox #6127 is open, has no comments or assignee, and no implementing mailbox PR was found.
A lock-releasing SDK agent tool remains integration work: normal native tools run under the
parent's state lock, so copying the synchronous child-launch executor would violate this issue.
The proposed PR must remain **draft** until the human template section is supplied, the
outstanding integration/feature-map decisions are reviewed, and upstream CI runs.
No PR or external human review was completed in this environment.

## Preflight

- Cloned upstream; fetched latest main before edits and again after validation. SHA unchanged.
- Read root AGENTS.md, CONTRIBUTING.md, DEVELOPMENT.md, custom review guide, Agent Server,
  Tools, SDK and TypeScript instructions, plus verify-agent-server guidance.
- Read #6129 fully, including AI triage, all acceptance criteria, labels and comments (none).
  Open, no assignee. Checked #6127, #4829, #5634, #4781 and #5126, including comments.
- Searched all-state PRs for 6129, child/wait, mailbox/6127 and server-side wait titles.
  No equivalent active wait PR found. #5126 supplies the launcher and existing relationship.
- Confirmed upstream has run-completion waiting internally, but no parent-scoped child wait
  REST operation. Reused EventService lifecycle machinery and catalog parent links.

## Final verified checks

### Python: 475 passed, 5 warnings, 70.61 seconds

```bash
uv run pytest tests/agent_server/child_wait \
  tests/agent_server/test_event_service.py \
  tests/agent_server/test_parent_child_conversations.py \
  tests/agent_server/test_run_admission.py \
  tests/agent_server/test_conversation_service.py \
  tests/agent_server/test_conversation_router.py \
  tests/agent_server/test_openapi_contract.py \
  tests/agent_server/test_api_authentication.py -q --disable-warnings
```

24 new deterministic cases cover completion before/after subscription, idle children starting
later, multiple children and parent progress, normal timeout snapshots, finish/timeout cleanup,
ERROR/STUCK/PAUSED, parent cancel/interrupt/delete, ID rejection and unrelated children,
provisional FINISHED, no leftover tasks, idle-eviction pins, a state lock held by another thread,
completed and pending children mixed at timeout, restart races for all cleanup policies,
cleanup cancellation, and external child deletion. Synchronization uses explicit events.

### Real HTTP: 1 passed, 34 deselected, 4.13 seconds

```bash
uv run pytest tests/cross/test_remote_conversation_live_server.py \
  -k child_wait_real_http_and_disconnect -q
```

Real uvicorn listener and sockets; normal service and LocalConversation lifecycles; an
explicitly controlled Agent.astep replaces model work. Verifies session-key rejection,
HTTP disconnect draining wait scopes, /ready and parent run responsiveness, and the exact
completion response without opening a WebSocket. This is deterministic integration evidence,
not a paid/live-model evaluation and not a packaged/Docker smoke test.

### TypeScript: 395 passed across 28 files

```bash
cd clients/typescript
npm ci
npm run lint
npm run build
npm test
npm run format:check
npm run check:agent-server-api
```

Lint: zero errors, five warnings in pre-existing files. Build, all tests, formatting and pinned
contract drift check passed. The new test verifies one authenticated request and cancellation
signal forwarding. Earlier focused TS run: 94 passed across child-wait and API-client tests.

### Repository hooks

`uv run pre-commit run --files <all modified Python/TypeScript files plus new tests>` passed.
Applicable hooks: Ruff format/lint, pycodestyle, Pyright, forbidden dynamic attributes, import
rules and tool registration. YAML hook skipped because no YAML changed. `git diff --check` passed.
The final source tree is the one used for the 475-test run and real HTTP test.

### OpenAPI and compatibility

```bash
OPENHANDS_SUPPRESS_BANNER=1 uv run python \
  .github/scripts/export_agent_server_openapi.py --output .pr/openapi.json
uv run python .github/scripts/check_agent_server_openapi_quality.py \
  --schema .pr/openapi.json
PATH=/tmp/tmp.NUwyWOPiO4:$PATH uv run python \
  .github/scripts/check_agent_server_rest_api_breakage.py
```

Quality: passed, 63 existing allowlisted weak locations. The compatibility run used the real
`oasdiff 1.19.1` executable and reported `No breaking changes detected`. An earlier invocation
without oasdiff warned that the binary was missing; that earlier result is not counted as proof.
The downloaded installer failed while restoring tar ownership in this container; the verified
extracted executable was then used directly for the successful check.

The documented TS generator was also run against the local upcoming schema:

```bash
AGENT_SERVER_OPENAPI_PATH=../../.pr/openapi.json \
AGENT_SERVER_GENERATED_OUTPUT=../../.pr/upcoming-agent-server-schema.ts \
AGENT_SERVER_SCHEMA_SOURCE=local-6129 npm run generate:agent-server-api
```

Generation succeeded. The temporary schema and generated output were moved into the delivery
package's `evidence/` directory after verification, rather than adding 28k generated lines to
this PR. The normal checked-in generated contract stays at the documented v1.58.1 release pin;
its ordinary regeneration/drift check passed unchanged. The additive client uses handwritten
typed models until the release pin advances. Generated aliases remain a release follow-up.

## Self-review findings corrected

This was a separate maintainer-style review pass by the implementing agent, **not** an
independent human or second-agent review.

1. Per-field FINISHED is provisional before stop hooks. Wait on a per-run future captured after
   run completion, not the first terminal event. Added controlled provisional-FINISHED coverage.
2. Old waits must not delete or interrupt a successor run. Capture run generation; check it at
   cleanup, fence conditional deletion, serialize a conditional interrupt's dispatch with run
   admission. Added restart races for finish-delete, timeout-stop and timeout-delete.
3. Idle eviction could close a valid waiter. Pin both parent and child runtimes with scoped
   counters; release them on completion, timeout, cancellation and disconnect.
4. Cancellation during destructive cleanup could leave a fenced runtime. Shield and drain the
   owned cleanup task; apply no cleanup when the pending wait itself is interrupted.
5. An SDK synchronous interrupt can acquire a state lock. Dispatch interrupt to a worker so
   this path cannot block the shared server event loop.
6. Broad ValueError-to-404 mapping could hide internal failures. Added a specific
   ChildConversationNotFound error and catch only that at the new route.
7. The first provisional-status test observed the state before its controlled step advanced.
   Replaced that test timing with an explicit synchronization event (initial run: 16 passed,
   1 failed; corrected and expanded suite: 24 passed within the final 475-test run).
8. Pyright caught a nullable URL port in the live test. Added a type-narrowing assertion and
   reran the complete changed-file hooks successfully.

## Remaining limitations and checks not run

- No mailbox delivery (#6127), finish-result payload, SDK agent tool or Canvas implementation.
  Canvas belongs downstream. No second delivery system or spawning registry was introduced.
- The timeout bounds waiting after validation/runtime loading. Existing stop/delete cleanup is
  drained outside that deadline. Legacy sync workers remain cooperatively interruptible; this
  change does not claim forced termination of arbitrary Python worker threads.
- Same server instance only. No cross-instance/container orchestration or durable wait recovery.
- TypeScript's pinned generated aliases must follow the release; handwritten models and the
  local generation were validated, but this is not a pin/version bump PR.
- Full stress suite, live-model behavior/benchmark tests, Windows, Docker/PyInstaller runtime
  checks and GitHub CI were not run. They are not claimed to pass.
- `control-agent-server launch --new --name child-wait-6129 --print-run` failed its ownership/
  readiness check after 180s despite server log GET /ready responses being 200. `doctor` then
  saw connection refused; `stop` hit `psutil.NoSuchProcess: process PID not found (pid=2)`.
  This environment's process-namespace inspection blocks the separate CLI map audit. No live
  map recipe was marked passed. The real-socket pytest integration is separately verified.
- The new route still needs its maintained feature-map recipe proven before readiness; a
  proposed recipe is in `.pr/FEATURE_MAP_DRAFT.md`, not silently marked as verified.
- Companion OpenHands/docs guide and nav change are committed locally; JSON parse, target
  existence, source inspection and `git diff --check` passed. Mintlify preview/link checks
  and a docs PR were not run/created.

## Submission blocker

Authenticated connector identity: `karurukaruru`. Upstream permissions are read-only. No existing
software-agent-sdk fork was found on that account. The connector exposes no fork-create tool.
Terminal Git has no credential/helper/token, and push reports:
`fatal: could not read Username for 'https://github.com': No such device or address`.
The fallback browser visibly shows Sign in and a sign-in-required Fork control.
No authenticated submission route is available. No PR URL is claimed.
