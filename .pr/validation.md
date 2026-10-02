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

## Remaining gates

Keep draft pending a human ChatGPT login and Codex turn, Docker/remote checks,
compatible server/client publication and downstream exact pins. The separate
Cloud App API integration is not implemented by this public Agent Server PR.
The HUMAN note remains reserved for the human author. Remove `.pr/` manually
before merging this fork PR.
