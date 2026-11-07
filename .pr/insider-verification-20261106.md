# Insider SDK refresh — 6 November 2026

Draft [SDK PR #5180](https://github.com/OpenHands/software-agent-sdk/pull/5180)
was refreshed from current main without rewriting the existing branch. The
saved-Cat Voice contract is unchanged. This record covers SDK, Agent Server,
TypeScript client and credential-free API verification; it makes no new claim
about provider speech or a physical microphone/speaker.

## Source and integration

The starting PR head was `e17f4e09b10db0227660975409f8452c5b8d4fbc`.
The tested product tree merged main
`1b1a3bf098847d40b51911f8b52a2a1f52a5f4bf` (SDK 1.60.1).
The final refresh also includes main
`1355a1d50271aa8f2d041e17eef3fe33405ea690`, whose only subsequent change
is TypeScript README maturity wording. Tests ran in an isolated worktree;
existing shared services and their conversations were left untouched.

- Main extracted the cross-test server fixture into `tests/cross/conftest.py`.
  The merge uses that shared fixture and retains the Insider HTTP regression.
- Main added a locale lifecycle test with a synthetic application state. Its
  fixture now supplies the Voice manager, as the real `create_app` already does.
  The initial run exposed two failures in this fixture; the corrected suite
  result is recorded below.
- Condensation retains main's LLM call-context scope and the PR's direct
  condenser invocation. Compaction cannot advance the agent or run pending tools.
- SDK family F35 is still available. The map total is updated to 966 IDs;
  current Canvas recipe links now use F30, because main assigned F29 to ACP.
  The dated October evidence retains its original identifiers.

## Current design and review entry points

The four additive Voice routes remain conversation-bound. An eligible saved
top-level Insider controller owns the tools, skills, approvals, messages and
answers. OpenAI API Voice delegates through the browser; the optional Codex
broker delegates through `CodexRelay._delegate`. The latter saves the request,
starts the same OpenHands conversation, waits for the run task to settle, and
speaks only its newly saved answer. Ending a call does not cancel accepted work.
Busy follow-ups are reported as not sent. Transcript and write-boundary guards
prevent a pending stale answer from being submitted after newer input; they
cannot revoke audio already queued by the provider.

Review the current [F35 contract and executable API recipes](../.agents/skills/verify-agent-server/references/feature-map/F35-insider-voice.md),
[F10 condensation contract](../.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md),
[Voice router](../openhands-agent-server/openhands/agent_server/voice_router.py),
[Codex relay](../openhands-agent-server/openhands/agent_server/codex_voice.py), and
[local condensation](../openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py).
The earlier [design snapshot](voice-design.html) and
[26 October integrated evidence](insider-verification-20261026.md) remain
historical records, rather than claims that those live checks ran again today.

## Automated checks

| Check | Result |
| --- | --- |
| Voice routes, Codex relay/protocol, condensation and deferred initialization | 124 passed; the initial 179-test run also included 53 passing API tests and the two locale fixture failures described above |
| API lifecycle, EventService and full cross/live-server suite | 227 passed; the existing WebSocket security-risk timing test remains skipped |
| OpenAPI contract and server config | 32 passed |
| LLM call-context isolation and summarizing condenser | 68 passed |
| TypeScript client unit suite with coverage | 407 passed across 28 files |
| TypeScript lint, build and formatting | Passed; nine existing lint warnings, no errors |
| Pinned v1.60.1 generated TypeScript API | Current, no generated diff |
| API generator fixtures and endpoint-audit tooling | Passed |
| Endpoint audit | Passed; zero unallowlisted client-only operations; existing report-only server gaps remain |
| Public weak-type budget | Unchanged: 103 sites, zero `any` |
| Current-server schema export | Two exports identical; type-quality check and OpenAPI validation passed |
| Repository pre-commit hooks on the feature and merge fixes | Passed, including Pyright |
| Feature-map structure | 35 families, 966 IDs; passed |

The feature-map coverage check owns and drives all four Voice operations. It
also reports two routes already present on main without a map owner: POST and
DELETE of `/api/canvas-extensions/installed/{extension_name}/backend/secret-grant`.
They are outside this Voice refresh and remain visible findings.

Reproduce the central checks from the repository root:

```sh
uv run pytest tests/agent_server/test_voice_router.py \
  tests/agent_server/test_codex_voice.py tests/agent_server/test_codex_voice_protocol.py \
  tests/sdk/conversation/test_condense.py tests/agent_server/test_init_router.py -q
uv run pytest tests/agent_server/test_api.py tests/agent_server/test_event_service.py \
  tests/cross/test_remote_conversation_live_server.py -q
uv run pytest tests/agent_server/test_openapi_contract.py tests/agent_server/test_config.py -q
uv run pytest tests/sdk/conversation/local/test_call_context_isolation.py \
  tests/sdk/context/condenser/test_llm_summarizing_condenser.py -q
make test-server-schema
```

The TypeScript checks run from `clients/typescript`: `npm ci`, `npm run lint`,
`npm run build`, `npm run test:coverage`, `npm run format:check`,
`npm run check:agent-server-api`, `npm run test:agent-server-api-tooling`,
`npm run test:endpoint-audit-tooling`, `npm run audit:endpoints`, and
`npm run check:public-type-budget`.

## Fresh API replay

`control-agent-server map run --file F35 --fresh --record` launched a private
real Agent Server with private state and credentials. Its doctor passed before
the recipes. No model was run and no provider credentials were imported.

| Feature IDs | Expected and observed result |
| --- | --- |
| F35.availability | Missing-key state includes provider, delegation owner, run activity and no-store caching |
| F35.auth | All four operations reject absent or incorrect session authentication without appending events |
| F35.controller-only | Ordinary, worker and child conversations are refused; a legacy top-level Cat remains eligible |
| F35.not-found | Unknown conversations/calls and malformed IDs return their documented statuses |
| F35.offer-validation | Empty, oversized, non-string and extra-field offers are rejected without controller changes |
| F35.missing-key | A valid-shaped offer without an API key fails while saved text work remains unchanged |
| F35.call-id-validation | Invalid call IDs are refused before provider access |
| F35.ts-client | Built `VoiceClient` reads availability and preserves real HTTP errors for all operations |
| F35.codex-unavailable | An unsigned dedicated broker reports unavailable and leaves no usable call |

All **nine feature recipes passed**, plus setup: ten successful recipe blocks,
zero failures. Six requirements were explicitly blocked in this API-only replay:
F35.live-call, saved-context, delegation, handoff-integrity, approval-boundary,
and call-ownership-cleanup. Their provider/browser preconditions were not
established by this run. Unit protocol fixtures do not replace those live checks.

The append-only evidence ledger and request/response transcripts are retained
privately under the run label `20261106-192822-a16e-map-f35`. The source was the
uncommitted merge tree described above, so its runtime build label still carried
the starting PR HEAD; it must not be mistaken for a clean checkout of that old
revision. The harness stopped its owned server and verified it was no longer
alive. Public notes omit keys, local service addresses and conversation IDs.

## Limits

The optional Codex prototype still requires version 0.154.0 and a separate
signed-in home. This refresh does not broaden that compatibility pin or claim
that an arbitrary current Codex binary supplies the same protocol. Live OpenAI
API Voice, live Codex Voice, physical iPad audio, backend-switch cleanup and
complete keyboard navigation require separate current integrated evidence.
