# Completion validation — 2026-10-24

Tested on Windows, Python 3.13.14, Node 24.19.0; uv's frozen development
environment and npm's lockfile. No dependency, lockfile or package version changes.
Original PR parent: `56c12b0e08a219c7a8e8596b7924f6d764df46c7`.

## Implemented behavior

- Canonical bounded direct-child wait, typed mailbox result delivery before
  cleanup, generation guards, caller cancellation and owned-task drainage.
- Registered SDK wait and mailbox tools using canonical REST. Both sync and async
  agent execution release the parent state lock for isolated coordination I/O.
- Persisted, agent-attributed mailbox events, typed validation, same-tree scope,
  stable idempotency keys, restart recovery and sender/recipient secret redaction.
  Default steer preserves active work and goals; explicit interrupt records its
  sender, mode and reason and persists interrupted goal status.
- TypeScript methods, typed requests/responses/events, local OpenAPI generation,
  maintained feature-map ownership, and companion documentation in docs PR #992.

## Python regressions

Final related suite: **596 passed, 4 skipped, 4 deselected, 7 warnings in 227.13s (0:03:47)**.

```sh
python -m pytest tests/agent_server/test_conversation_service.py \
  tests/agent_server/test_event_service.py tests/agent_server/test_conversation_lease.py \
  tests/agent_server/test_goal_loop.py tests/agent_server/test_parent_child_conversations.py \
  tests/agent_server/test_run_admission.py tests/agent_server/test_conversation_router.py \
  tests/agent_server/test_openapi_contract.py tests/agent_server/test_api_authentication.py \
  tests/agent_server/child_wait tests/tools/child_conversation \
  tests/sdk/agent/test_parallel_executor.py \
  tests/sdk/agent/test_parallel_execution_integration.py \
  tests/sdk/agent/test_astep_releases_state_lock.py \
  tests/sdk/agent/test_parallel_executor_locking.py \
  tests/cross/test_remote_conversation_live_server.py \
  -k 'not test_restart_resumes_conversations_after_non_graceful_shutdown and not test_claim_takes_over_when_previous_owner_pid_is_dead and not test_deferred_websocket_initialization_lifecycle and not test_openai_chat_completions_gateway_over_real_server'
```

Windows environment: `PYTHONUTF8=1`, `NO_PROXY=*`, Git `core.longpaths=true`,
and a short absolute pytest basetemp under the authorized workspace. The first
broad run exposed fixture omissions in old dispatch-only mocks; those mocks now
isolate the added result-snapshot helpers as they already isolate state reads.
The event-service + wait/mailbox + coordination-tool subset also passed **167 tests**.

Four tests are explicitly excluded above. All four also fail on the original PR
commit in a separate worktree, with verified imports from that worktree:

- Orphaned conversation restart and dead-PID lease takeover: Windows dead-PID
  detection does not reclaim the synthetic orphan used by these existing tests.
- Deferred WebSocket lifecycle: the existing shell command returns exit 1 on this host.
- OpenAI-compatible gateway smoke: existing local environment/provider setup fails
  before the coordination feature is exercised.

No claim is made that the unfiltered suite is green. The final filtered run and
baseline failures are recorded in the delivery evidence. Four additional existing
platform-dependent tests skip normally.

The added real-socket tests exercise an actual Agent and registered wait tool,
parent state reads and mailbox writes while waiting, child progress, interruption,
HTTP client disposal and runtime-pin drainage, with no WebSocket attached. The
sync Agent regression separately proves lock release using the real agent loop.
Scripted TestLLM responses make these deterministic; they are not live-model evals.

## Other checks

- TypeScript unit suite: **396 passed, 28 files**. Build passed; changed-file
  ESLint and Prettier checks passed; public weak-type budget unchanged at 103.
  Full-repository lint has three pre-existing unused-variable errors in unchanged
  integration tests; whole-tree formatting flags existing checkout files. Those
  unrelated files were not rewritten.
- Local upcoming OpenAPI generated through the documented TypeScript generator.
  The checked-in generated schema and published v1.58.1 image pin stay unchanged.
- OpenAPI quality passed: 63 existing allowlisted weak locations.
- Official oasdiff 1.19.1, SHA-256 checked against its release checksums, compared
  the normalized public schema against the official v1.58.1 release artifact.
  Six new tool/action/observation union additions are allowed by the repository's
  compatibility classifier; zero other changes and zero removed operations or
  properties. Raw oasdiff exits 1 for those additive unions; they are not hidden.
- Applicable pre-commit hooks passed: Ruff format/lint, pycodestyle, Pyright,
  forbidden dynamic attributes, import boundaries, tool registration. YAML skipped.
- Feature-map check passed: 35 families, 938 IDs. Both added routes have owners
  and executable recipes. Coverage still reports three unrelated pre-existing
  unowned routes: child launch, skills catalog and plugin fetch.
- `git diff --check` passed.

## Assembled-server verification

The repository's Unix-process-group `launch` fails on Windows (`os.getpgid`),
so an owned Windows server used the supported, unmodified `attach` path instead.
The original `doctor` passed before and after the probes. The original control
CLI drove parent/child creation, normal timeout, mailbox send/retry/conflict,
missing-evidence rejection, non-child rejection, timeout deletion and subsequent
404. All probes passed; the attached run and owned server were stopped.
This is live credential-free REST proof, not a substituted mock. F35 records the
same API recipe; its shell runner itself was not executed on this Windows host.

No DeepSeek key was available. Live-model behavior, full stress runs and
Docker/PyInstaller validation remain unrun and must not be reported as passes.
GitHub CI needs the upstream maintainer's fork-workflow approval. The tool change
also needs the upstream `integration-test` label, which this account cannot set.
The HUMAN PR section remains reserved for the human author.

## Scope and review

The wait now has the missing mailbox delivery and lock-releasing SDK integration.
The mailbox is a distinct canonical operation and independent tool, with its own
tests; it is included here as the #6127 dependency that is absent on main. This
does not implement Canvas UI, queue delivery, a scheduler or a second child registry.
Maintainers can split the mailbox dependency into a separate PR if preferred.
The implementing agent performed its own review; no independent human review is claimed.
