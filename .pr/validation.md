# Validation of server-owned Codex authentication

Implementation head: `fb9f996a1c8dc6d5cd6d9bcf71e36a96a0fb7743`.
Lint cleanup: `2f65706cc5efa069f7c7a3b7574f06dae0d98f1e`.
Canvas counterpart: https://github.com/OpenHands/OpenHands/pull/17851.
Documentation: https://github.com/OpenHands/docs/pull/866.

## Automated and packaging checks

Windows native environment: Python 3.13.5, Node 24.11.1. Dependencies were
installed into isolated task directories without changing the original checkout
or its Python environment.

```sh
uv run pytest tests/agent_server/test_codex_auth.py \
  tests/agent_server/test_llm_router.py \
  tests/agent_server/test_credential_binding.py \
  tests/sdk/agent/test_acp_file_credentials.py \
  tests/cross/test_remote_conversation_live_server.py::test_codex_device_login_over_authenticated_http -q
```

Observed: **81 passed**. The new HTTP test uses the full real FastAPI application
and session authentication. Only the OpenAI transport is mocked. Conversation
creation uses the real service, versioned binding and SDK file lifecycle to
materialize private Codex auth.json without an API key, mask credentials and
clean up after close. Other tests cover encrypted restart persistence, host
login import, refresh rotation, stale refresh success/failure, invalid ID tokens,
device expiry, cancellation, logout and sanitized failures.

Changed Python files passed Ruff. Focused Pyright passed with zero errors.
Agent Server wheel built successfully; it includes the new router/module.

From `clients/typescript`:

- `npm run build`: passed.
- `npm run check:public-type-budget`: unchanged, 0 any / 103 unknown sites.
- Changed-file ESLint and Prettier: passed.
- Vitest: **348 passed**, split into 334 tests and 14 fixed-tempdir tests. The
  initial Windows run failed on the unchanged `/tmp/test-utils-test` sandbox
  restriction and an import timeout. Rerunning with the specific temporary
  directory permitted and a 30-second import/test timeout resolved both.
- Full Windows lint now passes with 11 existing warnings after removing an
  unused import and variable from two integration tests. The two deletions
  preserve behavior; client build and changed-file formatting pass.
- The pre-commit runner is unavailable in this Windows environment. Direct
  ESLint/Prettier checks and the SDK dynamic-attribute baseline check passed;
  the latter received POSIX file paths because Windows auto-discovery uses
  backslashes while its committed baseline uses forward slashes.

## Live production-facing run

Full native Agent Server + built Canvas, MSW disabled: an actual OpenAI device
challenge returned HTTP 200; the UI displayed/copied the code and cancelled the
attempt. No actual account authorization or model call occurred.
Repeated after switching the running server to the final built wheel:
real device initiation, copy/cancel and manual fallback passed without page
errors. Backend/cross tests were rerun: **81 passed**.
See the [Canvas evidence](https://github.com/luxleader/OpenHands/blob/feat/codex-oauth-17372/.pr/validation.md)
for the runtime command, screenshots and video, including its precise scope.

## Authorized account verification — 2026-10-02

The human completed ChatGPT device authorization in the real Canvas. The
packaged Agent Server returned `connected=true`. Three isolated conversations
using the active Codex Agent Profile and pinned `codex-acp@1.10.0` each reached
`finished` and returned `OK` through the canonical `agent_final_response` API:
before restart, after restarting the server with its original encrypted store
and encryption key, and after refreshing credentials. No API key was supplied.

The actual OpenAI refresh endpoint was exercised through `CodexAuthService`.
Only the expiry predicate was forced to trigger refresh immediately; the OAuth
transport, account credentials, CAS update and encrypted store were real.
The access token changed, the refresh token rotated, and the stored version and
last-refresh timestamp changed. The running server still reported connected.
This does not establish naturally expired-token behavior or prolonged use.

The default bare `npx` command originally failed with `[WinError 2]` on
Windows. Commit `5ab913acbc8d938f21a18e981dd9aa3224eaf2ca` resolves the installed
npm shim through Node in both cache warming and ACP startup, without shell
parsing or a persisted profile change. Two offline native process tests failed
before the fix and passed after it, including literal spaces and `&` arguments.
The SDK wheel SHA256 is
`8f36731135c84c180e12b225d5b3a61524f0572688b4869e1fc6c87fcec4e736`;
its ACP module matches the source byte-for-byte. Ruff, focused Pyright and
import rules pass; `uv`/pre-commit is unavailable on the Windows host.

With that packaged SDK and Agent Server, the default profile command was
restored to `null` (built-in Codex preset). A new conversation returned `OK`;
the originally failed conversation was resumed and also returned `OK`.
Five real model turns have now passed. Reloading the actual Canvas showed
the built-in Codex command and **Connected to ChatGPT** with blank API fields.
The human's connection remains available; real account disconnect and natural
expiry/prolonged-use checks are still unverified.

The broader Windows ACP run had 526 passes and five Unix permission-bit
assertion failures. The exact same five failures were reproduced at the
pre-fix commit (27 other tests passed in that baseline subset). On hosted
Linux, the combined backend/ACP selection passed **594 tests**, with only the
two native Windows tests skipped; the TypeScript client passed **348 tests**.
See the [isolated runner](https://github.com/luxleader/software-agent-sdk/actions/runs/37023860358).
All test, package, canonical Docker build and container acceptance steps passed.

Credential-free observations: [live results](codex-live-results.json).


## Docker / hosted Linux acceptance and candidate publication

The [successful cloud-hosted Linux run](https://github.com/luxleader/software-agent-sdk/actions/runs/37023860358) built the canonical Agent Server
`source-minimal` Docker target through the SDK's sdist-based builder, with the
pinned Codex ACP provider. The image ran as its normal non-root user, exposed
only a host-loopback port and used a fresh named volume and test session key.
Real HTTP requests verified session authentication, device pending/success,
credential-free responses, encrypted storage, restart persistence, refresh,
logout deletion and cancellation of an in-flight login. These lifecycle checks
replace only OpenAI's OAuth transport with synthetic tokens. The restarted
unmocked image also initiated and cancelled an actual OpenAI device challenge;
its code and handle were never included in public output. No human account
credentials were sent to the runner and no remote model request was made.
See [credential-free container results](codex-remote-results.json).

Four Python wheels/sdists, the TypeScript tarball, source manifest, checksums
and the acceptance report are published as a [fork candidate prerelease](https://github.com/luxleader/software-agent-sdk/releases/tag/codex-oauth-17372-candidate-5ab913ac).
The changed SDK/server modules match production commit `5ab913ac` (line endings
normalized). Package metadata remains the development version `1.50.1`; this
does not mean that PyPI/npm's `1.50.1` contains this feature. Candidate publication
does not replace the official OpenHands release or downstream exact pins.

## Remaining gates

Keep draft pending maintainer CI approval, official compatible server/client
publication and downstream exact pins. Remote human account authorization and
model turns, real account disconnect and natural expiry are not established. The separate
Cloud App API integration is not implemented by this public Agent Server PR.
The Canvas author explicitly requested an AI-assisted HUMAN summary; it
discloses that assistance and its latest description check passed. Remove `.pr/` manually
before merging this fork PR.
