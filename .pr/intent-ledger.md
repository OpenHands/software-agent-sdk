# Full Agent Server maintenance: intent ledger

Recorded 2026-10-23 UTC. Fork target `enyst/agent-sdk` at `5966c781ed014efc96c028a99bddd4ff78426741`; accepted maintenance baseline `8ba966d1d3af49f08e04349c055009e1614b48d6` (2026-10-07). The clone is not shallow. All 130 commits in the range were resolved through the upstream GitHub commit-to-pulls API; all resolved to an associated PR. Commit subject regexes were not used as the source of that association.

Intent and runtime are independent. The documented column records what each merged PR says it intends; it does not assert that implementation works. Runtime is reconciled separately in [the per-PR overlay](intent-runtime-overlay.md); its snapshot records completed family sweeps, specific named-feature results, capability limits and remaining not-run rows without treating a broad family pass as proof of an entire PR. Changed source prefixes are expanded for shared settings, events, LLM configuration, launch resolution, and terminal consumers. Broad family ownership is deliberate; the full live pass is required.

Fetched evidence: all associated PR descriptions, complete changed-file lists, review bodies, inline review comments, issue discussion, and linked-issue bodies/acceptance criteria. Raw public API records are in `intent-api/`; joined datasets are `intent-commits.json`, `intent-prs.json`, and `intent-linked-issues.json`. The ledger quotes the relevant stated intent and records relevant review decisions below. No PR descriptions were changed.

REST compatibility reports: 112 of these PRs have a recorded `agent-server-rest-api-breakage-report` comment; all 112 say PASSED. The remaining 18 have no such comment in the returned discussion; absence is not a compatibility pass. Passing release-baseline checks also does not prove current runtime behavior.

## Deliberate changes that require map reconciliation

- #5987 adds strict plugin fetch preflight and `POST /api/plugins/fetch`; source fetching must not silently use stale cached revisions.
- #5126 adds the server-owned `start_child_conversation` tool and a pluggable launcher, a new behavior under tool/conversation families.
- #5983 deliberately removes the unreleased inline `agent_profile` launch introduced earlier in #5409. A stored profile is made active when possible; a corrupt default alone remains untouched with no active profile and gateway 409. Its requested-changes review was resolved before merge.
- #5981 adds profile public-skill selection and critic-key fallback; #4960 adds launch-time additional skills. Shared launch paths and consumers need coverage.
- #5182 adds durable `SecurityAnalysisEvent` records. Event ordering, persistence and both clients are consumers.
- #5341 exposes `requested_ref` on installed Canvas apps, preserving pinned install refs for update clients.
- Many prior audit defects were fixed in this interval. Existing `known bug` markers require the skill's two baseline xfail / two target xpass proof before removal; a closed issue or merged PR alone is insufficient.

## Commit / PR summary

| Commit / PR | Changed paths | Families / entry points | Intended result: quoted PR evidence | Intent | Runtime evidence / limits |
|---|---|---|---|---|---|
| `608a102c637d` / [#5467](https://github.com/OpenHands/software-agent-sdk/pull/5467) | `openhands-sdk/openhands/sdk/llm/utils/model_features.py`<br>`tests/sdk/llm/test_llm.py`<br>`tests/sdk/llm/test_model_features.py`<br>`tests/sdk/llm/test_vision_support.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - Narrow the automatic string-serializer entry from the bare `"deepseek"` substring to the legacy `"deepseek-v3.2"` family, so `DeepSeek-V3.2-Exp` / `deepseek-v3.2-reasoner` keep string-only content while the v4, v4.1, `v4-flash-vision-exp`, and `deepseek-chat` ids keep structured (list) content. - Add feature-registry regression coverage asserting the v4/v4.1 ids resolve `force_string_serializer is False` and the v3 | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5467) |
| `91ac058a08fd` / [#5548](https://github.com/OpenHands/software-agent-sdk/pull/5548) | `.github/workflows/storage-scenario.yml`<br>`pyproject.toml`<br>`tests/agent_server/docker_runtime/STORAGE_SCENARIO.md`<br>`tests/agent_server/docker_runtime/test_storage_scenario.py` | F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34 | - `tests/agent_server/docker_runtime/test_storage_scenario.py`: five tests against a real Docker daemon and agent-server image. The server is configured through `OH_*` environment variables, as the CLI does, and drives real containers. Each test checks what must survive as well as what must go: 1. **Only rebuildable files go.** Stopping a conversation drops `~/.cache` and `~/.npm` only; a pass over the disk budget th | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-5548) |
| `69e26889401f` / [#5538](https://github.com/OpenHands/software-agent-sdk/pull/5538) | `openhands-agent-server/openhands/agent_server/_secret_redaction.py`<br>`openhands-agent-server/openhands/agent_server/event_service.py`<br>`openhands-agent-server/openhands/agent_server/file_router.py`<br>`openhands-sdk/openhands/sdk/utils/files.py`<br>+3 paths (complete list below) | F05, F06, F07, F08, F09, F10, F11, F12, F14, F18, F20, F21, F22, F31 | - `_create_zip_from_directory` skips any entry (file or directory) that raises `FileNotFoundError` between listing and reading, and skips every `*.tmp` file before reading it; the file each one replaces is archived as usual. `atomic_write_text` now names its temp file `.<name>.<random>.tmp` (`tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp")`), and the conversation lease already writes `owner_lease.tmp` before | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5538) |
| `0bb7b525c75f` / [#5547](https://github.com/OpenHands/software-agent-sdk/pull/5547) | `openhands-agent-server/openhands/agent_server/conversation_service.py`<br>`openhands-agent-server/openhands/agent_server/local_storage.py`<br>`tests/agent_server/test_local_storage.py` | F04, F05, F10, F12, F15, F31, F32 | - `delete_conversation` now calls `remove_conversation_worktree(conversation_worktree_root, id)` after removing the conversation directory. - It removes `conversation_worktree_root/<id>/` and runs `git worktree prune` in each source repository. The `openhands/<id>` branch, with the agent's commits, is kept. - git runs with `core.fsmonitor=false` and `--no-optional-locks`, because the repository config is agent-writab | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5547) |
| `97190b800444` / [#5342](https://github.com/OpenHands/software-agent-sdk/pull/5342) | `openhands-tools/openhands/tools/terminal/terminal/terminal_session.py`<br>`openhands-tools/openhands/tools/terminal/utils/command.py`<br>`tests/tools/terminal/test_terminal_parsing.py`<br>`tests/tools/terminal/test_terminal_session.py` | F13, F27, F33 | - Preserve a top-level script boundary after a completed structural `heredoc_end` using the existing shared tree-sitter-bash AST. - Keep ordinary newline-separated commands and here-strings split/rejected; do not alter malformed-input fallback, action security analysis, confirmation policy, or the command sent for execution. - Add table-driven grammar regressions and real tmux/subprocess terminal-session coverage for | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5342) |
| `9f47d471ee6f` / [#5460](https://github.com/OpenHands/software-agent-sdk/pull/5460) | `openhands-sdk/openhands/sdk/llm/message.py`<br>`tests/sdk/llm/test_message.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - Keep the list format for messages that actually carry an image when vision is active, even if string serialization is forced. - Text-only messages still use the string form that forced providers require, so the reason those entries exist is preserved. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5460) |
| `5c6e2589656a` / [#5582](https://github.com/OpenHands/software-agent-sdk/pull/5582) | `.github/workflows/server.yml`<br>`openhands-agent-server/openhands/agent_server/docker/Dockerfile`<br>`pyproject.toml`<br>`tests/cross/test_agent_server_build_metadata.py`<br>+1 paths (complete list below) | F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34 | - advance the observed Debian snapshot from 2026-09-20 to 2026-10-04 - require and lock urllib3 2.8.0 for the available security fixes - remove OpenVSCode from the python-slim image while retaining browser and Docker capabilities - explicitly report VS Code disabled at runtime in python-slim | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-5582) |
| `c980344bc78e` / [#5341](https://github.com/OpenHands/software-agent-sdk/pull/5341) | `openhands-agent-server/openhands/agent_server/canvas_extensions_router.py`<br>`tests/agent_server/test_canvas_extensions_router.py` | F28 | - Add optional `requested_ref` to `InstalledCanvasExtensionResponse` and populate it in `from_info`, so install / list / get all return it. - Router test installing `github:org/repo` at `ref="v1"` (fetch patched) asserts `requested_ref` on the install and get responses. ### REST API contract changes Compared with base OpenAPI `d77ada7a030b` for public `/api/**` paths. ```diff --- base public OpenAPI +++ head public O | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5341) |
| `16662b0bd2c1` / [#4784](https://github.com/OpenHands/software-agent-sdk/pull/4784) | `openhands-tools/openhands/tools/file_editor/utils/encoding.py`<br>`tests/tools/file_editor/utils/test_encoding.py` | F14, F27, F33 | - Return `utf-8` immediately when the sampled bytes decode strictly as UTF-8. - Preserve `utf-8-sig` handling for files with a UTF-8 BOM. - Keep `charset-normalizer` as the fallback for non-UTF-8 content. - In `_recover_encoding`, try UTF-8 before charset-normalizer's guess, so `get_encoding` (the path the editor uses) cannot switch a valid UTF-8 file back to cp1251, cp1252 or latin-1. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-4784) |
| `2804c1273b40` / [#5559](https://github.com/OpenHands/software-agent-sdk/pull/5559) | `openhands-agent-server/openhands/agent_server/api.py`<br>`tests/agent_server/test_api.py` | F01, F03, F05, F30, F31, F32 | - The agent-server checks whether `$TMUX_TMPDIR/tmux-<uid>/openhands-<32 hex>` fits the platform's socket path limit. If the configured or default directory is too deep, it uses `/tmp/openhands-agent-server-<pid>` instead. - It logs a warning when it overrides an explicit `TMUX_TMPDIR`, and restores the original value on shutdown. - A short, explicit `TMUX_TMPDIR` is honored exactly as before. Socket naming and the i | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5559) |
| `9c4fd8c6664d` / [#4952](https://github.com/OpenHands/software-agent-sdk/pull/4952) | `openhands-agent-server/openhands/agent_server/profiles_router.py`<br>`tests/agent_server/test_profiles_router.py`<br>`tests/cross/test_remote_conversation_live_server.py` | F20 | - Resolve linked providers for `GET /api/profiles/{name}` with `X-Expose-Secrets: plaintext`. - Preserve stored profiles, active defaults, and the existing unresolved behavior of ordinary and encrypted editor reads. - Cover encrypted provider storage and credential rotation, plus real HTTP selection through `RemoteWorkspace.get_llm(profile_name=...)`. ### REST API contract changes Compared with base OpenAPI `b66c7243 | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-4952) |
| `d1bfda28da7f` / [#5619](https://github.com/OpenHands/software-agent-sdk/pull/5619) | `tests/agent_server/test_api.py` | no product runtime required | * Separate default-path selection tests from directory-creation tests, avoiding randomized fixture paths in assertions about the production default. * Use a genuinely short final directory for the real TmuxPanePool integration test and the lifespan test. * Add deterministic macOS/Linux socket-path byte-length tests using simulated canonical paths, without globally monkeypatching process identity. No production code c | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5619) |
| `330441825052` / [#5616](https://github.com/OpenHands/software-agent-sdk/pull/5616) | `.github/scripts/update_npm_pin.py`<br>`.github/workflows/update-npm-pin.yml`<br>`openhands-agent-server/openhands/agent_server/docker/Dockerfile`<br>`tests/cross/test_agent_server_build_metadata.py`<br>+1 paths (complete list below) | F01, F12, F16, F31 | - Upgrade the shared npm pin from 11.19.1 to 11.21.0 for both canonical and ACP Node runtimes. - Add a daily/manual updater that selects stable, non-deprecated, same-major releases at least seven days old; never downgrades or edits ambiguous pins. - Open draft update PRs using the existing bot PAT so ordinary PR CI runs. No automatic merge, no force-push, no rewriting human review; one pending npm update at a time, a | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5616) |
| `cd17bd89d52e` / [#5673](https://github.com/OpenHands/software-agent-sdk/pull/5673) | `openhands-sdk/openhands/sdk/llm/utils/openhands_provider.py`<br>`openhands-sdk/openhands/sdk/llm/utils/verified_models.py`<br>`tests/sdk/llm/test_llm.py`<br>`tests/sdk/llm/test_model_list.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - `litellm_call_kwargs` now resolves routed SDK prefixes from one small table: `openhands/` → `litellm_proxy/` + the OpenHands proxy (unchanged), and `digitalocean/` → `openai/` + `https://inference.do-ai.run/v1`. A user-provided `base_url` still wins. - `VERIFIED_MODELS["digitalocean"]` adds a short curated list of DO model IDs (taken from `GET /v1/models`), so DigitalOcean appears in the verified section of the pro | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5673) |
| `9e76fe72b43f` / [#5182](https://github.com/OpenHands/software-agent-sdk/pull/5182) | `.github/agent-server-openapi-weak-schema-allowlist.json`<br>`openhands-sdk/openhands/sdk/agent/agent.py`<br>`openhands-sdk/openhands/sdk/agent/response_dispatch.py`<br>`openhands-sdk/openhands/sdk/event/__init__.py`<br>+5 paths (complete list below) | F06, F07, F08, F32, F33, F34 | - **Async audit masking**: resolves lazy secrets in a worker while releasing the conversation state lock, then rechecks registry stability and emits the masked audit before applying confirmation policy. Regression coverage uses a real loopback HTTP secret service and registers another secret during lookup. - **`SecurityAnalysisEvent`** (`event/security.py`): `source="environment"`, not `LLMConvertible` so the model n | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5182) |
| `dd1af943cb49` / [#5683](https://github.com/OpenHands/software-agent-sdk/pull/5683) | `.github/workflows/stale.yml`<br>`MAINTAINERS` | no product runtime required | - Remove `.github/workflows/stale.yml`. - Remove the unused `MAINTAINERS` roster. | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5683) |
| `bfc1ae71b0e0` / [#5670](https://github.com/OpenHands/software-agent-sdk/pull/5670) | `openhands-tools/openhands/tools/file_editor/editor.py`<br>`tests/tools/file_editor/test_basic_operations.py` | F14, F27, F33 | - Number actual text lines with `splitlines()` instead of numbering newline separators. - Do not append a fabricated output line for an empty file. - Cover unterminated, singly terminated, blank-final-line, and empty files through the public file-editor path. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5670) |
| `6d51e7262fc2` / [#5579](https://github.com/OpenHands/software-agent-sdk/pull/5579) | `clients/typescript/endpoint-audit.config.json`<br>`clients/typescript/src/__tests__/api-clients.test.ts`<br>`clients/typescript/src/client/acp-client.ts`<br>`clients/typescript/src/clients.ts`<br>+20 paths (complete list below) | F01, F03, F04, F05, F06, F07, F09, F12, F18, F19, F20, F21, F22, F23, F24, F30, F31, F32, F33, F34 | - **`POST /api/acp/models`** (new `acp_router.py`) takes `agent_settings` plus the same `secrets` map as a conversation start (`LookupSecret` references to stored secrets work). It returns `ACPModelDiscovery`: `current_model_id` (the server's own default), `available_models`, `agent_name`/`agent_version`, `supports_runtime_model_switch`, and `error` (`ACPAuthRequired` / `ACPStartupTimeout` / `ACPSpawnError` / `ACPIni | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-5579) |
| `e8ad54729e43` / [#5671](https://github.com/OpenHands/software-agent-sdk/pull/5671) | `tests/integration/README.md`<br>`tests/integration/tests/c02_hard_context_reset.py` | no product runtime required | - make c02's hard-reset trigger deterministic without relying on model-generated event counts - validate the actual reset semantics: preserve the system prompt, forget all other prior history, insert the summary after it, and produce a system-first LLM request - verify continued execution and a later normal condensation that preserves the hard-reset summary; align the integration README ### REST API contract changes  | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5671) |
| `1d471742ea7b` / [#5609](https://github.com/OpenHands/software-agent-sdk/pull/5609) | `clients/typescript/package-lock.json`<br>`clients/typescript/package.json`<br>`clients/typescript/src/generated/agent-server-schema.ts`<br>`openhands-agent-server/pyproject.toml`<br>+5 paths (complete list below) | F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34 | ## Release v1.54.0 This PR prepares the release for version **1.54.0**. **Started by:** @neubig ### Release Checklist - [x] Version set to 1.54.0 — four Python packages and TypeScript client synchronized. - [x] Fix any deprecation deadlines if they exist — [Deprecation deadlines passed on current head 298227f40](https://github.com/OpenHands/software-agent-sdk/actions/runs/37801967286). - [ ] Integration tests pass (t | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-5609) |
| `c1c8c1c27824` / [#5682](https://github.com/OpenHands/software-agent-sdk/pull/5682) | `openhands-agent-server/openhands/agent_server/settings_router.py`<br>`tests/agent_server/test_settings_router.py`<br>`tests/cross/test_remote_conversation_live_server.py` | F18, F19, F32 | - Reject blank and redacted-placeholder values with HTTP 422 before persistence. - Leave existing secret values and descriptions unchanged when an invalid upsert is rejected. - Cover new-secret and existing-secret behavior through router and live-server tests. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5682) |
| `9929c14e7d2f` / [#5614](https://github.com/OpenHands/software-agent-sdk/pull/5614) | `.github/workflows/integration-runner.yml`<br>`tests/integration/README.md` | no product runtime required | - Replace the default DeepSeek model ID, display name, and litellm_proxy model mapping with V4.1 Flash. - Update integration coverage documentation; other models and condenser defaults are unchanged. | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5614) |
| `1cfba2117e92` / [#5675](https://github.com/OpenHands/software-agent-sdk/pull/5675) |  | no product runtime required | - Replace the 1,000-call instruction with two separate Python terminal commands, each printing 2,000 repetitions of a short phrase. - Raise the token threshold to 6,000 and keep the seeded context small so a single tool result clears it by a wide margin on every CI model. - Set `keep_first=2` so the user instruction survives the first condensation and the second command still runs. - Raise the iteration cap from 10 t | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5675) |
| `e2bac66be26e` / [#5291](https://github.com/OpenHands/software-agent-sdk/pull/5291) | `openhands-sdk/openhands/sdk/llm/utils/model_features.py`<br>`tests/sdk/llm/test_model_features.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - Add `"openai.gpt-"` to `SUPPORTS_STOP_WORDS_FALSE_MODELS`, next to o1/o3/grok-4. - Tests: five Bedrock ids that must drop stop words, one Claude-on-Bedrock id that keeps them. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5291) |
| `c4b93299cf2b` / [#5661](https://github.com/OpenHands/software-agent-sdk/pull/5661) | `openhands-agent-server/openhands/agent_server/api.py`<br>`openhands-agent-server/openhands/agent_server/init_router.py`<br>`tests/agent_server/test_init_router.py` | F01, F03, F05, F30, F31, F32 | - `require_initialized` takes an `HTTPConnection`, so the same dependency resolves on HTTP and WebSocket routes. HTTP requests still get a 503 while dormant; WebSockets get `WebSocketException(1013 Try Again Later)`, which closes the socket before the handshake is accepted. (Raising `HTTPException` from a WebSocket dependency would reach the app's HTTP exception handler, which reads `request.method` and fails.) - The | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5661) |
| `c96875fe3d03` / [#5703](https://github.com/OpenHands/software-agent-sdk/pull/5703) | `openhands-sdk/openhands/sdk/llm/utils/AGENTS.md`<br>`openhands-sdk/openhands/sdk/llm/utils/unverified_models.py`<br>`openhands-sdk/openhands/sdk/llm/utils/verified_models.py`<br>`tests/agent_server/test_llm_router.py`<br>+3 paths (complete list below) | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - Include curated, verified OpenRouter routes in SDK model discovery with their `openrouter/` prefix, independently of LiteLLM's cost map. - Exercise the HTTP filter with fixed catalogs both containing and omitting OpenRouter entries; preserve catalog-only routes, sorting, deduplication, and provider isolation. - Replace live-catalog model and capability assertions with SDK contract checks, correct curation guidance, | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5703) |
| `f824a949af7f` / [#5700](https://github.com/OpenHands/software-agent-sdk/pull/5700) | `openhands-agent-server/openhands/agent_server/api.py`<br>`tests/agent_server/test_init_router.py`<br>`tests/cross/test_remote_conversation_live_server.py` | F01, F03, F05, F30, F31, F32 | - Run the deferred-initialization gate before authentication on `/api/*`, `/v1/*`, and workspace-file routes. - Apply the same gate to all Canvas backend ingress, including session, HTTP proxy, and WebSocket routes. - Add deterministic transition-race coverage plus a real Uvicorn/wsproto lifecycle regression. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5700) |
| `4cc058275c5a` / [#4838](https://github.com/OpenHands/software-agent-sdk/pull/4838) | `openhands-sdk/openhands/sdk/mcp/client.py`<br>`tests/sdk/mcp/test_create_mcp_tool.py` | F19 | - Reset a completed FastMCP session under its existing session lock before reconnecting. - Add a real HTTP regression test that injects one 504 response and verifies the next tool call succeeds. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-4838) |
| `3934349da2db` / [#5436](https://github.com/OpenHands/software-agent-sdk/pull/5436) | `openhands-sdk/openhands/sdk/llm/auth/openai.py`<br>`tests/agent_server/test_llm_router.py`<br>`tests/sdk/llm/auth/test_openai.py` | F04, F06, F07, F09, F20, F22, F23, F24, F30, F33, F34 | - Add `gpt-6.1-sol`, `gpt-6-sol`, and `gpt-6-luna` to the ChatGPT subscription allowlist. - Keep subscription models separate from the Codex ACP model catalog. Current `main` already decouples these surfaces, so the PR no longer advertises models that the pinned ACP adapter rejects. - Verify the Agent Server subscription-model endpoint exposes the full allowlist. - Parameterize subscription LLM construction coverage  | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-5436) |
| `3dcb27b227aa` / [#5715](https://github.com/OpenHands/software-agent-sdk/pull/5715) | `openhands-tools/openhands/tools/terminal/constants.py`<br>`openhands-tools/openhands/tools/terminal/terminal/terminal_session.py`<br>`tests/tools/terminal/test_terminal_session.py` | F13, F27, F33 | - The poll interval starts at `MIN_POLL_INTERVAL` (0.02 s) and grows with the command's run time (`elapsed / 4`) up to `POLL_INTERVAL` (0.5 s). - A short command is noticed within a few tens of milliseconds. - A command running longer than 2 s is polled every 0.5 s, as before. - No command duration waits longer than it does today. - New test: the first poll is under half of `POLL_INTERVAL`, intervals never shrink, an | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5715) |
| `c0471362d340` / [#5689](https://github.com/OpenHands/software-agent-sdk/pull/5689) | `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`<br>`tests/agent_server/canvas_extensions/test_canvas_extension_backend.py` | F28 | - Reap the owned process group before reporting an exited backend as unhealthy. Live backends retain graceful TERM-to-KILL shutdown, while descendants left by an already-exited leader are terminated immediately. - Reclaim an exited runtime before a direct restart, and keep forced installs and data deletion blocked while its process group remains alive. - Add real-subprocess coverage for status, direct stop, direct re | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5689) |
| `3385a486997e` / [#5587](https://github.com/OpenHands/software-agent-sdk/pull/5587) | `openhands-sdk/openhands/sdk/llm/utils/model_features.py`<br>`tests/sdk/llm/test_llm.py`<br>`tests/sdk/llm/test_model_features.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - Add `claude-haiku-5-5` to `PROMPT_CACHE_MODELS`. - Test fallback recognition and real message formatting for both models with bare, Anthropic, and LiteLLM-proxy names. - Check automatic cache markers with caching enabled and disabled. Public APIs and cache policy remain unchanged. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5587) |
| `e696f72c260b` / [#5659](https://github.com/OpenHands/software-agent-sdk/pull/5659) | `openhands-agent-server/openhands/agent_server/config.py`<br>`openhands-agent-server/openhands/agent_server/init_router.py`<br>`openhands-agent-server/openhands/agent_server/sockets.py`<br>`tests/agent_server/test_config.py`<br>+3 paths (complete list below) | F01, F02, F03, F06, F07, F13, F29, F31, F32 | - `Config` gets a `session_api_keys` validator that drops empty strings, so env slots, the JSON config and explicit construction are normalized in one place: `[""]` becomes `[]`, `[key, ""]` becomes `[key]`. This matches the legacy `SESSION_API_KEY=""`, which `_default_session_api_keys` already treats as no key. - Both WebSocket auth paths (legacy query/header and first-frame auth) refuse an empty presented key even  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5659) |
| `e3a26821f8f7` / [#5657](https://github.com/OpenHands/software-agent-sdk/pull/5657) | `openhands-sdk/openhands/sdk/llm/utils/telemetry.py`<br>`openhands-sdk/openhands/sdk/utils/redact.py`<br>`tests/sdk/llm/test_llm_log_completions_integration.py`<br>`tests/sdk/utils/test_redact.py` | F04, F06, F07, F09, F19, F20, F22, F23, F30, F33, F34 | - `Telemetry.on_request` copies the logged `extra_headers` and `headers` before the provider call and passes them through the existing `sanitize_dict` helper (`openhands/sdk/utils/redact.py`), so in-place changes during the call can't reach the log and credential-like headers are logged as `<redacted>`. - Successful calls (`log_llm_call`) and failed ones (`on_error`) both log from this context, and the server's log e | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5657) |
| `acca181af1f3` / [#5621](https://github.com/OpenHands/software-agent-sdk/pull/5621) | `.agents/skills/verify-agent-server/SKILL.md`<br>`.agents/skills/verify-agent-server/references/adaptation.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F01-server-status.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F02-auth-and-sessions.md`<br>+42 paths (complete list below) | no product runtime required | - A contributor skill under `.agents/skills/verify-agent-server/`: a control CLI (`control-agent-server`), an executable feature map (34 families, one file each), and maintenance levers. - **`control-agent-server`** launches isolated servers from the checkout (private HOME, persistence, keys, port), drives any REST route or WebSocket with assertions, arranges fixtures (git repos, skills, plugins, MCP servers, webhook | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5621) |
| `d97eb76f6082` / [#5724](https://github.com/OpenHands/software-agent-sdk/pull/5724) | `openhands-sdk/openhands/sdk/security/_shell_ast.py`<br>`openhands-sdk/openhands/sdk/security/defense_in_depth/pattern.py`<br>`openhands-sdk/openhands/sdk/security/defense_in_depth/policy_rails.py`<br>`openhands-sdk/openhands/sdk/security/defense_in_depth/shell_semantics.py`<br>+3 paths (complete list below) | F08 | - Parse each original executable segment with the shared tree-sitter Bash parser. - Reuse the shared command view for command words and redirect-owned arguments. - Resolve quoted command names, paths, supported wrappers, descriptor flow, and literal nested or piped shell source. - Return `UNKNOWN` when relevant pipeline output, redirects, consumers, or inherited input cannot be resolved. - Treat `command -v/-V` as in | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5724) |
| `7bd668c9ab74` / [#5660](https://github.com/OpenHands/software-agent-sdk/pull/5660) | `openhands-agent-server/openhands/agent_server/config.py`<br>`openhands-agent-server/openhands/agent_server/init_router.py`<br>`tests/agent_server/test_config.py`<br>`tests/agent_server/test_init_router.py` | F01, F02, F03, F29, F31, F32 | - A `field_validator` on `Config.secret_key` treats a blank value (empty or whitespace only) as unset by returning `_default_secret_key()`, the same fallback used when the variable is absent. The first session key is used when one is set; otherwise there is no cipher and the existing warning is logged. This covers the env var, the JSON config file and direct `Config(...)` construction. - `/api/init` applies `secret_k | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5660) |
| `c1dfd9c52cd3` / [#5520](https://github.com/OpenHands/software-agent-sdk/pull/5520) | `openhands-sdk/openhands/sdk/subagent/scope.py`<br>`openhands-sdk/openhands/sdk/tool/tool.py`<br>`openhands-tools/openhands/tools/task_tracker/definition.py`<br>`tests/tools/task/test_sub_agent_scope.py` | F27, F33 | - **New `ToolDefinition.conversation_local` class flag** (default `False`). It marks tools that only read and write their own conversation's state. - **`SubAgentScope.missing_from` ignores conversation-local tools.** It's the single check behind the task tool's listing, the `TaskManager` start check and both workflow tools, so all of them change together. - **`TaskTrackerTool` sets the flag.** Every other tool is sti | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5520) |
| `d6165a51cb15` / [#5744](https://github.com/OpenHands/software-agent-sdk/pull/5744) | `openhands-sdk/openhands/sdk/profiles/seed.py`<br>`tests/sdk/profiles/test_resolver.py` | F21 | Copy the existing deny-list into the seeded profile. Inline skills still do not become positive references, and unknown disabled names remain harmless. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5744) |
| `4011cb5d4fd5` / [#5409](https://github.com/OpenHands/software-agent-sdk/pull/5409) | `openhands-agent-server/openhands/agent_server/agent_profiles_router.py`<br>`openhands-agent-server/openhands/agent_server/conversation_service.py`<br>`openhands-agent-server/openhands/agent_server/docker_runtime/routers.py`<br>`openhands-agent-server/openhands/agent_server/launch.py`<br>+16 paths (complete list below) | F01, F04, F05, F06, F07, F10, F12, F16, F18, F19, F20, F21, F22, F27, F29, F30, F31, F32, F33, F34 | - `StartConversationRequest.agent_profile` launches an unsaved profile. It is resolved exactly like a stored one, never saved, and recorded as `launched_agent_profile.inline`. It is mutually exclusive with the other agent sources. - `AgentLaunchAdditions.llm_profile_ref` launches an OpenHands profile with another LLM profile, without modifying the profile. It is recorded in `launched_agent_profile.llm_profile_ref`. I | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5409) |
| `27308a4b3f23` / [#4960](https://github.com/OpenHands/software-agent-sdk/pull/4960) | `openhands-sdk/openhands/sdk/conversation/request.py`<br>`openhands-sdk/openhands/sdk/launch/finalize.py`<br>`tests/agent_server/docker_runtime/test_mediation.py`<br>`tests/agent_server/test_agent_launch_additions.py` | F04, F06, F21, F30, F33, F34 | - `AgentLaunchAdditions.skills: list[Skill] \| None` — deployment-supplied skills, on the channel already carrying deployment context, applied in `finalize` right after `system_message_suffix_append`. - `finalize` merges them by name with two rules that keep it from becoming a back door: - **the resolved agent wins a collision** — its skills came from the profile and the server's own sources, the more authoritative v | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-4960) |
| `733f06edac35` / [#5727](https://github.com/OpenHands/software-agent-sdk/pull/5727) | `.agents/skills/verify-agent-server/SKILL.md`<br>`.agents/skills/verify-agent-server/references/adaptation.md`<br>`.agents/skills/verify-agent-server/references/daily.md`<br>`.agents/skills/verify-agent-server/references/feature-map/README.md`<br>+3 paths (complete list below) | no product runtime required | - New `references/daily.md`, linked from `SKILL.md`, `maintenance.md` and `adaptation.md`. - `map baseline [--set]` reads or moves the baseline line; `map check` validates it (now `main@<sha> (<date>)`). - `map owners` with no arguments diffs from the baseline and lists the families. - `evidence report --baseline` lists newly failing, newly passing, not-checked and conflicting rows. Ledgers are compared by attempt ti | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5727) |
| `dfd8fe8fb39e` / [#5728](https://github.com/OpenHands/software-agent-sdk/pull/5728) | `.agents/skills/verify-agent-server/SKILL.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F02-auth-and-sessions.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F03-deferred-init.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md`<br>+34 paths (complete list below) | no product runtime required | - **Marker contract.** Every marker now names the bug's own symptom: `# bug: PREDICATE`, made of `PATH OP [VALUE]` clauses joined by `&&`. They are matched like `--check` against the JSON that the last `control-agent-server` call on that line printed. `exit N` is kept only as a last resort. Here is `F08.secrets-bad-value` before and after: ```sh # before control-agent-server api POST "/api/conversations/$KID/secrets" | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5728) |
| `a430c778bd8a` / [#5754](https://github.com/OpenHands/software-agent-sdk/pull/5754) | `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`<br>`openhands-sdk/openhands/sdk/subagent/AGENTS.md`<br>`openhands-sdk/openhands/sdk/subagent/registry.py`<br>`tests/sdk/subagent/test_subagent_registry.py` | F27 | - `register_file_agents` and `register_plugin_agents` now register each definition through one helper, `_register_definition`, which catches the `ValueError` from `agent_definition_to_factory`, logs `Skipping <file-based\|plugin> agent '<name>' from <source>: <error>`, skips that definition and keeps registering the others. Valid skill resolution is unchanged. - `openhands-sdk/openhands/sdk/subagent/AGENTS.md` states | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5754) |
| `6a298a48fcdb` / [#5759](https://github.com/OpenHands/software-agent-sdk/pull/5759) | `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`<br>`openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`<br>`tests/agent_server/canvas_extensions/test_canvas_extension_backend.py` | F28 | - `status()` now also re-probes a live process that is `unhealthy` only because a probe failed (not one that exited or failed to start), and sets `ready` when the probe succeeds. - `start()` with the same revision on a live process returns that refreshed status instead of the cached one. - Unit test with a real backend subprocess (slow probe, still slow, recovery, then the same via `start`); the `F28.status-probe-lat | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5759) |
| `6e7a8532ef44` / [#4814](https://github.com/OpenHands/software-agent-sdk/pull/4814) | `examples/01_standalone_sdk/48_conversation_fork.py`<br>`openhands-agent-server/openhands/agent_server/conversation_service.py`<br>`openhands-sdk/openhands/sdk/conversation/base.py`<br>`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`<br>+3 paths (complete list below) | F04, F05, F08, F09, F10, F12, F31, F32, F33 | - Keep the `title` keyword for compatibility, but deprecate it locally and stop adding or overwriting tags. `LocalConversation` does not store titles. - Preserve agent-server and `RemoteConversation` titles through `StoredConversation.title`, and keep fork metadata tags consistent with state when tags are omitted. - Merge current `main` (`c0471362d340ade1e334595ba98417108b415e88`), retain its concurrent-fork lifecycl | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-4814) |
| `c3ecd82c7807` / [#5478](https://github.com/OpenHands/software-agent-sdk/pull/5478) | `openhands-sdk/openhands/sdk/llm/utils/verified_models.py`<br>`tests/sdk/llm/test_model_list.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - Add `gpt-6.1-sol` to `VERIFIED_OPENAI_MODELS`, `VERIFIED_OPENROUTER_MODELS` (`openai/gpt-6.1-sol`), and `VERIFIED_OPENHANDS_MODELS`. - Add `claude-sonnet-5-5` to `VERIFIED_ANTHROPIC_MODELS`, `VERIFIED_OPENROUTER_MODELS` (`anthropic/claude-sonnet-5.5`), and `VERIFIED_OPENHANDS_MODELS`. - Add `gemini-4-argon` to `VERIFIED_GEMINI_MODELS` and `VERIFIED_OPENROUTER_MODELS` (`google/gemini-4-argon`); not surfaced under th | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5478) |
| `7f7f05209200` / [#4926](https://github.com/OpenHands/software-agent-sdk/pull/4926) | `AGENTS.md`<br>`clients/typescript/src/models/acp.ts`<br>`openhands-sdk/openhands/sdk/settings/acp_providers.py` | F04, F09, F18, F19, F20, F21, F22, F23, F30, F33, F34 | - Clarify in the Python and TypeScript registry modules that they carry metadata, not deployment policy. - Document the one-way `SURFACED_ACP_PROVIDERS ⊆ ACP_PROVIDERS` invariant and reject complementary hidden-provider lists. - Record the cross-repository contract in `AGENTS.md`; amend #4841 and #4833 to match the latest correction. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-4926) |
| `d332f8156f12` / [#5981](https://github.com/OpenHands/software-agent-sdk/pull/5981) | `openhands-agent-server/openhands/agent_server/launch.py`<br>`openhands-agent-server/openhands/agent_server/server_details_router.py`<br>`openhands-agent-server/openhands/agent_server/skills_service.py`<br>`openhands-sdk/openhands/sdk/launch/preview.py`<br>+13 paths (complete list below) | F01, F04, F21, F25, F27, F29, F30, F33, F34 | - `OpenHandsAgentProfile.public_skills`: `null` keeps every public-catalog skill, as today; `[]` keeps none; a list keeps only those names, and unknown names are ignored. User and project skills are unaffected, and `disabled_skills` still applies to everything. - `LaunchStores.public_skill_names` tells the resolver which catalog skills are public. The agent-server derives it from the same loaders as the catalog, so a | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5981) |
| `0bd5cc2d322d` / [#5747](https://github.com/OpenHands/software-agent-sdk/pull/5747) | `openhands-agent-server/openhands/agent_server/sub_agents_router.py`<br>`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`<br>`openhands-sdk/openhands/sdk/profiles/resolver.py`<br>`openhands-sdk/openhands/sdk/subagent/AGENTS.md`<br>+10 paths (complete list below) | F05, F08, F09, F10, F21, F27, F33 | - **`inherit_mcp_config(parent, sub_agent)`** (`openhands.sdk.subagent.scope`) returns the built sub-agent with exactly `parent.agent.mcp_config`. That is the delegating conversation's *resolved* config: plugin servers already merged, `${VAR}` secrets already expanded, tool filters and headers intact. Servers the child declares are dropped, never merged, and an INFO log names them (names only, no config). `TaskManage | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5747) |
| `4bb57df68465` / [#5926](https://github.com/OpenHands/software-agent-sdk/pull/5926) | `.agents/skills/verify-agent-server/references/feature-map/F24-openai-subscription.md`<br>`openhands-agent-server/openhands/agent_server/llm_router.py`<br>`tests/agent_server/test_llm_router.py` | F23, F24 | - `get_openai_subscription_status` now catches `httpx.HTTPError` alongside `RuntimeError` around `refresh_if_needed()`, so an unreachable OpenAI reads as `200 connected: false`, like a rejected refresh. The credentials file is untouched, so status refreshes it once the network returns. - Unit test in `tests/agent_server/test_llm_router.py` for a refresh that raises `httpx.ConnectError`. - Map: the `F24.status-offline | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-5926) |
| `1a0fd91ad03d` / [#5923](https://github.com/OpenHands/software-agent-sdk/pull/5923) | `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`<br>`openhands-agent-server/openhands/agent_server/canvas_extensions_bridge_router.py`<br>`tests/agent_server/canvas_extensions/test_canvas_extension_bridge_routes.py` | F29 | - `canvas_extensions_bridge_router.py`: the root HTTP and WebSocket routes get their own handlers that take no `path` argument and always proxy to `/`; the sub-path handlers now require `path` (always bound from the URL). `bridge.py` is untouched. - New focused test `tests/agent_server/canvas_extensions/test_canvas_extension_bridge_routes.py` checks which upstream path each route form passes to the bridge, for HTTP ( | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5923) |
| `1c34a84ccea3` / [#5920](https://github.com/OpenHands/software-agent-sdk/pull/5920) | `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`<br>`openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`<br>`openhands-agent-server/openhands/agent_server/canvas_extensions/bridge.py`<br>`tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`<br>+1 paths (complete list below) | F28, F29 | - `CanvasExtensionBackendManager.ready_endpoint` returns `None` once the backend process has exited, so the HTTP proxy, WebSocket bridge and bootstrap answer 503 not ready without a status poll. - `proxy_app_backend_http` renames a connection failure to `502 Canvas App backend unreachable`. - F29 map: `F29.backend-crash-detected` drops its `# bug` marker; its prose and the Gotchas line now describe the fixed behaviou | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5920) |
| `2ec5b9ed4a67` / [#5944](https://github.com/OpenHands/software-agent-sdk/pull/5944) | `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`<br>`openhands-agent-server/openhands/agent_server/skills_service.py`<br>`openhands-sdk/openhands/sdk/git/cached_repo.py`<br>`openhands-sdk/openhands/sdk/skills/utils.py`<br>+2 paths (complete list below) | F15, F25 | - Add an opt-in `strict` flag to `try_cached_clone_or_update` (threaded through `_update_repository`) and `update_skills_repository`: when set, a failed fetch, or a failed checkout of the requested ref after it, returns `None` instead of the stale path. The default (`strict=False`) keeps the fall-back-to-cache behaviour for every other caller. - `sync_public_skills` uses `strict=True`, so the router reports `status:  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5944) |
| `9c0b99ec0077` / [#5725](https://github.com/OpenHands/software-agent-sdk/pull/5725) | `.github/workflows/pip-revendoring-retirement.yml`<br>`openhands-agent-server/openhands/agent_server/docker/Dockerfile`<br>`tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py` | F01, F12, F16, F31 | - Add a `pip-revendored` build stage. It fetches pip 26.2.1's tagged source (checked with `ADD --checksum`), bumps `vendor.txt` to urllib3 2.8.0 and msgpack 1.2.1, and runs pip's own `vendoring sync`. This applies pip's import rewrites and its `tools/vendoring/patches` (including the three urllib3 patches) and regenerates `bom.cdx.json`. The stage then builds a pip wheel. The GitHub source archive is used because the | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5725) |
| `95cb37b7f04f` / [#5986](https://github.com/OpenHands/software-agent-sdk/pull/5986) | `openhands-sdk/openhands/sdk/git/utils.py`<br>`tests/sdk/extensions/test_fetch.py` | F14, F15 | - `normalize_git_url` leaves Azure DevOps clone URLs (any path containing `/_git/`: dev.azure.com, `*.visualstudio.com`, Azure DevOps Server) without a `.git` suffix. It still strips a trailing slash. Other hosts are unchanged. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5986) |
| `4dc42d3a2b65` / [#5929](https://github.com/OpenHands/software-agent-sdk/pull/5929) | `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`<br>`openhands-agent-server/openhands/agent_server/__main__.py`<br>`tests/agent_server/canvas_extensions/test_canvas_extension_bridge.py`<br>`tests/agent_server/test_preload_modules.py` | F02, F27 | - `__main__.py`: resolve the agent-server config once through a small `_load_server_config()` helper (shared with `_auth_enabled`) and pass `proxy_headers=_trust_forwarded_headers()` to uvicorn's `Config`. No change to the bridge proxy code. - Tests: `test_preload_modules.py` checks the `proxy_headers` value `main()` passes for each flag setting; `canvas_extensions/test_canvas_extension_bridge.py` runs the bridge on  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5929) |
| `98c97e6f18ce` / [#5909](https://github.com/OpenHands/software-agent-sdk/pull/5909) | `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F26-plugins.md`<br>`openhands-agent-server/openhands/agent_server/plugins_router.py`<br>`openhands-agent-server/openhands/agent_server/skills_router.py`<br>+3 paths (complete list below) | F25, F26, F28 | - `openhands.sdk.extensions.fetch`: the GitHub shorthand takes an optional path after `owner/repo`, and `fetch_with_resolution` uses that path as `repo_path`. The existing containment check still applies, so `github:o/r/../..` is rejected. - Install source descriptions: the skill description drops the tree URL example and shows a git URL with `repo_path` instead. Both descriptions now say the shorthand can name a pat | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5909) |
| `d60b176ce32b` / [#5945](https://github.com/OpenHands/software-agent-sdk/pull/5945) | `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`<br>`openhands-agent-server/openhands/agent_server/api.py`<br>`tests/agent_server/test_api.py` | F01, F03, F05, F30, F31, F32 | - At shutdown, `api_lifespan` removes the per-server tmux directory, but only when this process created it. Removal is best-effort: a failure is logged as a warning and shutdown continues. A user-provided `TMUX_TMPDIR`, or a default directory that already existed, is never removed. Env restoration is unchanged. - Tests: the lifespan test asserts the directory is gone after exit. New tests cover these cases: - a user- | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5945) |
| `1045bdffdb8d` / [#5918](https://github.com/OpenHands/software-agent-sdk/pull/5918) | `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`<br>`openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py`<br>`tests/agent_server/docker_runtime/test_proxy.py` | F12, F29 | - `proxy_http` (`docker_runtime/proxy.py`) now sends no body when the client sent neither `Content-Length` nor `Transfer-Encoding`, re-adds the client's `Content-Length` while still streaming the body (httpx then does not chunk), and keeps a chunked client body chunked. A `body=` replacement is passed as bytes, so httpx sets its `Content-Length`. - New parametrized unit test in `tests/agent_server/docker_runtime/test | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5918) |
| `4e32a3adb052` / [#5907](https://github.com/OpenHands/software-agent-sdk/pull/5907) | `.agents/skills/verify-agent-server/references/feature-map/F16-workspace-serving-and-vscode.md`<br>`openhands-agent-server/openhands/agent_server/vscode_service.py`<br>`tests/agent_server/test_vscode_service.py` | F16 | - `VSCodeService.get_vscode_url` returns `None` unless the server is running. `get_vscode_service()` seeds the token from `session_api_keys[0]` before `start()` runs, so the token alone never meant a server was there (and `start()` also mints a token before its port check). - Unit tests: the not-running case with a token, and a failed `start()` (port taken) leaving no URL; the existing URL-shape tests now mark the se | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5907) |
| `f67cba804501` / [#5787](https://github.com/OpenHands/software-agent-sdk/pull/5787) | `.agents/skills/verify-agent-server/references/feature-map/F02-auth-and-sessions.md`<br>`openhands-agent-server/openhands/agent_server/auth_router.py`<br>`tests/agent_server/test_workspace_cookie_auth.py` | F02 | - `_request_is_secure_context` strips a `:port` suffix only from a bracketed host or a host with exactly one colon, so a bare IPv6 hostname is kept whole. - Tests: `Host: [::1]` and `Host: [::1]:8000` mints get `Secure; Partitioned`; a bracketed `X-Forwarded-Host: [::1]:8000` keeps working. - Map: `F02.workspace-session-ipv6-loopback` loses its `# bug` marker and "known bug" label; its prose now states the expected b | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5787) |
| `91b89bdc6f33` / [#5774](https://github.com/OpenHands/software-agent-sdk/pull/5774) | `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`<br>`openhands-agent-server/openhands/agent_server/api.py`<br>`openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`<br>`tests/agent_server/canvas_extensions/test_canvas_extension_backend.py` | F01, F03, F05, F28, F30, F31, F32 | - `CanvasExtensionBackendManager` now writes a small record per started backend under `backends/run/<pgid>.json`: the process group, the leader's start time (`/proc/<pid>/stat`), and the server's own pid and start time. `_terminate_runtime` removes the record once the group is stopped. - New `reap_orphans()`, called from the API lifespan before routes are served. For each record whose server is no longer running, it  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5774) |
| `dc897208faae` / [#5891](https://github.com/OpenHands/software-agent-sdk/pull/5891) | `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`<br>`openhands-sdk/openhands/sdk/git/git_diff.py`<br>`openhands-sdk/openhands/sdk/git/utils.py`<br>`tests/sdk/git/test_git_diff.py` | F14, F15 | - `run_git_command` gains a keyword-only `strip: bool = True`. All existing callers keep stripped output. - `get_git_diff` reads the `git show` blob with `strip=False` and normalizes it the way it already normalizes the modified side, so an unchanged file has identical sides. CRLF files behave as before. - Map: the `F15.diff-whitespace` bullet drops its `# bug` marker and "known bug" label, and the prose and Gotchas  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5891) |
| `923ff2de5fd6` / [#5903](https://github.com/OpenHands/software-agent-sdk/pull/5903) | `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`<br>`openhands-agent-server/openhands/agent_server/skills_service.py`<br>`tests/agent_server/test_skills_service.py` | F25 | - `sync_public_skills` clears the marketplace catalog cache on success (new `_invalidate_catalog_cache` helper); a failed sync keeps it. - `service_get_marketplace_catalog` caches only non-empty results, the same rule `plugins_service` already uses. - Unit tests for both; the `F25.marketplace-stale-cache` map bullet loses its known-bug marker and its prose and Gotchas line now describe the fixed behaviour. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5903) |
| `7bb4be18ca42` / [#5905](https://github.com/OpenHands/software-agent-sdk/pull/5905) | `.agents/skills/verify-agent-server/references/feature-map/F26-plugins.md`<br>`openhands-sdk/openhands/sdk/extensions/installation/manager.py`<br>`tests/sdk/plugin/test_installed_plugins.py` | F25, F26, F28 | - `InstallationManager.install` now loads a remote source fetched without `repo_path` from a temporary copy (without `.git`) named after the repository (`extract_repo_name`, the helper that names the cache directory), so a loader that infers the name from the directory sees `<repo>`, as for a local directory. Local sources and `repo_path` installs load in place as before; the installed files are still copied from the | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5905) |
| `4804aa0be06f` / [#5921](https://github.com/OpenHands/software-agent-sdk/pull/5921) | `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`<br>`openhands-agent-server/openhands/agent_server/sub_agents_router.py`<br>`tests/agent_server/test_sub_agents_router.py` | F27 | - `get_sub_agents` passes `enable_browser=configured_browser_available(config) is not False` to `discover_builtin_agents`. The function already filtered `web-researcher`; it was just never told to. - Unit test (parametrized on `enable_browser`) in `tests/agent_server/test_sub_agents_router.py`. - Map: `F27.subagents-browser-disabled` now restarts with `enable_browser: false` itself. Before, it relied on an earlier bu | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5921) |
| `75105bd40936` / [#5931](https://github.com/OpenHands/software-agent-sdk/pull/5931) | `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`<br>`openhands-agent-server/openhands/agent_server/file_router.py`<br>`tests/agent_server/test_file_router.py`<br>`tests/agent_server/test_runtime_router.py` | F14 | - `_upload_file` now maps `IsADirectoryError` to 400 `Path is a directory`, and `FileExistsError` / `NotADirectoryError` (which `mkdir(parents=True)` raises when a parent is a file) to 400 `A parent of the path is not a directory`. Every other failure still returns 500. The conversation-scoped route `POST /api/conversations/{id}/file/upload` mounts the same handler. - Tests: a parametrised test on the global route co | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5931) |
| `8efff8d3cf1a` / [#5924](https://github.com/OpenHands/software-agent-sdk/pull/5924) | `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`<br>`openhands-agent-server/openhands/agent_server/server_details_router.py`<br>`tests/agent_server/test_server_details_router.py` | F01, F29 | - `build_server_info` takes `browser_available` and `get_server_info` passes `configured_browser_available(config)`, the same decision the catalog route applies; `None` (local runtime, browser enabled) keeps the Chromium probe. - Unit tests for the route with `enable_browser=False` and for `build_server_info` with `None`/`True`/`False`. - Map: `F27.server-info-browser-disabled` drops its `# bug` marker, declares `Req | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5924) |
| `818c161b5344` / [#5983](https://github.com/OpenHands/software-agent-sdk/pull/5983) | `openhands-agent-server/openhands/agent_server/agent_profiles_router.py`<br>`openhands-agent-server/openhands/agent_server/conversation_service.py`<br>`openhands-agent-server/openhands/agent_server/docker_runtime/routers.py`<br>`openhands-agent-server/openhands/agent_server/launch.py`<br>+11 paths (complete list below) | F04, F05, F06, F10, F12, F16, F18, F19, F20, F21, F22, F27, F30, F31, F32, F33, F34 | - **The inline launch source is removed.** `StartConversationRequest.agent_profile` and `LaunchedAgentProfile.inline` are gone, along with their handling in launch, Docker forwarding and stored requests. A request whose only agent source is `agent_profile` now fails validation with "One of `agent`, `agent_settings`, or `agent_profile_id` must be provided". Next to another source, `agent_profile` is ignored like any u | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5983) |
| `f0206428306d` / [#5589](https://github.com/OpenHands/software-agent-sdk/pull/5589) | `openhands-agent-server/openhands/agent_server/api.py`<br>`openhands-tools/openhands/tools/terminal/constants.py`<br>`openhands-tools/openhands/tools/terminal/terminal/tmux_pane_pool.py`<br>`openhands-tools/openhands/tools/terminal/terminal/tmux_terminal.py`<br>+3 paths (complete list below) | F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34 | - Record the creating process as each socket's owner via a tmux user option (`@openhands_socket_owner_pid`) from `TmuxTerminal` / `TmuxPanePool` initialization. - `_cleanup_stale_tmux_sessions()` now reclaims an `openhands-<32 hex>` socket only when its recorded owner process is gone (reusing `_is_pid_alive` from `conversation_lease`). The legacy `openhands` socket is still always reclaimed, and sockets with no recor | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-5589) |
| `11d82b2c267a` / [#6003](https://github.com/OpenHands/software-agent-sdk/pull/6003) | `openhands-tools/openhands/tools/task_tracker/definition.py`<br>`tests/tools/task_tracker/__init__.py`<br>`tests/tools/task_tracker/test_task_tracker_status.py` | F27, F33 | - Task statuses are normalized before validation: case, spaces and hyphens are ignored, `pending` and `to do` mean `todo`, and `completed` and `complete` mean `done`. - Other values, such as `blocked` or `cancelled`, are still rejected with the same error. - The schema shown to the model and the saved task list still use only `todo`, `in_progress` and `done`. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-6003) |
| `8ad5845e46fc` / [#5990](https://github.com/OpenHands/software-agent-sdk/pull/5990) | `openhands-sdk/openhands/sdk/plugin/format/agent_plugins.py`<br>`tests/sdk/plugin/test_agent_plugins_format.py` | F09, F26 | - A root `plugin.json` selects the Agent Plugins format only when it declares `$schema`, or when it is unreadable or not a JSON object. A broken or unsupported Agent Plugins manifest is still claimed and rejected, so it is never silently loaded under the directory name. - A root `plugin.json` without `$schema` falls through to the Claude Code layout, which loads the plugin's `skills/` directory. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5990) |
| `b187e88eb018` / [#5984](https://github.com/OpenHands/software-agent-sdk/pull/5984) | `openhands-sdk/openhands/sdk/profiles/resolver.py` | F21 | - Both functions are `@deprecated(deprecated_in="1.55.0", removed_in="1.60.0")`. That is the 5-minor runway `check_deprecations.py` requires. - Each deprecation names its replacement: `openhands.sdk.launch.resolve` for the first, and `openhands.sdk.launch.preview_launch` for the dry-run. - No behavior changes. The module docstring now says what the module is for and points at the replacements. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5984) |
| `fd387b58dc7b` / [#6007](https://github.com/OpenHands/software-agent-sdk/pull/6007) | `clients/typescript/package-lock.json`<br>`clients/typescript/package.json`<br>`clients/typescript/src/generated/agent-server-schema.ts`<br>`openhands-agent-server/pyproject.toml`<br>+4 paths (complete list below) | F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34 | ## Release v1.55.0 This PR prepares the release for version **1.55.0**. **Started by:** @simonrosenberg ### Release Checklist - [x] Version set to 1.55.0 - [ ] Fix any deprecation deadlines if they exist - [ ] Integration tests pass (tagged with `integration-test`) - [ ] Behavior tests pass (tagged with `behavior-test`) - [ ] Example tests pass (tagged with `test-examples`) - [ ] Security scan passes (tagged with `se | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-6007) |
| `f5f849f5ab51` / [#5991](https://github.com/OpenHands/software-agent-sdk/pull/5991) | `openhands-agent-server/openhands/agent_server/docker/Dockerfile`<br>`tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py` | F01, F12, F16, F31 | - The `pip-revendored` stage deletes `setuptools==70.3.0` from pip's `vendor.txt` and removes pip's `pkg_resources.patch` before `vendoring sync`. The rebuilt pip and its `bom.cdx.json` then contain no setuptools. - The retirement guard also checks whether upstream pip still bundles `pkg_resources`. It asks for the stage to be removed only once upstream has also fixed urllib3 and msgpack. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5991) |
| `d5995133d942` / [#5753](https://github.com/OpenHands/software-agent-sdk/pull/5753) | `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`<br>`openhands-sdk/openhands/sdk/git/git_changes.py`<br>`tests/agent_server/test_git_router.py`<br>`tests/sdk/git/test_git_changes.py` | F15 | - `get_git_changes` (`openhands-sdk/openhands/sdk/git/git_changes.py`): when the requested directory is not a repository but repositories were found one level below it, start from no base changes and list theirs under their directory name (sorted by path, as before). If neither the directory nor any directory below it is a valid repository, the original `GitRepositoryError` is still raised, so the agent server still  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5753) |
| `cb266703c5c7` / [#5696](https://github.com/OpenHands/software-agent-sdk/pull/5696) | `openhands-agent-server/openhands/agent_server/file_router.py`<br>`tests/agent_server/test_file_router.py` | F14 | - `download_trajectory` now resolves the conversations directory from `request.app.state.config`, the request-time config pattern already used by `dependencies.py`, `tool_router.py`, and `server_details_router.py`. - The existing trajectory tests install that request-time config instead of patching the module-level lookup. - Regression test drives a real deferred-init app end to end: boot with `deferred_init=True`, ` | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5696) |
| `db9c54b71c8c` / [#5987](https://github.com/OpenHands/software-agent-sdk/pull/5987) | `openhands-agent-server/openhands/agent_server/agent_profiles_router.py`<br>`openhands-agent-server/openhands/agent_server/plugins_router.py`<br>`openhands-agent-server/openhands/agent_server/plugins_service.py`<br>`openhands-sdk/openhands/sdk/plugin/__init__.py`<br>+8 paths (complete list below) | F09, F21, F26, F30 | - Added `preflight_plugin(...)`, `PluginSourceError`, and `CheckedPlugin` to `openhands.sdk.plugin` with strict secret expansion, structured error codes, temporary checkout cleanup, and raw manifest/skills/commands metadata. - Added `POST /api/plugins/fetch` via the existing `/plugins` router to fetch and pin a plugin into an immutable commit-SHA cache path without installing or enabling it. - Added pinned-cache reus | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5987) |
| `5fa4dea24c52` / [#6021](https://github.com/OpenHands/software-agent-sdk/pull/6021) | `clients/typescript/package-lock.json`<br>`clients/typescript/package.json`<br>`clients/typescript/src/generated/agent-server-schema.ts`<br>`openhands-agent-server/pyproject.toml`<br>+4 paths (complete list below) | F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34 | ## Release v1.55.1 This PR prepares the release for version **1.55.1**. **Started by:** @malhotra5 ### Release Checklist - [x] Version set to 1.55.1 - [ ] Fix any deprecation deadlines if they exist - [ ] Integration tests pass (tagged with `integration-test`) - [ ] Behavior tests pass (tagged with `behavior-test`) - [ ] Example tests pass (tagged with `test-examples`) - [ ] Security scan passes (tagged with `securit | documented | family-sweep-with-capability-limits; [feature evidence and limits](intent-runtime-overlay.md#pr-6021) |
| `12e967664c23` / [#5998](https://github.com/OpenHands/software-agent-sdk/pull/5998) | `.agents/skills/pr-design-doc/SKILL.md`<br>`.agents/skills/pr-design-doc/references/html-craft.md`<br>`.github/PULL_REQUEST_TEMPLATE.md`<br>`.github/workflows/pr-artifacts.yml`<br>+3 paths (complete list below) | no product runtime required | - Remove approval cleanup and use the existing cleanup PR after merge to remove `.pr/` from `main` for both same-repository and fork PRs. - Update the artifact notice, contributor instructions, PR template, and design-doc guidance to describe automatic removal after merge to `main`. - Fail the existing pre-commit job only for `rel-` release PRs whose committed tree contains root `.pr` entries. | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-5998) |
| `6530325e9dd0` / [#6009](https://github.com/OpenHands/software-agent-sdk/pull/6009) | `openhands-sdk/openhands/sdk/marketplace/registry.py`<br>`openhands-sdk/openhands/sdk/marketplace/types.py`<br>`tests/agent_server/test_skills_service.py`<br>`tests/sdk/conversation/test_local_conversation_plugins.py`<br>+1 paths (complete list below) | F09, F25, F26 | - When a registered source has no marketplace manifest, the registry reads it as a plain skills folder with the new `Marketplace.from_skills_dir`. Each `<name>/SKILL.md` folder in `skills/` and `.skills/` becomes a standalone skill. If neither folder exists, the skill folders directly in the source path are used, so `repo_path` can point at the skills folder itself. - Loose markdown files are never read, so a reposit | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-6009) |
| `4a5f5a09096a` / [#5955](https://github.com/OpenHands/software-agent-sdk/pull/5955) | `.agents/skills/verify-agent-server/references/feature-map/F30-openai-gateway.md`<br>`openhands-agent-server/openhands/agent_server/event_service.py`<br>`openhands-agent-server/openhands/agent_server/openai/service.py`<br>`tests/agent_server/test_run_admission.py` | F05, F06, F07, F08, F09, F10, F11, F12, F30, F31 | - `EventService.reserve_run_slot()` claims run capacity up front (or returns `None` when the conversation already holds its own run permit or still has a run task wrapping up, so turns on a running conversation are not refused). `send_message` takes an optional `run_slot` and passes it to `run()`. - `_run_agent` (gateway) reserves before `send_message` and releases its handle afterwards. - Map: `F30.chat-reuse-run-li | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5955) |
| `304ea67a7993` / [#5842](https://github.com/OpenHands/software-agent-sdk/pull/5842) | `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`<br>`openhands-agent-server/openhands/agent_server/bash_service.py`<br>`tests/agent_server/test_bash_service.py` | F13 | - `_search_bash_events_sync` (`bash_service.py`) now drops filtered events while it fills the page, so `limit` counts only events that are returned. `next_page_id` names the next event that passes the filter, so a page is never followed by an empty one. - Two unit tests: `order__gt` paging across two pages, and no cursor when every remaining file is filtered out. - The `F13.order-gt-paging` map bullet no longer carri | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5842) |
| `60c6f62133e0` / [#5780](https://github.com/OpenHands/software-agent-sdk/pull/5780) | `.agents/skills/verify-agent-server/references/feature-map/F05-run-pause-interrupt.md`<br>`openhands-agent-server/openhands/agent_server/event_service.py`<br>`tests/agent_server/test_event_service.py` | F05, F06, F07, F08, F09, F10, F11, F12, F31 | - `event_service.py`: log the run failure at ERROR with the plain-text traceback in the message, not via `exc_info`, so no handler renders a rich traceback on the loop. - Tests: a new unit test checks that the record is ERROR, has no `exc_info` and carries the whole chain; the existing logging test is updated. - Map: `F05.run-error-responsive` loses its known-bug marker; the bullet prose and the Gotchas line now desc | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5780) |
| `a6c7bf5591f6` / [#5779](https://github.com/OpenHands/software-agent-sdk/pull/5779) | `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`<br>`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`<br>`tests/agent_server/test_switch_llm_survives_reload.py`<br>`tests/sdk/conversation/test_switch_model.py` | F05, F08, F09, F10 | - `LocalConversation.switch_llm` reuses a registered LLM only when its config (`model_dump(mode="json")` with secrets exposed) equals the posted one; otherwise it replaces the registry entry with the posted LLM and hands it the metrics object already recorded under that `usage_id`, so `stats.usage_to_metrics[usage_id]` keeps its accrued usage. Identical re-switches (`switch_profile`, `classify_and_switch_llm`'s `prof | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5779) |
| `8ba8ccbeb1a5` / [#5772](https://github.com/OpenHands/software-agent-sdk/pull/5772) | `.agents/skills/verify-agent-server/references/feature-map/F11-goals.md`<br>`openhands-agent-server/openhands/agent_server/event_service.py`<br>`tests/agent_server/test_goal_loop.py` | F05, F06, F07, F08, F09, F10, F11, F12, F31 | - `EventService.start()` now reconciles a stale goal status next to the existing stale-`RUNNING` execution-status reconcile: if the last persisted `goal` status is still `active`, it appends one `active: false, status: "interrupted"` status with the same `objective`, `iteration` and `max_iterations` (verdict cleared, like every other `interrupted` status). It is driven by the loaded state, so it covers graceful resta | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5772) |
| `c87fddb623f0` / [#5769](https://github.com/OpenHands/software-agent-sdk/pull/5769) | `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`<br>`clients/typescript/README.md`<br>`clients/typescript/src/__tests__/package-import.test.ts`<br>`clients/typescript/src/__tests__/websocket-callback-client.test.ts`<br>+3 paths (complete list below) | F13, F34 | - New internal helper `src/events/global-websocket.ts`: it returns `globalThis.WebSocket` when called and holds the shared error message. Both clients now call it when they connect. All module-load detection and `require('ws')` code is gone, and the built `dist/` has no `require` or `ws` import. - Tests: the callback-client test stubs `globalThis.WebSocket` instead of a fake `window`. New tests cover a constructor in | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5769) |
| `76055f697a5e` / [#5768](https://github.com/OpenHands/software-agent-sdk/pull/5768) | `.agents/skills/verify-agent-server/references/feature-map/F20-llm-profiles.md`<br>`openhands-agent-server/openhands/agent_server/profiles_router.py`<br>`tests/agent_server/test_profiles_router.py` | F20 | - The ping uses a copy of the LLM with `num_retries: 0`, no `fallback_strategy`, and `timeout` = min(15 s, posted timeout). The posted config itself is not changed. - A rate-limit error that the SDK's existing `is_quota_exhaustion_error` classifies as an exhausted budget/quota answers `valid: false` with the redacted provider message; a plain 429 is still `valid: true`. - verify-agent-server map: `F20.validate-unreac | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5768) |
| `c73ec79e3fd1` / [#5778](https://github.com/OpenHands/software-agent-sdk/pull/5778) | `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`<br>`clients/typescript/src/__tests__/remote-workspace-execute.test.ts`<br>`clients/typescript/src/workspace/remote-workspace.ts`<br>+2 paths (complete list below) | F13, F14, F15, F16, F34 | - `bash_router.py`: the route reads every page of the command's `BashOutput` events (following `next_page_id`), sorts by `order`, joins stdout and stderr separately, and returns the final chunk with the joined text and its exit code. The response is still one `BashOutput`; small outputs are unchanged. - `clients/typescript`: `RemoteWorkspace.executeCommand` maps a missing exit code to `-1` instead of `0`. - verify-ag | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5778) |
| `bf9b6057b00e` / [#6036](https://github.com/OpenHands/software-agent-sdk/pull/6036) | `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py` | no product runtime required | - Skip only tests that require a supported Linux backend artifact. - Add a non-Linux regression test covering `unsupported` status, prepare, and start without extracting an artifact. | documented | no-product-runtime-needed; [feature evidence and limits](intent-runtime-overlay.md#pr-6036) |
| `6235a41b5b84` / [#5951](https://github.com/OpenHands/software-agent-sdk/pull/5951) | `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`<br>`openhands-agent-server/openhands/agent_server/config.py`<br>`openhands-agent-server/openhands/agent_server/env_parser.py`<br>`tests/agent_server/test_config.py` | F01, F02, F03, F29, F31, F32 | - `env_parser.merge` gains a keyword-only `none_overrides` flag (default `False`, so merging inside the environment parser is unchanged); with it set, an explicit `None` in the later value replaces the earlier one, while `MISSING` still keeps it. - `load_config` merges the environment over the file with `none_overrides=True`. Fields with no `OH_*` variable are absent from the env data, so file values are untouched. - | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5951) |
| `8869bcd914af` / [#5840](https://github.com/OpenHands/software-agent-sdk/pull/5840) | `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`<br>`openhands-agent-server/openhands/agent_server/dependencies.py`<br>`tests/agent_server/test_runtime_router.py` | F02, F03, F06, F13, F16, F30 | - `get_bash_event_service`: when the runtime state is missing on a scoped request, raise the path parameter's own validation error (`RequestValidationError`, loc `["path", "runtime_conversation_id"]`), so the response is the same 422 body FastAPI returns for the other scoped routes. A valid id is unchanged; the global `/api/bash/...` routes are unchanged. - Unit test covering all 7 scoped bash routes with `not-a-uuid | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5840) |
| `cab7bb4b2947` / [#5901](https://github.com/OpenHands/software-agent-sdk/pull/5901) | `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`<br>`openhands-sdk/openhands/sdk/skills/installed.py`<br>`tests/sdk/skills/test_installed_skills.py` | F25 | - `_load_skill_from_dir` (`openhands-sdk/openhands/sdk/skills/installed.py`), the loader the skill install path uses, turns `yaml.YAMLError` into `SkillValidationError`, matching how directory loading in `skill.py` already treats YAML errors. The router needs no change: it already maps `SkillValidationError` to 422. - Unit test in `tests/sdk/skills/test_installed_skills.py`: `install_skill` raises `SkillValidationErr | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5901) |
| `acf55bcd2672` / [#5885](https://github.com/OpenHands/software-agent-sdk/pull/5885) | `.agents/skills/verify-agent-server/references/feature-map/F23-llm-catalog-and-connections.md`<br>`openhands-sdk/openhands/sdk/llm/utils/unverified_models.py`<br>`tests/sdk/llm/test_model_list.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - `get_supported_llm_models` now drops every id that LiteLLM's model-cost map files under a Bedrock provider (`litellm_provider` `bedrock`, `bedrock_converse`, `bedrock_mantle`), besides `bedrock`-prefixed ids, instead of adding more string heuristics. Bedrock models listed from AWS when credentials are configured (`bedrock/<modelId>`) are untouched. This also applies to the SDK's `UNVERIFIED_MODELS_EXCLUDING_BEDROCK | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5885) |
| `3adf42f6377b` / [#5884](https://github.com/OpenHands/software-agent-sdk/pull/5884) | `.agents/skills/verify-agent-server/references/feature-map/F22-meta-profiles.md`<br>`openhands-sdk/openhands/sdk/llm/meta_profile_store.py`<br>`tests/sdk/llm/test_meta_profile_store.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34 | - `default_meta_profile_dir()` (`openhands-sdk/openhands/sdk/llm/meta_profile_store.py`) now returns `get_user_persistence_dir() / "meta-profiles"`, so `~` is expanded and a relative value is anchored exactly as for every other store. It is still evaluated at call time, so the router, `launch.py` and bare `MetaProfileStore()` pick it up unchanged. - New unit test for `OH_PERSISTENCE_DIR='~/state'`. - The `F22.persist | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5884) |
| `e5a1602e3bbc` / [#5867](https://github.com/OpenHands/software-agent-sdk/pull/5867) | `.agents/skills/verify-agent-server/references/feature-map/F19-mcp.md`<br>`openhands-agent-server/openhands/agent_server/mcp_router.py`<br>`tests/agent_server/test_mcp_router.py` | F19 | - `_validate_callback_url` turns the `ValueError` from `.port` into `400 Unexpected OAuth callback URL`, and a `ValueError` from `urlparse` itself (e.g. `http://[::1/callback`, unclosed IPv6 bracket) into `400 Invalid OAuth callback URL`. Valid URLs and the existing checks are unchanged; the job keeps waiting. - New parametrized test in `tests/agent_server/test_mcp_router.py` (port 99999, port `abc`, malformed IPv6)  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5867) |
| `7abcd3bab78f` / [#5916](https://github.com/OpenHands/software-agent-sdk/pull/5916) | `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`<br>`openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py`<br>`tests/agent_server/docker_runtime/test_proxy.py` | F12, F29 | - `proxy_http` (`docker_runtime/proxy.py`) drops the upstream `date` and `server` headers from the response it returns, so uvicorn's single pair is the only one. The change is response-side only: `_filter_headers` and request header forwarding are untouched. - `proxy_http` is shared with the Docker runtime conversation proxy, whose inner agent server also sends `date`/`server`, so the same duplication goes away there | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5916) |
| `079d410db4fe` / [#5826](https://github.com/OpenHands/software-agent-sdk/pull/5826) | `.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md`<br>`openhands-agent-server/openhands/agent_server/event_service.py`<br>`tests/agent_server/test_conversation_response.py` | F05, F06, F07, F08, F09, F10, F11, F12, F31 | - `EventService._get_agent_final_response_sync` now passes `state.active_branch()` to `get_agent_final_response`. This covers the REST route and the OpenAI-compatible gateway (`openai/service.py`), which both go through `EventService.get_agent_final_response`. The SDK's own callers (task manager, delegate, agent hooks) read fresh sub-conversations that are never navigated, so they are unchanged; the TypeScript client | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5826) |
| `9ad70fedddc4` / [#5812](https://github.com/OpenHands/software-agent-sdk/pull/5812) | `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`<br>`clients/typescript/src/client/conversation-client.ts`<br>`clients/typescript/src/conversation/remote-conversation.ts` | F34 | - Rewrite both `switchAcpModel` docstrings: live session switches in place via `session/set_model`; before the first run the server answers 200 and stores the model for the first session; non-ACP conversations get 400. - Drop the `# bug` marker from the `F09.ts-client-acp-docs` map bullet, add a positive check for the new wording, and update the F09 Gotchas line. - No code change; the Python docstrings (`conversation | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5812) |
| `f676088d7a46` / [#5802](https://github.com/OpenHands/software-agent-sdk/pull/5802) | `.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md`<br>`openhands-agent-server/openhands/agent_server/models.py`<br>`tests/agent_server/test_conversation_router.py` | F04, F08, F10, F11, F12, F13 | - `UpdateConversationRequest` gets a `title` field validator that rejects a title that is blank after trimming, so the route answers `422` before the service runs and the stored title is kept. Non-blank titles are unchanged (the service still strips them; `max_length=200` still applies). - New router test `test_update_conversation_whitespace_only_title_rejected` (spaces, and tab/newline) asserts `422` and that the se | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5802) |
| `478a6f9caebc` / [#5864](https://github.com/OpenHands/software-agent-sdk/pull/5864) | `.agents/skills/verify-agent-server/references/feature-map/F17-workspaces-registry.md`<br>`openhands-agent-server/openhands/agent_server/persistence/store.py`<br>`tests/agent_server/test_workspaces_router.py` | F03, F17, F18, F31 | - `FileWorkspacesStore.update` skips `save` when `update_fn` returns the document it was given; every route's `apply` already returns its input unchanged for a no-op, so no router change is needed. Real adds and deletes still write (and still answer 500 on read-only storage). - Two unit tests in `tests/agent_server/test_workspaces_router.py`: a refused delete on a fresh directory leaves no file; with `save` failing,  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5864) |
| `07e6e059afd8` / [#5851](https://github.com/OpenHands/software-agent-sdk/pull/5851) | `.agents/skills/verify-agent-server/references/feature-map/F18-settings-and-secrets.md`<br>`openhands-agent-server/openhands/agent_server/settings_router.py`<br>`tests/agent_server/test_settings_router.py` | F18, F19, F32 | - Docstrings of `get_secret_value` and `delete_secret` now say `422 if name format is invalid`. The status code is unchanged. (`PUT /api/settings/secrets` already said 422 since https://github.com/OpenHands/software-agent-sdk/pull/5682.) - New parametrized test checks, for GET, DELETE and PUT, that an invalid name gets 422 and that the route's `/openapi.json` description names 422 and not 400. - Drops the `F18.secret | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5851) |
| `fd96074269ce` / [#5874](https://github.com/OpenHands/software-agent-sdk/pull/5874) | `.agents/skills/verify-agent-server/references/feature-map/F21-agent-profiles.md`<br>`openhands-agent-server/openhands/agent_server/agent_profiles_router.py`<br>`tests/agent_server/test_agent_profiles_router.py` | F21, F30 | - Docstring only: describe what materialize actually reports. No behaviour change. - `test_seed_skips_ghost_llm_profile_for_bare_defaults` now also pins the `errors` entry and the absence of `dangling_llm_profile_ref` in the materialize body. - verify-agent-server map: drop the known-bug marker on `F21.dangling-llm-report` and rewrite its prose as the expected behaviour. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5874) |
| `8d2c996de5bf` / [#5966](https://github.com/OpenHands/software-agent-sdk/pull/5966) | `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`<br>`openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`<br>`tests/sdk/conversation/remote/test_remote_events_list.py` | F33 | - `RemoteEventsList.create_default_callback()` skips `StreamingDeltaEvent`; user callbacks and the visualizer still receive every delta. - Unit test: the default callback keeps deltas out of the cached events. - Map: drop the `# bug` marker from `F33.conversation-streaming-state` and rewrite its prose and the Gotchas line as the expected behaviour. | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5966) |
| `57c71706e83c` / [#5917](https://github.com/OpenHands/software-agent-sdk/pull/5917) | `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`<br>`openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`<br>`tests/agent_server/canvas_extensions/test_canvas_extension_backend.py` | F28 | - `CanvasExtensionBackendManager.prepare` translates archive-format errors from extraction (`tarfile.TarError`, `gzip.BadGzipFile`, `EOFError`, `zlib.error`) into `ValueError("backend artifact is not a valid gzip tar archive")`, so the route answers 409. The temporary extraction directory is still removed and no approval is written. - Unit tests for a plain-bytes, a gzip-but-not-tar and a truncated gzip artifact (man | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5917) |
| `1396fa7ff651` / [#5898](https://github.com/OpenHands/software-agent-sdk/pull/5898) | `.agents/skills/verify-agent-server/references/feature-map/F18-settings-and-secrets.md`<br>`openhands-agent-server/openhands/agent_server/settings_router.py`<br>`tests/agent_server/test_settings_router.py` | F18, F19, F32 | - `list_secrets` now reads the scoping profile inside `store_errors()`, so the route answers 400 / 503 like the agent-profile routes; unknown ids still answer 404 and valid ids still scope the list. - Two unit tests in `tests/agent_server/test_settings_router.py` (unloadable profile -> 400, lock timeout -> 503). - Feature map `F18.secret-scope-profile-errors`: `# bug` markers removed, prose and the Gotchas line rewri | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5898) |
| `75dec1d3ef1c` / [#5875](https://github.com/OpenHands/software-agent-sdk/pull/5875) | `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`<br>`openhands-agent-server/openhands/agent_server/bash_service.py`<br>`tests/agent_server/test_bash_service.py` | F13 | - `BashEventService._timestamp_to_str` now converts aware datetimes to UTC before formatting, matching the UTC file-name prefixes (event timestamps come from `utc_now()`). Naive bounds are still read as UTC. - Focused unit test: `+02:00` bounds on both `timestamp__gte` and `timestamp__lt`, plus the naive case. - Feature map: the `F13.timestamp-offset` bullet loses its `known bug` label and `# bug` marker, its prose a | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5875) |
| `5f2185ed6333` / [#5957](https://github.com/OpenHands/software-agent-sdk/pull/5957) | `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`<br>`clients/typescript/src/__tests__/agent-server-contract-types.test.ts`<br>`clients/typescript/src/__tests__/remote-workspace-git.test.ts`<br>`clients/typescript/src/index.ts`<br>+1 paths (complete list below) | F34 | - `GitChange.status` (`clients/typescript/src/models/workspace.ts`) is now the generated contract's `GitChangeStatus`, so it cannot drift from the server again; `GitChangeStatus` is also exported from the main entry point. - Tests: a type-level check in `agent-server-contract-types.test.ts` (exported status equals the generated one, also through `RemoteWorkspace.gitChanges`) and a runtime test in `remote-workspace-gi | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5957) |
| `47c50f7e5c68` / [#6038](https://github.com/OpenHands/software-agent-sdk/pull/6038) | `openhands-tools/openhands/tools/terminal/terminal/tmux_terminal.py`<br>`tests/agent_server/test_api.py`<br>`tests/tools/terminal/test_tmux_pane_pool.py` | F13, F27, F31, F33 | - Use the Agent Server's canonical-byte-length approach to select an explicit short `/tmp` tmux socket path when the original would exceed the platform's Unix-socket limit, without modifying the process-wide `TMUX_TMPDIR`. - Keep socket creation locking alongside the selected socket path. - Exercise real standalone and pooled terminal sessions under an overlong directory; give shared-socket Agent Server tests short t | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-6038) |
| `f621dab57be8` / [#5973](https://github.com/OpenHands/software-agent-sdk/pull/5973) | `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`<br>`openhands-agent-server/openhands/agent_server/api.py`<br>`openhands-agent-server/openhands/agent_server/conversation_service.py`<br>`openhands-agent-server/openhands/agent_server/event_service.py`<br>+3 paths (complete list below) | F01, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F30, F31, F32 | - `EventService.save_meta(stored=None)` now takes the new `StoredConversation`, writes it under the lease guard, and makes it `self.stored` only after the write succeeded (persist first, as the issue suggests). `update_conversation`, `AutoTitleSubscriber` and `update_secrets` build a `model_copy` and pass it in; `update_conversation` applies the `ConversationState.tags` update only after the save. - `ConversationOwne | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5973) |
| `8cfff960ff3b` / [#5956](https://github.com/OpenHands/software-agent-sdk/pull/5956) | `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`<br>`clients/typescript/src/__tests__/api-clients.test.ts`<br>`clients/typescript/src/client/conversation-client.ts`<br>`clients/typescript/src/conversation/conversation-manager.ts` | F34 | - `ConversationManager.updateConversation` and `ConversationClient.updateConversation` type the PATCH as `Success` and resolve with the conversation read back via `getConversation`. - New unit test for both methods; the existing `ConversationClient` wrapper test accounts for the extra `GET`. - Map: `F34.manager-update-result` loses its `# bug` marker and "known bug" label; the prose and the Gotchas line now describe  | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5956) |
| `804498a61126` / [#5968](https://github.com/OpenHands/software-agent-sdk/pull/5968) | `.agents/skills/verify-agent-server/references/feature-map/F30-openai-gateway.md`<br>`openhands-agent-server/openhands/agent_server/openai/service.py`<br>`tests/agent_server/test_openai_gateway_reused_wait.py` | F30 | - `_wait_for_reused_conversation_completion` now also returns on `paused` and `waiting_for_confirmation`. It keeps the same guard (the run was seen `running`, or new events arrived), so a status left over from the previous turn does not end the wait early. `_raise_for_terminal_error` then answers 409 `Agent run ended with status: <status>`, the same answer a new conversation gets. - New unit test `tests/agent_server/ | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5968) |
| `b301ee008178` / [#5965](https://github.com/OpenHands/software-agent-sdk/pull/5965) | `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`<br>`openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`<br>`tests/sdk/conversation/remote/test_remote_conversation.py` | F33 | - `RemoteConversation.__init__` accepts `token_callbacks` (added as the last named parameter, so positional callers are unaffected) and passes it to `_initialize_connection`. - When token callbacks are given, a small event callback converts each `StreamingDeltaEvent` from the socket into an `LLMStreamChunk` (`choices[0].delta.content` / `reasoning_content`), the same type a local conversation's token callbacks receiv | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5965) |
| `4fedb7e2b524` / [#5877](https://github.com/OpenHands/software-agent-sdk/pull/5877) | `.agents/skills/verify-agent-server/references/feature-map/F21-agent-profiles.md`<br>`clients/typescript/config/public-type-budget.json`<br>`clients/typescript/src/__tests__/agent-server-contract-types.test.ts`<br>`clients/typescript/src/__tests__/api-clients.test.ts`<br>+2 paths (complete list below) | F34 | - `clients/typescript/src/models/agent-profile.ts`: declare the fields the server's Pydantic models send (`openhands-sdk/openhands/sdk/profiles/agent_profile.py`, `resolver.py`), and remove `skills` and `resolved_mcp_servers`. `AgentProfileSaveInput` therefore no longer offers `skills`. - `agent-server-contract-types.test.ts` asserts `keyof` equality between the four exported types and their generated contract counte | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5877) |
| `8ee5cba234d1` / [#5868](https://github.com/OpenHands/software-agent-sdk/pull/5868) | `.agents/skills/verify-agent-server/references/feature-map/F20-llm-profiles.md`<br>`openhands-agent-server/openhands/agent_server/profiles_router.py`<br>`openhands-sdk/openhands/sdk/llm/llm_profile_store.py`<br>`openhands-sdk/openhands/sdk/profiles/profile_refs.py`<br>+2 paths (complete list below) | F04, F06, F07, F09, F20, F21, F22, F23, F30, F33, F34 | - Add `canonical_profile_name()` in `llm_profile_store.py` (used by `_get_profile_path`). `_scan_referrers` / `_rewrite_refs` in `profile_refs.py` compare and write the canonical key. - `profiles_router.py`: `_set_active_profile_if_matches` compares canonical keys and stores the canonical new name, and activation records the canonical name. - Feature map: drop the `F20.json-alias-guard` known-bug marker, assert 409 a | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5868) |
| `e785defbf652` / [#5953](https://github.com/OpenHands/software-agent-sdk/pull/5953) | `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`<br>`openhands-agent-server/openhands/agent_server/conversation_service.py`<br>`tests/agent_server/test_webhook_subscriber.py` | F04, F05, F10, F12, F31, F32 | - `WebhookSubscriber._post_events` re-checks the buffer after draining what was queued when it started, and keeps posting while it is full again (count or `max_batch_bytes`). Still one request in flight; a failed POST still requeues and leaves the rest to the flush timer. Once the queue drains, a flush timer started during the POST is cancelled (not when `_post_events` runs inside that timer). `__call__` is unchanged | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5953) |
| `14f821bb2af5` / [#5848](https://github.com/OpenHands/software-agent-sdk/pull/5848) | `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`<br>`openhands-sdk/openhands/sdk/git/git_changes.py`<br>`tests/sdk/git/test_git_changes.py` | F15 | - `get_changes_in_repo` (`openhands-sdk/openhands/sdk/git/git_changes.py`) passes `--relative` to `git diff --name-status`, so tracked changes are scoped to and relative to the requested directory, like `git ls-files --others`. A repository root is unaffected (`--relative` from the top level is a no-op). - Test: `get_changes_in_repo` on `src/` of a repository with changes inside and outside it (modified, deleted, unt | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5848) |
| `9af99982a125` / [#5967](https://github.com/OpenHands/software-agent-sdk/pull/5967) | `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`<br>`openhands-agent-server/openhands/agent_server/telemetry/subscriber.py`<br>`tests/agent_server/telemetry/test_telemetry_subscriber.py` | F32 | - `TelemetrySubscriber` now records usage from every state update that carries stats: the `key="stats"` updates `EventService` publishes after each LLM call, and `full_state` snapshots. Every terminal emission (`finished`, failure statuses, and the `interrupted` fallback from `close()`) buckets the latest totals; raw figures are still never sent and the subscriber stays total and non-blocking. - New unit tests: `test | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5967) |
| `7d459bcb1111` / [#5964](https://github.com/OpenHands/software-agent-sdk/pull/5964) | `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`<br>`openhands-agent-server/openhands/agent_server/config.py`<br>`tests/agent_server/telemetry/test_telemetry_disabled_by_default.py` | F01, F02, F03, F29, F31, F32 | - Rewrite the `TelemetrySpec.salt` description to match the code: salt, else the secret key (defaulting to the first session API key, so refs are linkable across restarts until that key changes), else a random per-process key. No behaviour change. The README's `OH_TELEMETRY_SALT` row already said this. - Add a unit test that ties the description to `build_telemetry_sink`'s behaviour (refs equal `pseudonymize(id.bytes | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5964) |
| `615759cc3497` / [#5958](https://github.com/OpenHands/software-agent-sdk/pull/5958) | `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`<br>`openhands-agent-server/openhands/agent_server/conversation_router.py`<br>`openhands-agent-server/openhands/agent_server/conversation_service.py`<br>+2 paths (complete list below) | F04, F05, F08, F09, F10, F11, F12, F31, F32 | - `_create_conversation` remembers which `tool_module_qualnames` modules failed to import; once the agent is launched, if the agent uses one of those tools and it is not otherwise resolvable, it raises `UnavailableAgentTool`, which the router maps to `422`. A module whose tool the agent does not use is still only a warning. - Map: `F33.custom-tool-module-unimportable` drops its known-bug marker and now asserts the `4 | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5958) |
| `eac3ee16cc98` / [#5859](https://github.com/OpenHands/software-agent-sdk/pull/5859) | `.agents/skills/verify-agent-server/references/feature-map/F16-workspace-serving-and-vscode.md`<br>`openhands-agent-server/openhands/agent_server/vscode_service.py`<br>`tests/agent_server/test_vscode_service.py` | F16 | - `VSCodeService.get_vscode_url` builds the query with `urlencode(..., quote_via=quote, safe="/")`, so `folder` (and `tkn`) decode back exactly; `/` stays readable, so `folder=/workspace` is unchanged. - Unit tests for the round trip (`&`, `#`, `?`, `%`, `+`, spaces) and the exact encoded form. - Feature map: `F16.vscode-folder-encoding` loses its `# bug` marker and "known bug" label, its prose now states the expecte | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5859) |
| `af05c8b46a8a` / [#5856](https://github.com/OpenHands/software-agent-sdk/pull/5856) | `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`<br>`openhands-agent-server/openhands/agent_server/file_router.py`<br>`openhands-agent-server/openhands/agent_server/models.py`<br>`tests/agent_server/test_file_router.py` | F04, F08, F10, F11, F12, F13, F14 | - `_upload_file` counts the bytes it streams to disk and returns them as `file_size`; both upload routes share it. - `tests/agent_server/test_file_router.py` asserts the size for a small upload, a chunked (>8 KiB) upload, and an overwrite. - The `F14.upload-size` map bullet drops its `# bug` marker and "known bug" label, now also checks `file_size` on the raw REST response, and its prose and Gotchas line describe the | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5856) |
| `501d27323cf3` / [#5853](https://github.com/OpenHands/software-agent-sdk/pull/5853) | `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`<br>`openhands-agent-server/openhands/agent_server/file_router.py`<br>`tests/agent_server/test_file_router.py` | F14 | - Sort by `(name.lower(), name)`: the order stays case-insensitive, and case-only twins now have a stable order. - `next_page_id` is now the exact name of the next entry. A `page_id` that matches no exact name falls back to the old case-insensitive match, so a lowercase cursor from an older server still resumes at its entry. - Two unit tests: paging past `Foo`/`foo`, and resuming from a lowercase cursor. The `F14.sub | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5853) |
| `e98b95654fdf` / [#5819](https://github.com/OpenHands/software-agent-sdk/pull/5819) | `.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md`<br>`openhands-agent-server/openhands/agent_server/conversation_router.py`<br>`tests/agent_server/test_conversation_router.py` | F04, F05, F08, F09, F10, F11, F12 | - `conversation_router.ask_agent`: map the service's `None` (only returned when the conversation is missing; `EventService.ask_agent` returns `str`) to `404 "Conversation not found"`, the same answer `condense` gives. - Router unit test for the unknown-id case. - verify-agent-server map: drop the `F10.ask-agent-unknown` known-bug marker, check the 404 detail, and list 404 among the route's responses. `RemoteConversat | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5819) |
| `7f41284acefd` / [#5811](https://github.com/OpenHands/software-agent-sdk/pull/5811) | `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`<br>`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`<br>`tests/sdk/conversation/test_switch_model.py` | F05, F08, F09, F10 | - `LocalConversation.switch_acp_model` now refuses an empty or whitespace-only model (`ValueError`, same message as `set_acp_model`) before the live/pre-run branch, so nothing is persisted. The agent-server route already maps `ValueError` to 400; the TypeScript client only forwards the request, so no change there. - Unit test for the pre-run blank case (`""` and `" "`) in `tests/sdk/conversation/test_switch_model.py` | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5811) |
| `9d07c6d55d1b` / [#5795](https://github.com/OpenHands/software-agent-sdk/pull/5795) | `.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md`<br>`openhands-agent-server/openhands/agent_server/conversation_router.py`<br>`tests/agent_server/test_conversation_router.py` | F04, F05, F08, F09, F10, F11, F12 | - Declare the limit as `ids: Annotated[list[UUID], Query(max_length=99)]` and drop the assert. 100+ ids now get a 422 ("List should have at most 99 items"), the service is not called, and OpenAPI shows `maxItems: 99`, following the declared `le=100` on search `limit` (https://github.com/OpenHands/software-agent-sdk/issues/5114). - The router test now asserts the 422 (it used to assert the `AssertionError`), the 99-id | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5795) |
| `75abe1eb3f5f` / [#5773](https://github.com/OpenHands/software-agent-sdk/pull/5773) | `.agents/skills/verify-agent-server/references/feature-map/F05-run-pause-interrupt.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F06-conversation-events.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F08-confirmation-security-secrets.md`<br>`.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`<br>+3 paths (complete list below) | F05, F06, F07, F08, F09, F10, F11, F12, F31 | - `EventService._event_matches_filters` (shared by `search_events` and `count_events`) now accepts either the bare class name or the fully-qualified class path. Unknown or partial names still give an empty page / `0`, and `kind` still combines with the other filters. No change to the wire format, the schema or the TypeScript client. - Unit tests in `tests/agent_server/test_event_service.py` cover the short kind for s | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5773) |
| `1fbfd37f6fb2` / [#5783](https://github.com/OpenHands/software-agent-sdk/pull/5783) | `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`<br>`clients/typescript/src/__tests__/conversation-create-options.test.ts`<br>`clients/typescript/src/conversation/base.ts`<br>`clients/typescript/src/conversation/conversation-manager.ts`<br>+1 paths (complete list below) | F34 | - `ConversationManager.createConversation` passes `maxIterations` and `stuckDetection` to `RemoteConversation.start()`. - `RemoteConversation` keeps both constructor options and uses them as fallbacks in `start()`; the `0`/omitted handling of `maxIterations` (`\|\| 500`) is unchanged. Also fixes the stale "default: 50" doc comment on `BaseConversationOptions.maxIterations` (the default is 500). - New `conversation-cr | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5783) |
| `5966c781ed01` / [#5126](https://github.com/OpenHands/software-agent-sdk/pull/5126) | `openhands-agent-server/openhands/agent_server/api.py`<br>`openhands-agent-server/openhands/agent_server/config.py`<br>`openhands-agent-server/openhands/agent_server/conversation_router.py`<br>`openhands-agent-server/openhands/agent_server/conversation_service.py`<br>+12 paths (complete list below) | F01, F02, F03, F04, F05, F06, F08, F09, F10, F11, F12, F13, F27, F29, F30, F31, F32, F33, F34 | - `POST /api/conversations/{conversation_id}/children`, backed by `ConversationService.start_child_conversation`. Like a fork, the child copies the parent's stored configuration (secrets, client tools, plugins, hooks, tags, confirmation and security settings) and its resolved agent, but it starts with an empty history, receives `task` as its first user message and records `parent_conversation_id`. `isolation="worktre | documented | family-sweep-with-known-defects; [feature evidence and limits](intent-runtime-overlay.md#pr-5126) |

## Per-PR evidence

### #5467: fix(llm): stop string-serializing vision-capable DeepSeek v4 models

- Commit: `608a102c637d8d8a999f49d7b04846524bd8bd1c` (2026-10-06T22:17:37-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5467).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5467). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - Narrow the automatic string-serializer entry from the bare `"deepseek"`
>   substring to the legacy `"deepseek-v3.2"` family, so `DeepSeek-V3.2-Exp` /
>   `deepseek-v3.2-reasoner` keep string-only content while the v4, v4.1,
>   `v4-flash-vision-exp`, and `deepseek-chat` ids keep structured (list) content.
> - Add feature-registry regression coverage asserting the v4/v4.1 ids resolve
>   `force_string_serializer is False` and the v3.2 family stays `True`.
> - Add a formatted-payload regression test proving a vision-active
>   `openhands/deepseek-v4.1-flash` keeps one `image_url` part.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/utils/model_features.py`
- `tests/sdk/llm/test_llm.py`
- `tests/sdk/llm/test_model_features.py`
- `tests/sdk/llm/test_vision_support.py`

Linked issue acceptance context:

- [#5360](https://github.com/OpenHands/software-agent-sdk/issues/5360): [Bug]: force_string_serializer` blanket `"deepseek"` substring drops images for vision-capable deepseek-v4.1-flash — current state closed, closed 2026-10-07T02:17:39Z.

> - [ ] A `deepseek`-named model that reports `supports_vision=True` no longer has its image parts dropped (image_url present in the formatted payload). - [ ] `FORCE_STRING_SERIALIZER_MODELS` no longer uses the unqualified `"deepseek"` substring; only the specific legacy ids that genuinely require string-only content match. - [ ] Unit test asserting `get_features("openhands/deepseek-v4.1-flash").force_string_serializer is False` (or that a vision-active model is never string-serialized). - [ ] Existing `DeepSeek-V3.2-Exp` string-serializer behavior (PR #994/#996) is preserved. ### Installation Method `pip install openhands-sdk` ### If you selected "Other", please specify _No response_ ### SDK Version `openhands-sdk==1.49.6` (also present on `main` at the time of filing: `FORCE_STRING_SERIALIZER_MODELS` still contains the bare `"deepseek"` substring). ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version 3.12 ### Model Name (if applicable) `openhands/deepseek-v4.1-flash` (also reproduced reasoning for `deepseek-v4-flash`, `deepseek-v4-pro`). Provider reports `supports_vision=True`. ### Operating System MacOS ### Logs and Error Messages o error is raised — the image is silently removed. Evidence from `LLM.format_messages_for_llm`: ``` force_string_serializer=None -> effective=True images_in_payload=0 # default (bug) force_string_serializer=False -> effective=False images_in_payload=1 # fix force_string_serializer=True -> effective=True images_in_payload=0 ``` Direct provider probe (tool message with list vs. st

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5467#pullrequestreview-5402967894): _This review was posted by an AI agent (OpenHands)._ ## Summary Scope fits this repository. The change is confined to the SDK LLM feature registry (`openhands-sdk/openhands/sdk/llm/utils/model_features.py`), which `AGENTS.md` designates as the home for model/provider capability tweaks, and it implements exactly the minimal narrowing prescribed by the linked issue's triage (replace the bare `"deepseek"` substring with `"deepseek-v3.2"`). ## Verification - **Fix is correct.** On the current head, `openhands/deepseek-v4.1-flash`, `deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-v4-flash-vision-exp`, and `deepseek-chat` all resolve `force_string_serializer=False`, while `DeepSeek-V3.2-Exp` and `deepseek-v3.2-reasoner` remain `True`. With vision active, the formatted v4.1-flash payload keeps its `image_url` part, matching the reported root ca
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5467#pullrequestreview-5436847998): Thank you for the fix!

### #5548: test(agent-server): real Docker scenario for storage reclaim

- Commit: `91ac058a08fd6365286fc8c5628f35a5f41ff300` (2026-10-07T12:51:16+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5548).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-5548). Families: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34.

Stated intent:

> - `tests/agent_server/docker_runtime/test_storage_scenario.py`: five tests against a real Docker daemon and agent-server image. The server is configured through `OH_*` environment variables, as the CLI does, and drives real containers. Each test checks what must survive as well as what must go:
>   1. **Only rebuildable files go.** Stopping a conversation drops `~/.cache` and `~/.npm` only; a pass over the disk budget then sheds `node_modules/`, `.venv/`, `build/` and a nested checkout's `node_modules/`, and keeps shell history, config, tracked files, untracked notes, an ignored `.env`, a clone inside an ignored `vendor/`, and `.git`. The running conversation is unchanged, file for file.
>   2. **Links planted by the sandbox are removed, never followed**: `~/.npm` and a link inside an ignored `build/` pointing at host paths, and `~/.cache` pointing (relatively) into the runtime's own workspace. The links and `build/` go; all targets survive.
>   3. **A caller-supplied workspace is never shed**, even over budget.
>   4. **A reclaimed conversation resumes and works** (reading a workspace file 

Changed paths:

- `.github/workflows/storage-scenario.yml`
- `pyproject.toml`
- `tests/agent_server/docker_runtime/STORAGE_SCENARIO.md`
- `tests/agent_server/docker_runtime/test_storage_scenario.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5548#issuecomment-6015363913)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5548#pullrequestreview-5438953842): _This review was posted by an AI agent (OpenHands)._ Scope: this is a test/CI/docs-only change in the right repository (agent-server storage reclaim), so no ownership or architecture concern. What I checked on head `6435a40`: - New `docker_live` marker is deselected by default (`pyproject.toml` `addopts`), matching the existing `stress`/`acp_live` pattern, and the new `Storage scenario` workflow runs it on the relevant path changes. The `Storage scenario` check passed on this head. - Unit tests for the same module still pass: `uv run pytest tests/agent_server/docker_runtime/` -> 108 passed, 5 deselected. - The one failing check on this head, `Agent Server`, is the `Build & Push (golang-arm64)` job failing on a `wget` of openvscode-server (`exit code: 8`) — an unrelated, pre-existing image-build flake, not caused by this PR. One material f
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5548#pullrequestreview-5441057382): _This review was posted by an AI agent (OpenHands)._ Scope: test/CI/docs-only change in the owning repository (agent-server storage reclaim). No ownership or architecture concern. What I verified on head `1baaae41` (the merge of `main` into the branch): - The PR diff against `main` is exactly 4 files (workflow, `pyproject.toml`, scenario test, scenario doc); the main-branch merge itself is not part of the changed set. - `pyproject.toml` deselects the new `docker_live` marker by default, matching the existing `stress`/`acp_live` pattern; the new `Storage scenario` workflow passed on this head (run 37607965606). - Focal unit tests in the touched package pass: `uv run pytest tests/agent_server/docker_runtime/` -> 108 passed, 5 deselected (the 5 new docker_live tests, as intended). - The `docker_live` tests skip cleanly when Docker/the image 
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5548#discussion_r4204168947): This planted `build` symlink is never selected for shedding, so the test does not verify that a planted ignored-directory link is removed. Evidence (verified in this workspace against the real selector): - `openhands/agent_server/storage/selectors.py` `git_ignored_dirs()` drops any candidate with `not path.is_symlink()`, and the preceding `git ls-files --others --ignored --exclude-standard --directory` does not even list a symlink to an ignored directory. - Running `ignored_dirs()` on a workspace built exactly like this test (`build/` ignored, `build` -> host dir, plus a nested `sub/node_modules`) returns only `['sub/node_modules']` — `build` is absent. Because the final assertion only checks that the link *target* survives (`(outside / name / "precious")`), the test passes whether or not the symlink is removed. It therefore does not cove

### #5538: fix(agent-server): tolerate state saves while zipping a trajectory

- Commit: `69e26889401fe69157fff536e6a69049e6644cb3` (2026-10-07T18:46:12+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5538).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5538). Families: F05, F06, F07, F08, F09, F10, F11, F12, F14, F18, F20, F21, F22, F31.

Stated intent:

> - `_create_zip_from_directory` skips any entry (file or directory) that raises `FileNotFoundError` between listing and reading, and skips every `*.tmp` file before reading it; the file each one replaces is archived as usual. `atomic_write_text` now names its temp file `.<name>.<random>.tmp` (`tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp")`), and the conversation lease already writes `owner_lease.tmp` before renaming it to `owner_lease.json`, so the rule covers both. It lives in `is_temp_file()`, next to the writer in `openhands.sdk.utils.files`, and a test checks it against the temp files the writer actually creates. Other dotfiles, such as `.env.template`, are archived. A side effect: every `atomic_write_text` temp file now ends in `.tmp`, wherever it is written. Nothing in the repo depended on the old name, and the event store still ignores these files because they start with a dot.
> - Each `.json`/`.jsonl` file is read once, and those bytes are both checked for secrets and archived. `redacted_file_bytes(path)` is split in two: `should_redact(path)` is the one place that p

Changed paths:

- `openhands-agent-server/openhands/agent_server/_secret_redaction.py`
- `openhands-agent-server/openhands/agent_server/event_service.py`
- `openhands-agent-server/openhands/agent_server/file_router.py`
- `openhands-sdk/openhands/sdk/utils/files.py`
- `tests/agent_server/test_event_service.py`
- `tests/agent_server/test_file_router.py`
- `tests/sdk/utils/test_files.py`

Linked issue acceptance context:

- [#5537](https://github.com/OpenHands/software-agent-sdk/issues/5537): [Bug]: download-trajectory returns 500 when a state file is replaced while the zip is built — current state closed, closed 2026-10-07T16:46:14Z.

> - [ ] `download-trajectory` returns 200 when a listed file or directory is renamed or removed before it is read. - [ ] Unfinished `atomic_write_text` temp files are not added to the trajectory zip. - [ ] Each archived JSON file holds the bytes that were checked for secrets, even if the file is replaced while the zip is built. - [ ] `meta.json` is saved atomically, so a download never archives a partly written copy (see Evidence). ### Installation Method Source checkout (`uv sync`), Agent Server started with `python -m openhands.agent_server`. CI used the agent-server image built for #5531. ### If you selected "Other", please specify _No response_ ### SDK Version 1.53.0. Found on `main` at `cb7eaba` and checked again on current `main` at `6f8c38d`, where `file_router.py`, `_secret_redaction.py` and `event_service.py` are unchanged. The numbers above and the code sample are from `6f8c38d`. ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version 3.13 (local 3.13.14, CI 3.13.15) ### Model Name (if applicable) _No response_ ### Operating System Linux ### Logs and Error Messages From CI ("TypeScript client integration tests / integration-test", run 37385593191 attempt 1, job 112017951574, on #5531 at `7aff7ee`; the re-run passed). The agent server logged: ``` Unhandled exception on GET /api/file/download-trajectory/83012474-5e40-421c-a7e9-4163fb38e3c7 [error_id=22f9e76ca40a49ebb5f63550626a5f50] Traceback (most recent call last): ... File "/agent-server/.venv/lib/python3.13/site-packages/openhands/agent_server/file_

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5538#issuecomment-6009377263)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5538#pullrequestreview-5439082159): _This review was posted by an AI agent (OpenHands)._ ## Review: fix(agent-server): tolerate state saves while zipping a trajectory Reviewed head `c9f9868d3c53530d431c7fb4f401085613a6a96c` (matches the requested head; base `main`). Scope gate: this is an Agent Server behavior fix with no REST contract change and no `clients/typescript` change, so it belongs in this repository. ### Verification - **CI on the head:** all checks pass except `unresolved-review-threads`, which is the repository's pre-merge hygiene gate. It fails only because the reviewer's open `Final[str]` / `is_temp_file` nit threads from @VascoSch92 have not been resolved yet, not because of a code defect. `agent-server-tests`, `cross-tests`, `integration-test`, `pre-commit`, `REST API (OpenAPI)`, `windows-tests`, and the packaging jobs are all green. - **Focused tests in th
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4191932773): use contextlib.Suppress
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4192145476): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. Done in 6f6176c94: the loop now uses `contextlib.suppress(FileNotFoundError)`. The tests (214) and pre-commit pass. --- _Generated by [Claude Code](https://claude.ai/code)_
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4192159065): The suffix is checked twice: once at file_router.py:179 (REDACTED_SUFFIXES) and again inside redacted_json_bytes (_secret_redaction.py:76). I'd keep only one of these checks, so the rule for which files get redacted lives in one place.
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4192169685): Optional: `save_meta` is async, but `atomic_write_text` now does mkstemp + fsync + replace directly on the event loop (the old `write_text` had no fsync). On a slow disk that blocks every other request while it runs. `_scrub_persisted_credentials` already does the same, so it's not new to this PR, but `await asyncio.to_thread(atomic_write_text, ...)` would be cheap here.
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4192169694): This pattern relies on `mkstemp`'s internal naming and also matches ordinary dotfiles, e.g. `.env.template` or `.cache.metadata` get silently dropped from the zip. Nothing in a conversation dir matches today, so no data is lost now, but it would be more robust to give the temp file an explicit marker (`mkstemp(prefix=f".{path.name}.", suffix=".tmp")`) and skip that instead. Related: `conversation_lease.py:279-281` writes `owner_lease.tmp` in place inside the conversation dir and this regex doesn't skip it, so a download can pick up a stray or truncated `owner_lease.tmp` (no secrets, just noise). A `.tmp` marker would cover it too.
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4198518715): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. Done in abd04204c and a4763c1b7. `should_redact(path)` in `_secret_redaction.py` is now the only check for which files get redacted: `.json` and `.jsonl`, in any letter case. The zip calls it before reading a file. `redacted_json_bytes(data, path)` no longer decides which files get redacted; it looks at the suffix only to choose between JSON lines and JSON. The zip still reads each selected file once and archives those same bytes. A new test checks that `.json` and `.jsonl` files, including upper-case names, are redacted and that other files are archived byte for byte. --- _Generated by [Claude Code](https://claude.ai/code)_
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4198519000): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. You're right that this PR adds an `fsync` on the event loop that the old `write_text` did not have. I looked into moving the write to a thread and left the code as it is for now, because an `await` inside `with self._write_guard():` can hang the server: - With a lease, `_write_guard()` is `ConversationLease.guarded_write`. It takes a `filelock.FileLock` on `.owner_lease.lock`, whose `acquire()` blocks the calling thread with no timeout, and two `FileLock` objects for the same file wait on each other even in one thread. - Awaiting inside the guard lets other coroutines run while it is held. A second `save_meta` (for example, a conversation PATCH racing the auto-title subscriber) or `_scrub_persisted_credentials` (from `activate_credential_binding` or `start`) then
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4198519354): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. Done in 09fb94f74. `atomic_write_text` now creates `.<name>.<random>.tmp` (`mkstemp(prefix=f".{path.name}.", suffix=".tmp")`), and the regex is gone. The zip skips every `*.tmp` file in the conversation folder, not only names with the atomic writer's prefix, so `owner_lease.tmp` is covered too. The check is `is_temp_file()`, next to `atomic_write_text`. I went with the broader rule because every server-side writer in the conversation folder that uses a temp file names it `*.tmp` (`atomic_write_text` and the lease's `owner_lease.tmp`), and the server keeps no finished data in a `.tmp` file. A `.tmp` copy would also skip redaction, since its name does not end in `.json`. The ACP CLI's own files under `acp/` are no longer archived at all since #5551, which this bran
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4198847965): @VascoSch92 I tend to agree with the agent above 😅
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4203993873): can you use FInal[str] here? more in general, I think we do have this line of code like everywhere :-) Perhaps, we could gentralize that but not for this PR
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4203995925): same as before
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4203998752): Do we need this method? Can we not just use `path.suffix == _TEMP_SUFFIX`?
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4204003296): make sense to me
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4206583518): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. Thanks. This already had an open issue, so I added these two call sites and the lease-lock constraint to #5402, which tracks durable writes on the event loop: https://github.com/OpenHands/software-agent-sdk/issues/5402#issuecomment-6037417334. Resolving this thread. --- _Generated by [Claude Code](https://claude.ai/code)_
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4206584056): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. Done in 21ebbf4c4: `_JSON_LINES_SUFFIX: Final[str]`. Agreed that these suffix constants could live in one place; that would be a separate change. --- _Generated by [Claude Code](https://claude.ai/code)_
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4206584451): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. Done in 21ebbf4c4: `_TEMP_SUFFIX: Final[str]`. --- _Generated by [Claude Code](https://claude.ai/code)_
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5538#discussion_r4206584775): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project work. I would keep it. Its only caller outside tests is the agent server's `file_router.py`, which is in another package. The helper lets that code use the SDK's temp-file convention without importing the private `_TEMP_SUFFIX`, and it gives the rule one name and one test (`tests/sdk/utils/test_files.py`). If you would rather inline it, I can make the constant public as `TEMP_SUFFIX` and write `path.suffix == TEMP_SUFFIX` in `file_router.py`. --- _Generated by [Claude Code](https://claude.ai/code)_

### #5547: fix(agent-server): remove a deleted local conversation's worktree, keep its branch

- Commit: `0bb7b525c75f4a9a0606844fc4a2579106b08d32` (2026-10-08T10:37:19Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5547).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5547). Families: F04, F05, F10, F12, F15, F31, F32.

Stated intent:

> - `delete_conversation` now calls `remove_conversation_worktree(conversation_worktree_root, id)` after removing the conversation directory.
> - It removes `conversation_worktree_root/<id>/` and runs `git worktree prune` in each source repository. The `openhands/<id>` branch, with the agent's commits, is kept.
> - git runs with `core.fsmonitor=false` and `--no-optional-locks`, because the repository config is agent-writable. A symlinked conversation directory is left alone.

Changed paths:

- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `openhands-agent-server/openhands/agent_server/local_storage.py`
- `tests/agent_server/test_local_storage.py`

Linked issue acceptance context:

- [#5553](https://github.com/OpenHands/software-agent-sdk/issues/5553): [Bug]: Deleting a local conversation leaves its git worktree behind — current state closed, closed 2026-10-08T10:37:21Z.

> - [ ] Deleting a local conversation removes `conversation_worktree_root/<id>/` and prunes it from `git worktree list`. - [ ] The `openhands/<id>` branch and its commits are kept. - [ ] Other conversations' worktrees and the user's own worktrees of the same repository are untouched. - [ ] Delete still succeeds if the source repository is gone; a link planted in place of the worktree dir is not followed. ### Installation Method From source (agent-server via `uv run`) ### SDK Version 1.53.0 (main) ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version 3.13 ### Model Name (if applicable) N/A ### Operating System MacOS ### Additional Context Fix: #5547. Related but separate: #5307 (worktrees created across host/container namespaces in Docker mode). ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5547#issuecomment-6015103659)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5547#pullrequestreview-5439415911): _This review was posted by an AI agent (OpenHands)._ Reviewed head `f9ec3438db45705a3744e9c171b3180d8c63fc0b` (matches the requested SHA) against base `main`, with the repo `AGENTS.md` and `.agents/skills/custom-codereview-guide.md`. Scope: this belongs in this repository (the Agent Server owns local conversation storage), and #5553 is `ready-for-dev` / `priority:medium`, so the direction is clear. The change addresses the reported bug: `delete_conversation` now removes `conversation_worktree_root/<id>/` and prunes the source repo's git registration, keeping the `openhands/<id>` branch. Verification on this head: CI is green; `tests/agent_server/test_local_storage.py` passes 6/6 in the workspace, and `test_conversation_service.py` + `test_conversation_router.py` (237 tests) still pass. The `_git` invocation is hardened (`--no-optional-loc
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5547#pullrequestreview-5441366331): _This review was posted by an AI agent (OpenHands)._ Reviewed head `031b68e6ff6af05b96bb20db481d4cf645857e7a` against base `main`, with the repo `AGENTS.md` and `.agents/skills/custom-codereview-guide.md`. Scope: this belongs in this repository (the Agent Server owns local conversation storage), and #5553 is `ready-for-dev` / `priority:medium`. The change fixes the reported bug: `delete_conversation` removes the conversation's worktree and prunes the source repo's git registration, keeping the `openhands/<id>` branch. The prior material finding is addressed. Forks persist their source's worktree path (`LocalConversation.fork` passes `workspace=self.workspace`, and `ForkConversationRequest` has no workspace field), so the new `conversation_worktree_dir` + `_conversation_records` ownership check now removes the worktree only when no remaini
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5547#pullrequestreview-5455280265): The current head addresses the shared-worktree regression, includes real server and git coverage, and has no unresolved review threads or material code issues. Approved for merge. This review was submitted by an AI agent (OpenHands) on behalf of Graham Neubig.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5547#discussion_r4204537279): This removes the worktree for `conversation_id` unconditionally, but a forked conversation shares its source's worktree. `ForkConversationRequest` (`models.py`) has no `worktree`/`workspace` field, and `LocalConversation.fork` passes `workspace=self.workspace`, so `fork_conversation` persists the fork's workspace as the source's worktree (`conversation_worktree_root/<source_id>/<repo>`) and never creates a separate one. Reproduced on this head against a real server and real git: forking a `worktree=true` conversation returns the same `working_dir`, and then `DELETE /api/conversations/<source_id>` removes `conversation_worktree_root/<source_id>/`, so the fork's workspace no longer exists while the fork is still readable (`GET` returns 200). Deleting a source conversation that has a live fork now destroys the fork's checkout and any uncommi

### #5342: fix(terminal): preserve scripts across heredoc boundaries

- Commit: `97190b800444aaf090eee29e336a63d80146c7ae` (2026-10-08T09:24:58-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5342).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5342). Families: F13, F27, F33.

Stated intent:

> - Preserve a top-level script boundary after a completed structural `heredoc_end` using the existing shared tree-sitter-bash AST.
> - Keep ordinary newline-separated commands and here-strings split/rejected; do not alter malformed-input fallback, action security analysis, confirmation policy, or the command sent for execution.
> - Add table-driven grammar regressions and real tmux/subprocess terminal-session coverage for multiple heredocs and post-terminator commands.
> 
> ### Design and security invariants
> 
> A terminal tool call remains one `TerminalAction`; this does not enable parallel tool actions. Unix commands may contain one valid Bash script, including commands joined by `;`, `&&`, `||`, `|`, and `&`. Ordinary bare-newline command sequences remain rejected.
> 
> The implementation changes only terminal action-boundary classification. Security analysis and confirmation continue to receive the complete original `TerminalAction`, including every command before, inside, and after each heredoc. The code does not regex-match delimiters or rewrite the command.
> 
> 
> ### REST API contract changes
> 
> Co

Changed paths:

- `openhands-tools/openhands/tools/terminal/terminal/terminal_session.py`
- `openhands-tools/openhands/tools/terminal/utils/command.py`
- `tests/tools/terminal/test_terminal_parsing.py`
- `tests/tools/terminal/test_terminal_session.py`

Linked issue acceptance context:

- [#4957](https://github.com/OpenHands/software-agent-sdk/issues/4957): Make shell more permissive to reduce "combined heredoc" rejections — current state closed, closed 2026-10-08T13:25:01Z.

> - [ ] A single terminal action containing multiple sequential heredocs and commands after their terminators is accepted and executes through the real terminal entry point. - [ ] Quoted heredoc delimiters and `<<-` indentation are classified correctly. - [ ] Existing behavior for pipelines, `&&`, `;`, `\|\|`, command substitutions, and here-strings remains correct. - [ ] Ordinary unchained newline-separated commands remain rejected. - [ ] Security-analysis and confirmation boundaries are unchanged; the complete original command remains the analyzed/executed action. - [ ] Table-driven parser tests and real terminal-session regression tests cover positive and negative cases. - [ ] Authentic evidence demonstrates the same command rejected on base and accepted/executed on the fixed head. 

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5342#issuecomment-5856179987)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5337195894): _This review was posted by an AI agent (OpenHands)._ Reviewed head `96b6991642083720989ad224611d053064e8981d` (`fix/heredoc-command-validation`) against base `d77ada7a0`. **Scope** — Belongs in this repository: the change is confined to `openhands-tools/openhands/tools/terminal` command-boundary classification; no other repo owns terminal-action validation. **What I verified** — I reproduced the reported failure through the real entry point `TerminalSession.execute(TerminalAction(...))` with the subprocess backend. On base the two-heredoc script is rejected with `Cannot execute multiple commands at once`; on this head it returns `is_error=False, exit_code=0` and runs both heredocs. The negative paths are still enforced (ordinary `echo first\necho second` and the here-string `cat <<< text\necho second` are both rejected). Focused tests pas
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5346251531): _This review was posted by an AI agent (OpenHands)._ Reviewed head `df385560dd0892e25d6eb44ef28ccf3e7781da82` (`fix/heredoc-command-validation`) against base `a8a9fc6e2`. **Scope** — Belongs in this repository: the change is confined to `openhands-tools/openhands/tools/terminal/utils/command.py` command-boundary classification, which this repo owns; no other repository owns terminal-action validation. **Previous finding is resolved** — The earlier concern that `_ends_with_heredoc` matched a statement merely *containing* a heredoc is fixed at this head. `_ends_with_heredoc` now also requires the trailing `heredoc_end` to reach the node's end (`last.end_byte == node.end_byte`), and four negative regressions were added to `test_non_heredoc_newlines_still_split`. I re-derived the base splitter and compared it against this head: `if true; then
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5347862394): _This review was posted by an AI agent (OpenHands)._ Reviewed head `df385560dd0892e25d6eb44ef28ccf3e7781da82` against base `a8a9fc6e2`. **Scope** — Belongs in this repository. The change is confined to `openhands-tools/openhands/tools/terminal/utils/command.py` terminal action-boundary classification plus tests; no other repository owns terminal-action validation. **Splitter logic is correct** — I re-derived the base splitter and diffed it against this head over a broad corpus. `_ends_with_heredoc` now requires the trailing `heredoc_end` to reach the node's end (`last.end_byte == node.end_byte`), so compound statements/substitutions that merely contain a heredoc (`if…fi`, `for…done`, `$( … )`, `( … )`) still split, while `cat <<EOF\nx\nEOF\necho after`, pipelines, redirections, `<<-`, `<<'EOF'`, `!`-prefixed, and multiple sequential hered
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5347872310): _This review was posted by an AI agent (OpenHands)._ Reviewed head `df385560dd0892e25d6eb44ef28ccf3e7781da82` (`fix/heredoc-command-validation`) against base `a8a9fc6e2`. **Scope** — Belongs in this repository: the change is confined to `openhands-tools/openhands/tools/terminal/utils/command.py` command-boundary classification, which this repo owns. **Previous finding resolved** — `_ends_with_heredoc` now requires the trailing `heredoc_end` to reach the node's end (`last.end_byte == node.end_byte`), so a statement that merely *contains* a heredoc no longer swallows the following newline. I re-derived the base splitter and compared it against this head: `if true; then\n cat <<EOF\nx\nEOF\nfi\necho after`, `for i in 1 2; do cat <<EOF\nx\nEOF\ndone\necho after`, `x=$(cat <<EOF\nhi\nEOF\n)\nrm -rf /tmp/x`, and `( cat <<EOF\nx\nEOF\n)\necho af
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5353660810): _This review was posted by an AI agent (OpenHands)._ Reviewed head `df385560dd0892e25d6eb44ef28ccf3e7781da82` (`fix/heredoc-command-validation`) against base `a8a9fc6e2`, after @neubig's 2026-09-29 comment. **Scope** — Belongs in this repository: the change is confined to `openhands-tools/openhands/tools/terminal/utils/command.py` command-boundary classification, which this repo owns. **Splitter fix verified** — `_ends_with_heredoc` now requires the trailing `heredoc_end` to reach the node's end, so a statement that merely *contains* a heredoc no longer swallows the next newline. Comparing the base splitter to this head: `if true; then\n cat <<EOF\nx\nEOF\nfi\necho after`, `for i in 1 2; do cat <<EOF\nx\nEOF\ndone\necho after`, `x=$(cat <<EOF\nhi\nEOF\n)\nrm -rf /tmp/x`, and `( cat <<EOF\nx\nEOF\n)\necho after` are split and rejected agai
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5357501061): _This review was posted by an AI agent (OpenHands)._ Reviewed head `1ae1b6193e1140d95f8558aca1ba117a927ce3ee` (`fix/heredoc-command-validation`) against base `a8a9fc6e2`, after @neubig's completion-race fix. **Scope** — Belongs in this repository: the change is confined to `openhands-tools/openhands/tools/terminal/` command-boundary classification and session completion handling, which this repo owns. **Prior material finding resolved.** My earlier review flagged that a command after the final heredoc was not reliably executed before `execute()` returned, and could be reported as `exit_code=0` with its output dropped. Commit `1ae1b61` addresses this: newly accepted post-heredoc scripts are wrapped in one current-shell brace group plus a per-command boundary marker, and completion now also requires that marker to be visible. I re-ran the f
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5367317021): _This review was posted by an AI agent (OpenHands)._ Reviewed head `a38863b26eeff8bf86892a20e6c59acbe3896f97` (`fix/heredoc-command-validation`) against base `a8a9fc6e236a`. This is a re-review because the head advanced from the previously approved `1ae1b6193`. **Scope** — In-repo: the change is confined to terminal command-boundary classification and the terminal session in `openhands-tools`, which this repository owns. **Head advance is a no-op for code** — `a38863b` only adds the automated `chore: Remove PR-only artifacts` commit; `command.py`, `terminal_session.py`, `test_terminal_parsing.py`, and `test_terminal_session.py` are byte-identical to `1ae1b61`. The prior approval therefore still applies to the code under review. **Verified on this head** — - `tests/tools/terminal/test_terminal_parsing.py`: 47 passed. - `tests/tools/termina
- [reviews; COMMENTED; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5367949951): Verified the heredoc grouping on the PR head (a38863b) on macOS and in a Linux container (bash 5.2.37, tmux 3.5a). Splitting/grouping and exit-status propagation look correct, but the internal wrapper/marker leaks into agent-visible output in two ways (inline comments): \| Case \| Linux \| macOS bash 3.2 \| \|---\|---\|---\| \| multi-heredoc script (PR test) \| ok \| leaks \| \| 2nd grouped command in a session \| ok \| leaks \| \| `<<-` with tab-indented body \| **leaks** \| leaks \| \| no-change timeout, then poll `""` \| **leaks** \| leaks \| Same result on subprocess and tmux backends. It would be worth adding tests for the `<<-` and timeout+poll cases, since the current suite passes on Linux while both of those leak.
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5402840579): _This review was posted by an AI agent (OpenHands)._ Reviewed head `ce199ea49250cf0a1044baf2b4eb08426f957b16` (commit `84c3b1040` "strip grouped-heredoc wrapper beyond the completed path") against base `1b2b5c850b6a99d8b742bfe20d198d6167165ca2`. **Scope** — Belongs in this repository: changes are confined to `openhands-tools/openhands/tools/terminal/` command-boundary classification and session output handling, which this repo owns. **What I verified as fixed.** The two leaks @VascoSch92 reported are addressed in `84c3b1040`, and I reproduced their cases green on both backends on this host: - `<<-` tab-indented heredoc echo (`cat <<-EOF\n\tindented\n\tEOF\nprintf done`) returns `indented\ndone` with no `__OH_COMMAND_FINISHED_` / `__openhands_status` / wrapper echo — the line-normalized fallback in `_remove_command_prefix` catches the rewr
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5403517996): _This review was posted by an AI agent (OpenHands)._ Reviewed head `bc0263a13dbd7c6769d546744de6342f0ab334a5` ("Fix heredoc wrapper leak when rejecting a running-command follow-up") against base `30e90260c9`. **Scope** — Belongs in this repository: the change is confined to `openhands-tools/openhands/tools/terminal/` command-boundary classification and terminal-session output handling, which this repo owns. It stays within one `TerminalAction` and does not alter security analysis, confirmation policy, or the command sent to the shell. **Prior finding resolved.** My previous review flagged that the "previous command still running, then send a new command" early-return path returned the raw screen without stripping the tracked wrapper. `bc0263a1` moves the wrapper/marker setup below that guard and calls `_strip_boundary_wrapper(last_termina
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5428063182): _This review was posted by an AI agent (OpenHands)._ Reviewed head `c73ed81c64b870cdba8196024279ea7b6da72801` (`fix/heredoc-command-validation`) against base `30e90260c914`. This is a fresh review of the reworked follow-up (brace grouping removed, `/dev/tty` completion record, `bash -n` syntax check). **Scope** — In-repo: terminal command-boundary classification and the terminal session in `openhands-tools`, which this repository owns. **What works on this head** — On Linux I ran the focused suites: `tests/tools/terminal/test_terminal_parsing.py` 51 passed, and `tests/tools/terminal/test_terminal_session.py -k "heredoc or multiline or multiple or boundary or alias or redirect"` 29 passed (both backends). The core fix is intact: the reported shape (two chained heredocs plus a trailing command) is accepted and executes to completion, the sp
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#pullrequestreview-5436793771): _This review was posted by an AI agent (OpenHands)._ Reviewed head `bada904359ac8ac69d4c4bd4fd5de80e70d5d1e9` (`fix/heredoc-command-validation`) against base `30e90260c914`. Fresh review of the reworked guard. **Scope** — In-repo: terminal command-boundary classification and the terminal session in `openhands-tools`, which this repository owns. **Prior blocking finding is resolved** — The previous review flagged that `needs_heredoc_completion_boundary()` swallowed `FileNotFoundError` from its `bash -n` shell-out, so on Windows the parse-error heredoc forms (`cat <<A <<B`, `cat <<E"OF"`, a backgrounded heredoc) reported no boundary was needed, and `windows-tests` was red on this PR's own assertions. The author removed the `bash` dependency entirely (`511268f`) and now decides heredoc closure from tree-sitter nodes via `_last_heredoc_is_clo
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4121047099): `_ends_with_heredoc` returns True whenever the last *named* descendant is a `heredoc_end`, without checking that the `heredoc_end` actually reaches the statement's end. tree-sitter keeps closing tokens such as `fi`, `done`, and `)` unnamed, so a compound statement or substitution that merely *contains* a heredoc satisfies the predicate. The exemption then swallows a following bare-newline command. Verified on this head via `TerminalSession.execute(TerminalAction(...))` (subprocess backend), compared against the base splitter: ``` if true; then\n cat <<EOF\nx\nEOF\nfi\necho after base -> ['if true; ... fi', 'echo after'] (rejected) head -> one element, is_error=False, exit_code=0 x=$(cat <<EOF\nhi\nEOF\n)\nrm -rf /tmp/x base -> ['x=$(cat <<EOF...)\\n', 'rm -rf /tmp/x'] (rejected) head -> one element (executes) ( cat <<EOF\nx\nEOF\n)\necho 
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4125181541): Fixed in df385560d. `_ends_with_heredoc` now also requires the trailing `heredoc_end` to reach the node's end (`last.end_byte == node.end_byte`), so a `heredoc_end` that is followed by an unnamed closing token no longer satisfies the predicate. Verified end-to-end through `TerminalSession.execute(TerminalAction(...))` on the subprocess backend: all three reported shapes are split and rejected again (`if true; then...fi \| echo after`, `x=$(cat <<EOF...)\n \| rm -rf /tmp/x`, `( cat <<EOF... )\n \| echo after`), while `cat <<EOF\nx\nEOF\necho after` still stays a single element. Added those four shapes as negative regressions in `test_non_heredoc_newlines_still_split`.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4129763567): This assertion is flaky on the current head, which points at a real behavior gap rather than a test-only problem. Running the real entry point `TerminalSession.execute(TerminalAction(command=command))` on `df385560d`, `'done'` is missing from `obs.text` in roughly 1 in 10 runs (2/20 direct session runs; 3/15 pytest runs across `subprocess` and `tmux`), while `obs.metadata.exit_code` is still reported as `0`. The PR body's `4 passed` does not hold on this head. Direct marker probe (subprocess): `touch A; python - <<'PY'\nprint('one')\nPY\ntouch B; python - <<'PY'\nprint('two')\nPY\ntouch C\n` returns `exit_code=0` while `C` does not yet exist; `C` appears only ~1.5–2 s later. So the session declares the action complete and successful *before* the trailing command has executed, and that command's output is usually lost from the observation.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4129772296): This new test is flaky. `assert "done" in obs.text` depends on the terminal backend capturing the final output segment, which it sometimes does not. On this head the command itself always succeeds (`exit_code == 0`), but `obs.text` intermittently stops right after the second heredoc, so the assertion fails on output that was simply not captured. Reproduced on the current checkout: - `uv run pytest 'tests/tools/terminal/test_terminal_session.py::test_chained_script_with_multiple_heredocs[subprocess]'` -q -> 2 failures in 15 runs (~13%). Failure text: `assert 'done' in "printf start; python - <<'PY'\nprint('first')\nPY\nstart\nfirst\n\n\nprintf middle; python - <<'PY'\nprint('second')\nPY\nmiddle"` with `exit_code=0`. - The tmux variant fails the same way, and CI's forked mode reproduces it: `CI=true uv run python -m pytest -q --forked <tes
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4134330981): Correction to my earlier note on this same line (review 5347872310): this is not test-only flakiness. The command's trailing statement is sometimes executed *after* `execute()` already returned success, so the failure is in completion/output handling. Decisive repro on this head (subprocess backend), where the trailing statement appends to a file so execution is distinguishable from display: ```python CMD = "printf start; python - <<'PY'\nprint('first')\nPY\nprintf middle; python - <<'PY'\nprint('second')\nPY\nprintf done; echo x >> log.txt\n" obs = session.execute(TerminalAction(command=CMD)) ``` In the failure run: `obs.metadata.exit_code == 0`, `'done' not in obs.text`, `log.txt` is **absent at return**, and 3 s later it contains `x` — the trailing `echo x >> log.txt` ran only after the action was already reported successful. Observed 
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4137340583): Addressed in 1ae1b61. Newly accepted scripts with a statement buffered after a heredoc terminator are submitted as one brace-grouped shell unit, and a non-echoed per-command boundary marker prevents both stale and intermediate PS1 metadata from completing the action early. The wrapper preserves the trailing statement's exact exit status and its current-shell effects. Regression tests now use a deliberately slow trailing statement plus an immediate marker-file assertion, cover both tmux and subprocess, verify non-zero status propagation, and hide the internal marker. Focused parsing/session tests pass (51), as do 5 repeated two-backend runs, Ruff, and diff checks. _This reply was created by an AI agent (OpenHands) on behalf of Graham Neubig._
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4137341062): Addressed in 1ae1b61. Newly accepted scripts with a statement buffered after a heredoc terminator are submitted as one brace-grouped shell unit, and a non-echoed per-command boundary marker prevents both stale and intermediate PS1 metadata from completing the action early. The wrapper preserves the trailing statement's exact exit status and its current-shell effects. Regression tests now use a deliberately slow trailing statement plus an immediate marker-file assertion, cover both tmux and subprocess, verify non-zero status propagation, and hide the internal marker. Focused parsing/session tests pass (51), as do 5 repeated two-backend runs, Ruff, and diff checks. _This reply was created by an AI agent (OpenHands) on behalf of Graham Neubig._
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4137341541): Addressed in 1ae1b61. Newly accepted scripts with a statement buffered after a heredoc terminator are submitted as one brace-grouped shell unit, and a non-echoed per-command boundary marker prevents both stale and intermediate PS1 metadata from completing the action early. The wrapper preserves the trailing statement's exact exit status and its current-shell effects. Regression tests now use a deliberately slow trailing statement plus an immediate marker-file assertion, cover both tmux and subprocess, verify non-zero status propagation, and hide the internal marker. Focused parsing/session tests pass (51), as do 5 repeated two-backend runs, Ruff, and diff checks. _This reply was created by an AI agent (OpenHands) on behalf of Graham Neubig._
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4145997994): The echo of this wrapper is removed with an exact `removeprefix(command_to_send)`. When the terminal echo differs even slightly from the text sent, the whole wrapper and both halves of the marker end up in the observation. Reproducible on Linux (bash 5.2, tmux 3.5a), both backends, with a `<<-` script: ```python session.execute(TerminalAction(command="cat <<-EOF\n\tindented\n\tEOF\nprintf done")) ``` Output the agent sees: ``` { cat <<-EOF indented EOF printf done }; (__openhands_status=$?; printf '\n%s%s\n' '__OH_COMMAND_FINISHED_5701db' 'fee2434a4eb65fbbd4e757632b__'; exit "$__openhands_status") indented done ``` The tabs are consumed by readline completion, so the echo no longer matches. On macOS `/bin/bash` 3.2 it's worse: the echo gets a space mid-marker (e.g. `'dc8 bd347…'`), so *every* grouped command leaks. `test_chained_script_wi
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4145998004): The marker is only stripped in the completed branch of the same `execute()` call. `_handle_nochange_timeout_command` / `_handle_hard_timeout_command` get the raw screen and the original command, and a follow-up poll starts with `command_boundary_marker = None`, so nothing is stripped there either. Repro (Linux, both backends), `no_change_timeout_seconds=2`: ```python session.execute(TerminalAction(command="cat <<'EOF'\nbody\nEOF\nsleep 5; echo done")) session.execute(TerminalAction(command="", is_input=False, timeout=10)) ``` The first call returns the full echoed `{ … }; (__openhands_status=… )` wrapper (on the subprocess backend also raw `\x1b[?2004l`/`\x1b[?2004h` codes). The poll returns: ``` done __OH_COMMAND_FINISHED_b93cae8fd0314f24b44fdb4b3cb7c6da__ ``` So any heredoc script that runs longer than the no-change timeout exposes the 
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4174872546): Fixed in 84c3b1040. `_remove_command_prefix` now falls back to a line-by-line comparison with whitespace and control characters normalized when the exact prefix does not match, so a rewritten echo (readline dropping tabs on tmux, BEL-substituted tabs on the subprocess backend) is still recognized and stripped. I did not switch to a start marker: the completion sentinel is already printed by the command itself, so bounding the cut on our own end marker keeps the change small and avoids a second marker on the wire. Added `test_indented_heredoc_echo_is_stripped` as a regression test, and the three macOS-failing cases you named (`test_chained_script_with_multiple_heredocs[subprocess\|tmux]`, `test_chained_heredoc_script_preserves_trailing_exit_status[subprocess]`) pass here.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4174872616): Fixed in 84c3b1040, exactly as you suggested: the marker and the echoed wrapper now live on the session (`_boundary_marker` / `_wrapper_echo`) instead of the `execute()` call. Both timeout handlers strip the wrapper before parsing, a follow-up poll that completes falls back to the tracked wrapper, and a new command clears it. Reproduced your scenario on both backends before the change (marker and wrapper leaked) and after it the first call returns just `body`. Added `test_no_change_timeout_strips_heredoc_wrapper`, which also asserts the follow-up poll stays clean.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4174947144): Stale-wrapper cleanup is incomplete here for the "new command while the previous one is still running" path. `_boundary_marker` is set only when a command is actually wrapped (lines 552-555), and unconditionally cleared here (lines 537-540). But the still-running guard below (the `self.prev_status in {HARD_TIMEOUT, NO_CHANGE_TIMEOUT}` branch, ~lines 585-624) returns an observation built from `last_terminal_output` **before** any stripping, then this clear has already discarded the tracking. So the previous grouped-heredoc script's wrapper echo and both marker halves leak into the agent-visible text. Deterministic repro on the `subprocess` backend: ```python session = create_terminal_session(work_dir=d, terminal_type="subprocess", no_change_timeout_seconds=2) session.execute(TerminalAction(command="cat <<'EOF'\nbody\nEOF\nsleep 8; echo don
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4175144024): Addressed in `84c3b1040`. The still-running branch now strips before combining: ```python last_terminal_output = self._strip_boundary_wrapper(last_terminal_output) ``` so both the tracked wrapper echo and both marker halves are removed on the "previous command still running" path too, not just the completed path. Verified against your scenarios — with the branch code, the follow-up `echo second` observation reports `is_error=True` and contains neither `__openhands_status` nor `__OH_COMMAND_FINISHED_`, on both the subprocess and tmux backends. Regression tests for the timeout and follow-up-poll paths are included. _This comment was created by an AI agent (OpenHands) on behalf of @neubig._
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4195030416): This platform-dependent branch makes `needs_heredoc_completion_boundary()` return `False` whenever the `bash` executable is unavailable, because the `FileNotFoundError` raised by `subprocess.run` is swallowed. On Windows, where `bash` is not on `PATH`, every parse-error heredoc form therefore reports no boundary is needed — the exact gap this function was added to close. The current head fails CI because of it: `windows-tests` is the only non-green check (job 111987076533), and its three failures are this PR's own new cases in `tests/tools/terminal/test_terminal_parsing.py::test_heredoc_scripts_need_completion_boundary`: ```text FAILED ...test_heredoc_scripts_need_completion_boundary[cat <<A <<B\na\nA\nb\nB\necho done] - AssertionError: assert False FAILED ...test_heredoc_scripts_need_completion_boundary[cat <<E"OF"\nbody\nEOF\necho done]
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4202147634): Fixed in b28a50e. The `FileNotFoundError` path no longer returns `False`; when `bash` is unavailable it now derives the completion boundary from the tree-sitter nodes (`_last_heredoc_is_closed`), recognizing the final heredoc body's last non-blank line as the terminator. Added `test_completion_boundary_falls_back_without_bash` and verified the full file passes with `subprocess.run` forced to raise `FileNotFoundError` (52 passed), so `windows-tests` should no longer be a no-op there.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#discussion_r4202214969): Correction after re-checking CI: b28a50e was not enough. The Windows runner ships Git Bash on PATH, so `subprocess.run` did not raise `FileNotFoundError` and the guard still returned `False` for the three forms. Fixed properly in 511268f by dropping the `bash` invocation entirely and deciding heredoc closure from the tree-sitter nodes (`_last_heredoc_is_closed`), matching your platform-independent suggestion. `tests/tools/terminal/test_terminal_parsing.py` now passes with the assertion in the test body (52 passed), plus the heredoc session/chunked-send suites (15 passed) on Linux.
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#issuecomment-5896931253): ## Completion-race fix pushed (`1ae1b61`) - Wraps newly accepted post-heredoc scripts in one current-shell brace group, preventing intermediate prompts between buffered statements. - Adds a non-echoed, per-command boundary marker so an old PS1 cannot make `execute()` return before the grouped unit reaches its trailing statement. - Preserves the trailing statement's exact exit status and strips the wrapper/marker from the user observation. - Makes the regression deterministic with a slow trailing command and immediate side-effect assertion across tmux and subprocess; also covers non-zero status propagation. Verification: 51 focused tests passed; 5 repeated two-backend runs passed; Ruff and `git diff --check` passed. A broader terminal run reached 112 passes but had eight unrelated tmux lifecycle failures under the heavily concurrent local 
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#issuecomment-5901533201): ## Non-blocking output observation The live heredoc matrix confirmed the fix, but the `<<-` indented-terminator case still exposed part of the generated boundary sentinel plus the appended status snippet in the echoed command text on this host. The full 32-hex marker was absent, exit status was correct, and execution did not return early, so this appears cosmetic rather than a correctness failure. Also, tmux completion timing was flaky for both heredoc cases and the no-heredoc control in this environment; the strong confirmation comes from the subprocess backend (40/40 correct statuses, zero premature returns). _This comment was created by an AI agent (OpenHands) on behalf of Graham Neubig._
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5342#issuecomment-6002815966): Follow-up after broader heredoc probing: I found two material edge cases not covered by the prior approval. 1. Valid Bash forms that tree-sitter-bash 0.25 reports as parse errors—multiple heredoc redirections (`cat <<A <<B`), mixed quoted delimiters (`<<E\"OF\"`), and a backgrounded heredoc—take the permissive parse-error fallback but skip `group_heredoc_script_for_execution()`. Through the real subprocess `TerminalSession`, each can return `COMPLETED`, exit 0 before a delayed trailing `touch marker` has run. 2. The stdout completion marker is redirected by a trailing `exec > output.txt`: the action falsely hard-times out and the internal marker lands in the user’s file. Brace grouping also changes alias-expansion timing for aliases defined before a post-heredoc command. I am updating the branch with the smallest practical fix: decouple c

### #5460: fix(llm): stop dropping images when string serializer is forced

- Commit: `9f47d471ee6f91ba1d140d10d34f3b26d5ac2427` (2026-10-08T09:25:27-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5460).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5460). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - Keep the list format for messages that actually carry an image when vision is active, even if string serialization is forced.
> - Text-only messages still use the string form that forced providers require, so the reason those entries exist is preserved.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/message.py`
- `tests/sdk/llm/test_message.py`

Linked issue acceptance context:

- [#5360](https://github.com/OpenHands/software-agent-sdk/issues/5360): [Bug]: force_string_serializer` blanket `"deepseek"` substring drops images for vision-capable deepseek-v4.1-flash — current state closed, closed 2026-10-07T02:17:39Z.

> - [ ] A `deepseek`-named model that reports `supports_vision=True` no longer has its image parts dropped (image_url present in the formatted payload). - [ ] `FORCE_STRING_SERIALIZER_MODELS` no longer uses the unqualified `"deepseek"` substring; only the specific legacy ids that genuinely require string-only content match. - [ ] Unit test asserting `get_features("openhands/deepseek-v4.1-flash").force_string_serializer is False` (or that a vision-active model is never string-serialized). - [ ] Existing `DeepSeek-V3.2-Exp` string-serializer behavior (PR #994/#996) is preserved. ### Installation Method `pip install openhands-sdk` ### If you selected "Other", please specify _No response_ ### SDK Version `openhands-sdk==1.49.6` (also present on `main` at the time of filing: `FORCE_STRING_SERIALIZER_MODELS` still contains the bare `"deepseek"` substring). ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version 3.12 ### Model Name (if applicable) `openhands/deepseek-v4.1-flash` (also reproduced reasoning for `deepseek-v4-flash`, `deepseek-v4-pro`). Provider reports `supports_vision=True`. ### Operating System MacOS ### Logs and Error Messages o error is raised — the image is silently removed. Evidence from `LLM.format_messages_for_llm`: ``` force_string_serializer=None -> effective=True images_in_payload=0 # default (bug) force_string_serializer=False -> effective=False images_in_payload=1 # fix force_string_serializer=True -> effective=True images_in_payload=0 ``` Direct provider probe (tool message with list vs. st

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5460#pullrequestreview-5402641939): _This review was posted by an AI agent (OpenHands)._ ## Assessment The code change is minimal and behaves as described. `preserve_images = vision_enabled and self.contains_image` adds one branch that keeps list serialization for image-bearing messages when vision is active, even when `force_string_serializer` is set; text-only and vision-disabled messages keep the previous string form. I traced the branch against base behavior in `openhands-sdk/openhands/sdk/llm/message.py` and found no other behavioral change: when `vision_enabled` is `False` or the message carries no `ImageContent`, the new `elif` is not taken, so those paths serialize exactly as before. The tool-role truncation path in `_list_serializer` still applies `_maybe_truncate_tool_text`, so the string serializer's tool-text truncation is preserved. The follow-up about the bare
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5460#pullrequestreview-5402783738): _This review was posted by an AI agent (OpenHands)._ ## Assessment Re-reviewed at head `381ce31653b8b2c02d8aaa16ea0cfc9545b02bf0` (a merge of `main`; the fix itself is unchanged from the earlier revision). The change is minimal and matches the reported root cause. In `openhands-sdk/openhands/sdk/llm/message.py`, `preserve_images = vision_enabled and self.contains_image` adds one branch that keeps list serialization for image-bearing messages when vision is active, even when `force_string_serializer` is set. I traced the branch against base behavior: - `vision_enabled=False` or no `ImageContent` → the new `elif` is not taken, so serialization is byte-for-byte the same as before; text-only messages still get the string form forced providers require. - The `role="tool"` truncation path in `_list_serializer` still applies `_maybe_truncate_too

### #5582: fix(container): refresh agent server security dependencies

- Commit: `5c6e2589656aeeacedf6a0bd79a725688bb9e872` (2026-10-08T10:44:41-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5582).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-5582). Families: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34.

Stated intent:

> - advance the observed Debian snapshot from 2026-09-20 to 2026-10-04
> - require and lock urllib3 2.8.0 for the available security fixes
> - remove OpenVSCode from the python-slim image while retaining browser and Docker capabilities
> - explicitly report VS Code disabled at runtime in python-slim

Changed paths:

- `.github/workflows/server.yml`
- `openhands-agent-server/openhands/agent_server/docker/Dockerfile`
- `pyproject.toml`
- `tests/cross/test_agent_server_build_metadata.py`
- `uv.lock`

Linked issue acceptance context:

- [#5606](https://github.com/OpenHands/software-agent-sdk/issues/5606): Harden published Agent Server image dependencies and slim capabilities — current state closed, closed 2026-10-08T14:44:43Z.

> - [ ] The `ARG DEBIAN_SNAPSHOT` pin in `openhands-agent-server/openhands/agent_server/docker/Dockerfile` is advanced from `20260920T000000Z` to a newer snapshot that has completed the repository's three-day observation period, and re-running `.github/scripts/update_debian_snapshot.py` for the build date selects no later eligible snapshot. - [ ] The workspace constraint in root `pyproject.toml` and the resolved version in `uv.lock` both require `urllib3>=2.8.0`; `uv lock --check` passes and a frozen sync resolves `urllib3 2.8.0`. - [ ] The `python-slim` published image omits the OpenVSCode payload (no `/openhands/.openvscode-server` server) while still providing the browser (Chromium) and Docker Engine capabilities. - [ ] The `python-slim` published image reports VS Code disabled at runtime: `GET /vscode/status` returns `{"running": false, "enabled": false}`, and `GET /vscode/url` returns HTTP 503 with the "VSCode is disabled in configuration" detail. - [ ] The `python` (full) image still ships OpenVSCode plus browser/Docker, and `python-minimal` still ships no capabilities and no ACP providers; the `server.yml` matrix records these per-variant capability sets explicitly. - [ ] Regression coverage in `tests/cross/test_agent_server_build_metadata.py` asserts the `python-slim` matrix entries exclude `vscode` and set the runtime VS Code-disabled flag, and that test passes under `uv run pytest`. - [ ] Rescanning the rebuilt `python-slim` (and `python`) images with Trivy shows the previously fixable High findings from the stale Debian packages and `urllib3 2.7.0` are gone, with b

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5582#issuecomment-6049129719)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5582#pullrequestreview-5453926144): _This review was posted by an AI agent (OpenHands)._ ## Scope Security/dependency refresh of the agent-server image. Within this repository's scope (agent-server Docker packaging + root uv workspace constraints). ## What I verified on head `4190c1d` - `uv lock --check` passes and `uv.lock` agrees with the new `pyproject.toml` constraint (`urllib3>=2.8.0`); the lock pins `urllib3 2.8.0` from PyPI. - `uv run pytest -q tests/cross/test_agent_server_build_metadata.py` -> 13 passed. The snapshot regex guard still matches `20261004T000000Z`. - The advanced snapshot is reachable: `snapshot.debian.org/archive/debian/20261004T000000Z/` and `.../debian-security/...` both return 200, and the four-day advance exceeds the three-day observation period enforced by `.github/scripts/update_debian_snapshot.py`. - `urllib3 2.8.0` was published `2026-09-15T1
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5582#pullrequestreview-5455636479): _This review was posted by an AI agent (OpenHands)._ ## Scope Dependency/Debian-snapshot refresh plus removal of OpenVSCode from the python-slim image. This is inside this repository's ownership (agent-server Docker packaging and the root uv workspace constraints). ## Verified on head `b7cdf7ba` - `uv lock --check` passes; `uv.lock` pins `urllib3 2.8.0` and agrees with `urllib3>=2.8.0` in `pyproject.toml`. - `uv run pytest -q tests/cross/test_agent_server_build_metadata.py` -> 13 passed. - The advanced snapshot `20261004T000000Z` is reachable (302 from both debian and debian-security), and `urllib3 2.8.0` was published `2026-09-15T19:29:34Z`, so the seven-day `exclude-newer` guardrail is satisfied. - 29 of 30 current-head checks pass; the remaining one is the blocker below. - The python-slim wiring is internally consistent: it passes `ins
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5582#pullrequestreview-5458196881): _This review was posted by an AI agent (OpenHands)._ ## Scope Dependency/Debian-snapshot refresh plus removal of OpenVSCode from the `python-slim` image. Inside this repository's ownership (agent-server Docker packaging, root uv workspace constraints, build-metadata regression tests). ## Re-review context This updates my earlier review on the same head `b7cdf7ba`. The only blocker then was the failing PR-description check; @neubig posted a new comment asking for reconsideration after linking the issue, so I re-verified the current state rather than re-asserting the old verdict. ## Verified on head `b7cdf7ba2599221410e7249fcbf473c105a9b2e0` - The PR now says `Fixes #5606`, and #5606 is open with `ready-for-dev` (+ `priority:low`). Running `.github/scripts/check_pr_description.py` against the current body returns `PR description validation 
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5582#issuecomment-6049299800): ## Trivy image scan Scanned the published amd64 PR images by immutable manifest digest with Trivy 0.74.0, excluding only the verified OpenVSCode Handlebars extension-manifest name collision. \| Variant \| Main fixable Critical/High \| PR fixable Critical/High \| \|---\|---:\|---:\| \| python \| 175 \| 33 \| \| python-slim \| 172 \| 30 \| \| python-minimal \| 9 \| 7 \| The snapshot refresh removes the large Debian `linux-libc-dev`, Chromium, and OpenSSL fixable clusters. Trivy still reports `urllib3` 2.7.0 in the PyInstaller payload even though the source lock now selects 2.8.0, so the binary bundling path needs separate follow-up investigation. OpenVSCode v1.109.5 is already the latest upstream release. _This scan report was generated by an AI agent (OpenHands) on behalf of the user._
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5582#issuecomment-6061975697): The issue-link blocker from the latest review has been resolved: the PR now links ready-for-dev issue #5606, and the fresh PR Description Check passed: https://github.com/OpenHands/software-agent-sdk/actions/runs/37777108606. The code head is unchanged; please re-review the current PR state and reconsider approval now that this metadata blocker is resolved. _This comment was posted by an AI agent (OpenHands) on behalf of the user._

### #5341: feat(agent-server): expose requested_ref on installed canvas extensions

- Commit: `c980344bc78e21d5625ec4e987680d87c6c56f7f` (2026-10-08T17:21:44+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5341).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5341). Families: F28.

Stated intent:

> - Add optional `requested_ref` to `InstalledCanvasExtensionResponse` and populate it in `from_info`, so install / list / get all return it.
> - Router test installing `github:org/repo` at `ref="v1"` (fetch patched) asserts `requested_ref` on the install and get responses.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `d77ada7a030b` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -1718,0 +1719 @@
> +schema InstalledCanvasExtensionResponse property requested_ref optional schema=anyOf=[type="string",type="null"]
> ```

Changed paths:

- `openhands-agent-server/openhands/agent_server/canvas_extensions_router.py`
- `tests/agent_server/test_canvas_extensions_router.py`

Linked issue acceptance context:

- [#5340](https://github.com/OpenHands/software-agent-sdk/issues/5340): [Feature]: Expose requested_ref on installed canvas extension responses — current state closed, closed 2026-10-08T15:21:49Z.

> - [ ] `InstalledCanvasExtensionResponse` has an optional `requested_ref: str \| None` field. - [ ] `from_info` populates it from the install record. - [ ] A router test covers an install with an explicit `ref`. - [ ] No REST API breaking change (additive, optional field). ### Priority / Severity Medium - Would improve experience ### Estimated Scope Small - API addition, config option, or minor change ### Feature Area - [x] Agent Server ### Additional Context Related: #4349 (field on the record), #4363 (`update()` drops pinned refs for Plugins/Skills). ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5341#issuecomment-5855777665)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5341#pullrequestreview-5332332948): _This review was posted by an AI agent (OpenHands)._ ## Review summary Additive, optional `requested_ref` on `InstalledCanvasExtensionResponse`, populated in `from_info` from `InstallationInfo.requested_ref` (the install record field added in #4349). What I verified on head `c202c26bebb145404e1d702cef9d25b32b0aaf63`: - All three REST paths that return the model route through `from_info` — `install` (line 180), `list` (line 228), and `get` (line 251) — so the field is exposed consistently, matching the issue's desired behavior. - `InstallationInfo.requested_ref` carries the `ref` passed to `manager.install(...)` and is preserved across `check_/apply_canvas_extension_update`, so the value round-trips from the stored install record rather than being reconstructed. - Scope stays inside this repository: the field is exposed on the canonical Ag

### #4784: fix(file_editor): prefer valid UTF-8 encoding

- Commit: `16662b0bd2c1a93d3c5e282b48da05b95e90a0c8` (2026-10-08T11:42:23-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/4784).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-4784). Families: F14, F27, F33.

Stated intent:

> - Return `utf-8` immediately when the sampled bytes decode strictly as UTF-8.
> - Preserve `utf-8-sig` handling for files with a UTF-8 BOM.
> - Keep `charset-normalizer` as the fallback for non-UTF-8 content.
> - In `_recover_encoding`, try UTF-8 before charset-normalizer's guess, so `get_encoding` (the path the editor uses) cannot switch a valid UTF-8 file back to cp1251, cp1252 or latin-1.

Changed paths:

- `openhands-tools/openhands/tools/file_editor/utils/encoding.py`
- `tests/tools/file_editor/utils/test_encoding.py`

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4784#pullrequestreview-5331072133): _This review was posted by an AI agent (OpenHands)._ ## Review: `fix(file_editor): prefer valid UTF-8 encoding` **Scope** — In-repo and correctly placed. The change lives in `openhands-tools/openhands/tools/file_editor/utils/encoding.py` plus a focused test; nothing here belongs in another repository. **Change assessment** — The fast path is well-formed: a UTF-8 BOM check (`codecs.BOM_UTF8` -> `utf-8-sig`), then a strict `raw_data.decode("utf-8")` sample decode returning `utf-8` on success, then the existing `charset_normalizer` fallback when the sample is not valid UTF-8. This makes the common case (UTF-8 source/JSON/Markdown/config) deterministic and prevents a high-confidence heuristic misclassification from overriding a valid UTF-8 interpretation, which is the reported charset-normalizer 3.5.0 CJK JSON issue. **Verification I ran** - 
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4784#issuecomment-5476705857): 🚦 **CI is currently failing on this PR's latest commit.** Please fix the failing checks before OpenHands reviews it - this is re-checked automatically once you push a new commit. (A maintainer can also request `@all-hands-bot` as a reviewer to have it reviewed regardless of CI status.) _This is an automated check - no AI was used to generate this comment._
- [discussion; comment; ialexj](https://github.com/OpenHands/software-agent-sdk/pull/4784#issuecomment-6062118026): Human: I've been repeatedly hitting this issue on a project. The project is in English, but this happens quite often because of the model's propensity for em-dashes and fancy-quotes. This was bad enough that I added a guard to my AGENTS.md to look out for it. I finally threw some tokens at it and had my agent diagnose and generate a reproduction. It found this existing PR and the associated issues. I hope this is useful for your prioritization. --- ### Production evidence that the read-side UTF-8 fast path is needed (and that #3678's write-side fallback doesn't cover this failure mode) We hit this in production (agent-server / `openhands-tools` 1.53.0, Python 3.13, Linux, `LANG=C.UTF-8`) and it independently reproduces the misclassification this PR fixes — this time with `mac_iceland` and silent byte corruption, not just a failed read. **
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/4784#issuecomment-6062301101): > Human: I've been repeatedly hitting this issue on a project. The project is in English, but this happens quite often because of the model's propensity for em-dashes and fancy-quotes. This was bad enough that I added a guard to my AGENTS.md to look out for it. > > I finally threw some tokens at it and had my agent diagnose and generate a reproduction. It found this existing PR and the associated issues. I hope this is useful for your prioritization. > > ### Production evidence that the read-side UTF-8 fast path is needed (and that #3678's write-side fallback doesn't cover this failure mode) > We hit this in production (agent-server / `openhands-tools` 1.53.0, Python 3.13, Linux, `LANG=C.UTF-8`) and it independently reproduces the misclassification this PR fixes — this time with `mac_iceland` and silent byte corruption, not just a failed 

### #5559: fix(agent-server): keep tmux socket paths within the Unix socket limit

- Commit: `2804c1273b4093e632e332a7a572894782c7b1e1` (2026-10-08T23:22:07+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5559).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5559). Families: F01, F03, F05, F30, F31, F32.

Stated intent:

> - The agent-server checks whether `$TMUX_TMPDIR/tmux-<uid>/openhands-<32 hex>` fits the platform's socket path limit. If the configured or default directory is too deep, it uses `/tmp/openhands-agent-server-<pid>` instead.
> - It logs a warning when it overrides an explicit `TMUX_TMPDIR`, and restores the original value on shutdown.
> - A short, explicit `TMUX_TMPDIR` is honored exactly as before. Socket naming and the isolation from #5485 are unchanged.

Changed paths:

- `openhands-agent-server/openhands/agent_server/api.py`
- `tests/agent_server/test_api.py`

Linked issue acceptance context:

- [#5558](https://github.com/OpenHands/software-agent-sdk/issues/5558): [Bug]: Terminals fail with "File name too long" when TMUX_TMPDIR is deep, since per-pool tmux socket names — current state closed, closed 2026-10-08T21:22:09Z.

> - [ ] With `TMUX_TMPDIR` set to a directory too deep for a socket path, the Agent Server still initializes terminal pools and logs which directory it used instead. - [ ] With `TMUX_TMPDIR` unset and a temp dir too deep for sockets (macOS `$TMPDIR`), the per-server default still fits. - [ ] A short, explicit `TMUX_TMPDIR` is still honored unchanged. ### Installation Method / Version `uvx openhands-agent-server==1.53.0`, as Agent Canvas launches it. Also reproduced on `main` at 8ba966d. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5559#issuecomment-6026291519)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5559#pullrequestreview-5462499737): **Approved** ✅ Reviewed the diff (`openhands-agent-server/.../api.py`, `tests/agent_server/test_api.py`) at head `55f5488`, linked issue #5558 and its acceptance criteria, and current-head CI (all green, including `agent-server-tests`, `workspace-tests`, `windows-tests`). What I checked and confirmed: - **Socket-path math is right.** `sockaddr_un.sun_path` is 108 bytes on Linux and 104 on macOS including the NUL, so the 107/103 maxima are correct. I reproduced the boundary against real tmux on Linux: a 107-byte `$TMUX_TMPDIR/tmux-<uid>/openhands-<32hex>` path starts a server; 108 fails with `File name too long`. - **`os.path.realpath()` in `_fits_tmux_socket` is load-bearing, not incidental.** tmux resolves `$TMUX_TMPDIR` before binding — I verified a short symlink pointing at a deep target *fails*, while a deep symlink pointing at a shor

### #4952: fix(agent-server): resolve linked providers for plaintext profile reads

- Commit: `9c4fd8c6664dfce98524d04d965b4f009f390fbe` (2026-10-09T02:06:11+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/4952).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-4952). Families: F20.

Stated intent:

> - Resolve linked providers for `GET /api/profiles/{name}` with `X-Expose-Secrets: plaintext`.
> - Preserve stored profiles, active defaults, and the existing unresolved behavior of ordinary and encrypted editor reads.
> - Cover encrypted provider storage and credential rotation, plus real HTTP selection through `RemoteWorkspace.get_llm(profile_name=...)`.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `b66c72436157` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -26,0 +27 @@
> +operation GET /api/canvas-extensions/installed/{extension_name}/icon operationId=get_canvas_extension_icon_endpoint_api_canvas_extensions_installed__extension_name__icon_get
> @@ -191,0 +193 @@
> +parameter GET /api/canvas-extensions/installed/{extension_name}/icon path:extension_name required=true schema=type="string" minLength=1 maxLength=255 pattern="^[a-z0-9]+(?:-[a-z0-9]+)*$"
> @@ -471,0 +474,3 @@
> +response GET /api/canvas-extensions/installed/{extension_name}/icon 200 image/svg+xml schema=type="string" format="binary"
> +response GET /api/canvas-extensions/installe

Changed paths:

- `openhands-agent-server/openhands/agent_server/profiles_router.py`
- `tests/agent_server/test_profiles_router.py`
- `tests/cross/test_remote_conversation_live_server.py`

Linked issue acceptance context:

- [#5514](https://github.com/OpenHands/software-agent-sdk/issues/5514): [Bug]: Plaintext LLM profile reads omit linked provider credentials — current state closed, closed 2026-10-09T00:06:13Z.

> - [ ] Plaintext reads of provider-linked profiles resolve the provider API key and base URL, including when provider credentials are stored encrypted. - [ ] A subsequent read uses rotated provider credentials. - [ ] Ordinary and encrypted editor reads keep their existing unresolved behavior. - [ ] Reading or selecting a named profile does not modify stored profiles or active defaults. - [ ] A real HTTP test through `RemoteWorkspace.get_llm(profile_name=...)` verifies selection and credential rotation. 

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/4952#issuecomment-5634104584)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4952#pullrequestreview-5331217452): _This review was posted by an AI agent (OpenHands)._ ## Scope This change belongs in this repository. `openhands-agent-server/openhands/agent_server/profiles_router.py` is the canonical Agent Server REST surface, which this repo owns; the extensions/automation follow-ups only consume it. No file move or maintainer architecture decision is needed. ## What the change does `GET /api/profiles/{name}` now passes `resolve_provider=expose_mode == "plaintext"` to `LLMProfileStore.load`. Previously the route always used `resolve_provider=False`. I traced the surrounding code: - The plaintext branch (the only one that resolves) then dumps with `build_expose_context("plaintext", cipher)`, so the profile's own secret is exposed as before and now the linked provider's `api_key` / `base_url` are added. - The absent-header and `encrypted` branches keep 
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4952#pullrequestreview-5427552385): _This review was posted by an AI agent (OpenHands)._ ## Scope Belongs here. `openhands-agent-server/openhands/agent_server/profiles_router.py` is this repo's canonical Agent Server REST surface; the extensions/automation follow-ups only consume it. No file move or architecture decision needed. ## Change `GET /api/profiles/{name}` now passes `resolve_provider=expose_mode == "plaintext"` to `LLMProfileStore.load` instead of the hard-coded `resolve_provider=False`. I traced both branches against the base behavior: - Plaintext (the only mode that resolves): `load` applies the linked connection's `api_key`/`base_url`, and the subsequent `build_expose_context("plaintext", cipher)` dump exposes them alongside the profile's own secret. This is exactly the credential set `RemoteWorkspace.get_llm(profile_name=...)` needs, and it matches the plainte
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4952#pullrequestreview-5428818545): _This review was posted by an AI agent (OpenHands)._ ## Scope Belongs here. `openhands-agent-server/openhands/agent_server/profiles_router.py` is this repo's canonical Agent Server REST surface; the extensions/automation follow-ups only consume it. ## What changed since the last reviewed head The previous head `da27a9ec` was approved. This head `6b41f7ad` differs only by commit "chore: Remove PR-only artifacts [automated]", which deletes `.pr/stop-bash-evidence.md` and `.pr/stop-bash-evidence.py` — scratch evidence files left over from the unrelated BashCommand PR #5348, absent from both `main` and this head. The router diff and both test files are byte-identical to the approved head (`git diff da27a9ec 6b41f7ad -- .../profiles_router.py` is empty), so the substantive change is unchanged. ## Change `GET /api/profiles/{name}` now passes `r
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/4952#pullrequestreview-5455205516): Approved: the plaintext profile-read path now reuses existing provider resolution to return the current API key and base URL needed by RemoteWorkspace.get_llm(), while ordinary/encrypted editor reads remain unresolved. The tests cover encrypted provider storage, key rotation, and real HTTP SDK retrieval without changing stored profiles or active defaults. Reviewed head 6b41f7ad1717a18eb11488601ff3e833f805c65b; current CI was green. No blocking correctness issues found. This is worth merging as an independent SDK bug fix, but is no longer a prerequisite for extensions#547: that PR now displays model provenance from conversation metadata without provider-key reads. Risk: medium because this changes a credential-bearing response, though it retains the existing authentication and explicit plaintext-exposure mechanism. This review was generate
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/4952#issuecomment-5634476665): @openhands /codereview this pr please and post your feedback as a comment. look at the linked pr too. do we really need to add a plaintext secrets? other options? why does it seemingly needed?
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/4952#issuecomment-5636792479): 🟢 **Good taste** I looked through this PR and the linked OpenHands/extensions#547 flow. I do **not** think this PR adds a new plaintext-secret capability: `X-Expose-Secrets: plaintext` already exists on settings and profile reads, `RemoteWorkspace.get_llm(profile_name=...)` already requests it, and an inline profile API key was already returned in plaintext under that mode. This patch makes a provider-linked profile obey the same runtime-read contract as an inline profile. Why it is needed in the current design: a linked profile deliberately stores only `provider_connection_id`; the current provider key and base URL live in the provider store. The conversation-create API accepts a concrete agent/LLM, but it does not accept an LLM profile name to resolve server-side. Therefore extensions#547 must resolve the named profile before constructi
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/4952#issuecomment-5636819334): Reviewed PR #4952 and linked OpenHands/extensions#547, then posted feedback here: https://github.com/OpenHands/software-agent-sdk/pull/4952#issuecomment-5636792479 Conclusion: the PR does not introduce plaintext exposure; it makes provider-linked profiles consistent with the existing authenticated `X-Expose-Secrets: plaintext` runtime contract. A server-side LLM-profile selector would avoid plaintext transit and is the cleaner long-term design, but it is a larger API change and should not block this focused fix. Validation: - 106 profile-router tests passed - 2 selected live-server tests passed - Current CI failures appear unrelated - No workspace files were changed GitHub formal approval was not possible because the available token belongs to the PR author, so I posted the requested review as a PR comment.
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4952#issuecomment-5662938852): 🚦 **CI is currently failing on this PR's latest commit.** Please fix the failing checks before OpenHands reviews it - this is re-checked automatically once you push a new commit. (A maintainer can also request `@all-hands-bot` as a reviewer to have it reviewed regardless of CI status.) _This is an automated check - no AI was used to generate this comment._

### #5619: test(agent-server): avoid overlong tmux test socket paths on macOS

- Commit: `d1bfda28da7f5c4d3b33d65e729161cef6e0a4ec` (2026-10-09T03:50:36+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5619).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5619). Families: no product runtime required.

Stated intent:

> * Separate default-path selection tests from directory-creation tests, avoiding randomized fixture paths in assertions about the production default.
> * Use a genuinely short final directory for the real TmuxPanePool integration test and the lifespan test.
> * Add deterministic macOS/Linux socket-path byte-length tests using simulated canonical paths, without globally monkeypatching process identity.
> 
> No production code changes. The per-process directory name, socket isolation, path-length validation, and /tmp fallback are unchanged.
> 
> Follow-up to #5559 (original bug #5558).

Changed paths:

- `tests/agent_server/test_api.py`

Linked issue acceptance context:

- [#5620](https://github.com/OpenHands/software-agent-sdk/issues/5620): [Bug]: Agent Server tmux test fixtures exceed macOS Unix socket path limit — current state closed, closed 2026-10-09T01:50:39Z.

> ☐ Default-path selection tests do not depend on randomized fixture-directory lengths. ☐ The real tmux integration test uses a socket directory short enough for macOS. ☐ Deterministic tests cover macOS and Linux canonical socket-path byte limits. ☐ Directory creation and lifespan environment restoration remain covered. ☐ No production behavior or socket-isolation changes. ☐ Relevant CI tests pass. 

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5619#pullrequestreview-5464805793): Approved. Test-only change that correctly stops the tests from recreating the macOS socket-length failure the production code guards against. Verified locally on `tests/agent_server/test_api.py`: - Full file: `uv run pytest tests/agent_server/test_api.py` -> **47 passed** (tmux subset `-k tmux` -> 9 passed). - New `test_default_server_tmux_tmpdir_counts_canonical_socket_bytes[macos]` and `[linux]` both pass on Linux CI, confirming the 103/107 boundary is exercised from one platform. - `ruff check` and `ruff format --check` clean. Design reads well: separating default-path selection from directory creation, using a genuinely short final dir for the real `TmuxPanePool`/lifespan tests, and keeping the `/tmp` fallback deterministic. No production changes, as advertised. Two minor, non-blocking notes: 1. The new parameterized test hardcodes th

### #5616: chore(agent-server): refresh npm pin and automate observed updates

- Commit: `33044182505244749928644a4643368a9e998ee7` (2026-10-09T02:35:02Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5616).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5616). Families: F01, F12, F16, F31.

Stated intent:

> - Upgrade the shared npm pin from 11.19.1 to 11.21.0 for both canonical and ACP Node runtimes.
> - Add a daily/manual updater that selects stable, non-deprecated, same-major releases at least seven days old; never downgrades or edits ambiguous pins.
> - Open draft update PRs using the existing bot PAT so ordinary PR CI runs. No automatic merge, no force-push, no rewriting human review; one pending npm update at a time, and previously closed version PRs are not reopened.

Changed paths:

- `.github/scripts/update_npm_pin.py`
- `.github/workflows/update-npm-pin.yml`
- `openhands-agent-server/openhands/agent_server/docker/Dockerfile`
- `tests/cross/test_agent_server_build_metadata.py`
- `tests/cross/test_update_npm_pin.py`

Linked issue acceptance context:

- [#5618](https://github.com/OpenHands/software-agent-sdk/issues/5618): Refresh agent-server npm pin and automate observed same-major updates — current state closed, closed 2026-10-09T02:35:05Z.

> - [ ] The agent-server Dockerfile's single `ARG NPM_VERSION` default is `11.21.0`, and both consumption sites use it: the canonical stage runs `npm install --global "npm@${NPM_VERSION}"` and the ACP stage runs `"$ACP_NODE_DIR/bin/npm" install --global "npm@${NPM_VERSION}"`. - [ ] The existing CI test `tests/cross/test_agent_server_build_metadata.py::test_agent_server_uses_one_pinned_npm_version_for_both_node_runtimes` passes with the new pin (its version assertion is updated; the exactly-one-default-pin and both-consumption-site assertions still hold). - [ ] A built agent-server image installs npm `11.21.0` in both the canonical and ACP runtimes, and the npm CLI executes successfully under each runtime's Node (currently `24.21.0` for both). - [ ] A repository-owned workflow exists with both a `schedule` trigger and `workflow_dispatch`, mirroring the existing Debian updater, and it authenticates with the existing `OPENHANDS_BOT_GITHUB_PAT_PUBLIC` credential (no new credentials or registry configuration). - [ ] Release selection reads the official npm registry and chooses the highest stable (non-prerelease), non-deprecated release within the current major whose registry publish time is at least seven days before the run; it never selects a version at or below the current pin (no downgrade), and makes no edit when no eligible newer release exists. - [ ] A missing pin, or more than one `ARG NPM_VERSION=<x.y.z>` line, makes the updater fail without editing the Dockerfile. - [ ] Pin edits are idempotent: re-running with no eligible update yields no file change (empty diff), and a

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5616#issuecomment-6067433688)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5616#pullrequestreview-5463527778): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope for this repository. The agent-server npm pin and its image live here (`.github/scripts/update_npm_pin.py`, `.github/workflows/update-npm-pin.yml`, `openhands-agent-server/openhands/agent_server/docker/Dockerfile`, `tests/cross/test_update_npm_pin.py`), so no product/architecture decision is needed to proceed. Technical review follows. ## Blocking finding **The bump breaks an existing test and CI is red on this head.** `cross-tests` fails on `8b2deea`: ``` tests/cross/test_agent_server_build_metadata.py::test_agent_server_uses_one_pinned_npm_version_for_both_node_runtimes FAILED AssertionError: assert '11.21.0' == '11.19.1' ``` `tests/cross/test_agent_server_build_metadata.py:161` hardcodes the expected value: ```python assert match.group(1) == "11.19.1" ``` This PR ch
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5616#pullrequestreview-5464922624): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope. The agent-server npm pin, its updater script/workflow, and the cross-tests live in this repository, so no product/architecture decision is needed. ## What changed since the prior review The earlier blocking finding (the bump left `tests/cross/test_agent_server_build_metadata.py` asserting `11.19.1`, reddening `cross-tests`) is resolved on this head. Commit `6c0b43b88` removed the literal-value assertion and replaced the adjacent Python/Node release literals with version-format checks, so a future pin bump no longer needs a manual test edit. `cross-tests` now passes. ## Verification on this head (`e70ae3b0`) - Ran the updater tests in `tests/cross/test_update_npm_pin.py` directly (pytest is not installed here): both pass, covering the seven-day boundary, same-major/pre
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5616#discussion_r4224707481): This one-line change breaks `tests/cross/test_agent_server_build_metadata.py:161`, which asserts `match.group(1) == "11.19.1"`. CI confirms it on this head: ``` test_agent_server_uses_one_pinned_npm_version_for_both_node_runtimes FAILED AssertionError: assert '11.21.0' == '11.19.1' ``` `cross-tests` is red as a result. Because `update_npm_pin.py` only rewrites this Dockerfile pin while the test keeps a literal, the automated updater added in this PR will fail `cross-tests` on every future npm bump too. Update the assertion to `11.21.0` (ideally derive it from the pin so it does not need editing on each update).
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5616#issuecomment-6070425157): Fixed the cross-tests failure in 6c0b43b88: removed the assertion against a specific npm release and replaced adjacent Python/Node release literals with version-format checks. Shared npm pin usage and runtime-layout assertions remain; updater tests still exercise synthetic version-selection behavior. Validation: reproduced the npm assertion failure before the change; all 15 build-metadata/updater test functions pass afterward using a stdlib harness loading the real dependency-free catalog. `git diff --check` passed. Full pytest/pre-commit were not available locally (pre-commit invocation failed because the executable was absent); fresh CI will validate those. The prior PR-description failure requested an issue link; the description now links #5618. The HUMAN section was not edited. _This comment was generated by an AI agent (OpenHands) on
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5616#issuecomment-6072102426): ### ⏳ Review waiting on checks The current head `e70ae3b03cb7` still has checks that have not finished, so no review conversation was started: - `agent-server-tests` - `build-binary-and-test (ubuntu-latest)` - `cross-tests` No action is needed. The scheduled scan retries once every check on the head reports a conclusion. _This is an automated check - no AI was used to generate this comment._

### #5673: feat(llm): add DigitalOcean Inference as a verified provider

- Commit: `cd17bd89d52ef209abbfca5870631d0474ca8f66` (2026-10-09T15:14:20+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5673).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5673). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - `litellm_call_kwargs` now resolves routed SDK prefixes from one small table: `openhands/` → `litellm_proxy/` + the OpenHands proxy (unchanged), and `digitalocean/` → `openai/` + `https://inference.do-ai.run/v1`. A user-provided `base_url` still wins.
> - `VERIFIED_MODELS["digitalocean"]` adds a short curated list of DO model IDs (taken from `GET /v1/models`), so DigitalOcean appears in the verified section of the provider/model pickers. Only Chat Completions models with tool calling; the gpt-6 line is left out because DO needs the Responses API for its tools.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/utils/openhands_provider.py`
- `openhands-sdk/openhands/sdk/llm/utils/verified_models.py`
- `tests/sdk/llm/test_llm.py`
- `tests/sdk/llm/test_model_list.py`

Linked issue acceptance context:

- [#5666](https://github.com/OpenHands/software-agent-sdk/issues/5666): [Feature]: Add DigitalOcean Inference as a selectable (verified) LLM provider — current state closed, closed 2026-10-09T13:14:22Z.

> - [ ] `digitalocean/<model>` is routed to `openai/<model>` with `api_base=https://inference.do-ai.run/v1` (unit test on `litellm_call_kwargs`-style routing), and a user-provided `base_url` still wins. - [ ] `VERIFIED_MODELS["digitalocean"]` exists and contains only models verified live to complete a multi-turn agent run with tool calls over `/chat/completions` (no Responses-only models). - [ ] Context window / max output tokens / feature flags (vision, reasoning, prompt caching) are known for every verified DO model, so condensation and limits behave correctly (LiteLLM has no metadata for `openai/<do-model-id>`). - [ ] `GET /llm/providers` + `/llm/models/verified` on the agent-server expose `digitalocean` so Agent Canvas lists it in the verified providers section. - [ ] Telemetry/cost accounting doesn't break for these models (unknown pricing is acceptable, errors are not). - [ ] Follow-up in OpenHands/OpenHands (Agent Canvas): display name `digitalocean: "DigitalOcean"` in `src/utils/map-provider.ts`, and bump the SDK pin once released. ### Alternatives Considered - **LiteLLM `gradient_ai/` provider**: no tool calling (see above), wrong URL for `/v1` base URLs. Could be fixed upstream in LiteLLM, but we'd still depend on its release and on its stale model metadata. - **Advanced settings only (`openai/` + custom base URL)**: already works and can be documented as a stopgap, but it is not a selectable provider and requires users to know the endpoint and model IDs. ### Priority / Severity High - Significant impact on productivity ### Estimated Scope Small - API addition, conf

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5673#issuecomment-6081093056)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5673#pullrequestreview-5470362841): LGTM, thanks!

### #5182: feat(security): emit SecurityAnalysisEvent with the analyzer's verdict per action batch

- Commit: `9e76fe72b43f7be65b48a243486c2e30b5104a22` (2026-10-09T16:24:54Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5182).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5182). Families: F06, F07, F08, F32, F33, F34.

Stated intent:

> - **Async audit masking**: resolves lazy secrets in a worker while releasing the conversation state lock, then rechecks registry stability and emits the masked audit before applying confirmation policy. Regression coverage uses a real loopback HTTP secret service and registers another secret during lookup.
> 
> - **`SecurityAnalysisEvent`** (`event/security.py`): `source="environment"`, not `LLMConvertible` so the model never sees it. Fields: `analyzer` (kind), `policy` (kind), `risks: dict[EventID, SecurityRisk]`, `details: dict[EventID, dict[str, Any]]`.
> - **`SecurityAnalysis(risk, details)`** plus `SecurityAnalyzerBase.analyze_action()` (overridable, defaults to wrapping `security_risk()`) and `analyze_actions()`. This lets an analyzer attach probabilities, confidence, or a rationale. Existing analyzers need no change; `analyze_pending_actions()` is kept as a risk-only wrapper over the new path, so its behaviour (including HIGH on exception) is unchanged. On exception the detail carries `{"error": ...}`.
> - **Emission**: one event per analyzed action batch, from `_requires_user_confirm

Changed paths:

- `.github/agent-server-openapi-weak-schema-allowlist.json`
- `openhands-sdk/openhands/sdk/agent/agent.py`
- `openhands-sdk/openhands/sdk/agent/response_dispatch.py`
- `openhands-sdk/openhands/sdk/event/__init__.py`
- `openhands-sdk/openhands/sdk/event/security.py`
- `openhands-sdk/openhands/sdk/security/__init__.py`
- `openhands-sdk/openhands/sdk/security/analyzer.py`
- `tests/sdk/agent/test_async_secret_masking.py`
- `tests/sdk/agent/test_security_analysis_event.py`

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5182#pullrequestreview-5261393746): ## ❌ QA Report: FAIL The audit event works in the exercised SDK paths, but live Haiku before/after QA reproduced a custom-analyzer confirmation bypass and unredacted secret persistence. ### Does this PR achieve its stated goal? **Partially.** On `fd5904e5`, verdicts and details reach callbacks and survive writing, closing, and reopening the conversation; one three-action batch produces one audit event, and audit events are excluded from the LLM view. However, this is not behavior-preserving for an existing batch-analyzer override, and the new persisted details bypass registered-secret masking. \| Phase \| Result \| \|---\|---\| \| Environment setup \| ✅ Locked SDK runtime installed with `uv sync --frozen --package openhands-sdk --no-dev`; SDK imports successfully \| \| CI status \| ✅ Current head: 33 successful, 6 skipped; none failing/pe
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5182#pullrequestreview-5261560196): _This review was created by an AI agent (OpenHands) on behalf of the repository maintainers._ **Taste rating: good** 👍 No material findings. This is a clean, additive change: - **Gated & inert by default**: emission only happens when `state.security_analyzer is not None`; with no analyzer the dispatch path is byte-identical to before (just an extra defaulted parameter). Zero cost for existing users, as claimed. - **Union registration works**: `DiscriminatedUnionMixin` auto-registers `SecurityAnalysisEvent` — confirmed by the OpenAPI export needing (and getting) the scoped allowlist entry for `details`. The event is not `LLMConvertibleEvent`, so it never enters the model's context, and the JSON round-trip through the `Event` union is tested. - **Compat preserved**: `analyze_pending_actions()` is now a risk-only wrapper over `analyze_action
- [reviews; COMMENTED; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5182#pullrequestreview-5262281573): ## 🟡 QA Report: PARTIAL Both reported regressions are fixed in `6fa4e0ed8`: 15 local functional scenarios passed; live LLM verification was blocked by missing credentials. ### Does this PR achieve its stated goal? Yes in the exercised SDK paths: analyzer verdicts are recorded before execution, including under NeverConfirm, remain outside the LLM view, and survive close/reopen. Legacy batch overrides now supply the actual confirmation verdict, and registered secrets in nested details and exception messages are masked before callbacks and persistence. \| Phase \| Result \| \|---\|---\| \| Environment setup \| ✅ `make build` completed \| \| CI status \| Pending on the pushed commit; pre-fix CI passed except unresolved review threads \| \| Functional verification \| ✅ 15 local SDK scenarios; 🟡 live provider call unavailable \| <details><summa
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5182#pullrequestreview-5372178140): _This review was posted by an AI agent (OpenHands)._ ## Review of PR #5182 @ `6fa4e0e` **Scope:** in-repo. Events, agent dispatch, and security analyzers are owned by this repository, and the change follows the SDK → OpenAPI → TS-client flow: the new `SecurityAnalysisEvent` is an `environment`-sourced event that is not `LLMConvertible`, so it never enters the model context, and the OpenAPI weak-schema entry is scoped to `details` (analyzer-specific by design, same precedent as `HookExecutionEvent.hook_input`). ### Verified on this head - **Both earlier P1 threads are resolved and the fixes are present.** Legacy `analyze_pending_actions` overrides are consulted again via `type(self).analyze_pending_actions is not SecurityAnalyzerBase.analyze_pending_actions`, and the emitted event is passed through `state.secret_registry.mask_secrets_in_mo
- [reviews; CHANGES_REQUESTED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5182#pullrequestreview-5409099663): 🟡 **Taste Rating: Acceptable** — the event model and backward-compatible analyzer wrapper are coherent, but the current head is not merge-ready. **[CRITICAL ISSUES]** - [Current-head CI] **Windows binary build is failing:** `build-binary-and-test (windows-latest)` failed on the current head: https://github.com/OpenHands/software-agent-sdk/actions/runs/37243581710/job/111556964633. Diagnose and fix the failure, or rerun it successfully if it is demonstrably transient, before requesting re-review. The PR includes a real LLM/tool end-to-end event-persistence transcript, and Canvas visual evidence is genuinely not applicable because this PR adds an SDK/server event without UI rendering. Static review found no prompt injection, dangerous scripts, exposed secrets, dependency/supply-chain changes, or merge conflict. **[RISK ASSESSMENT]** - [Over
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5182#pullrequestreview-5417498063): _This review was posted by an AI agent (OpenHands)._ ## Review of PR #5182 @ `717eed4` **Scope:** in-repo. Events, agent dispatch, and security analyzers are owned by this repository, and the change follows the SDK → OpenAPI → TS-client flow: the new `SecurityAnalysisEvent` is an `environment`-sourced, non-`LLMConvertible` event, so it never enters the model context, and the weak-schema allowlist entry is scoped to `details` (analyzer-specific by design, same precedent as `HookExecutionEvent.hook_input`). ### Verified on this head - **Both earlier P1 threads are resolved and the fixes are present.** `analyze_actions()` still routes through a legacy `analyze_pending_actions` override when one exists (`type(self).analyze_pending_actions is not SecurityAnalyzerBase.analyze_pending_actions`), and the emitted event is masked via `mask_secrets_
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5182#pullrequestreview-5421134224): Approved following explicit maintainer authorization for reviewed head 717eed414. The Windows CI failure cited in my earlier review is resolved on this head. Current checks pass, and the five-model integration workflow succeeded on this exact commit (49 passed, one expected vision skip). Inspected verdict emission, legacy batch-analyzer compatibility, masking before callbacks/persistence, async secret lookup with state-lock release, and regression coverage. No material code findings. No GUI screenshot is applicable because this adds an SDK event without UI rendering. Feature-specific live evidence is author-provided; the latest async path has controlled-model/real-HTTP coverage. I did not independently rerun a live conversation. Risk assessment: Medium — confirmation dispatch, registered-secret handling and a public serialized event. No-a
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5182#discussion_r4057744486): 🔴 **Critical [P1]: preserve existing batch-analyzer overrides when changing the dispatch API.** This replaces the call to `analyze_pending_actions()` with `analyze_actions()`. The compatibility wrapper only preserves callers of the old method, not custom analyzers that override it: the agent now silently ignores their batch verdicts. I reproduced this in live Haiku-driven conversations on base and head with an otherwise unchanged analyzer: ```python class LegacyBatchAnalyzer(SecurityAnalyzerBase): def security_risk(self, action): return SecurityRisk.LOW def analyze_pending_actions(self, pending_actions): return [(action, SecurityRisk.HIGH) for action in pending_actions] ``` Under `ConfirmRisky()`, base stops at `waiting_for_confirmation` with **0 file writes**. This head finishes with **1 actual file write**, and the new audit says LOW, e
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5182#discussion_r4057744490): 🔴 **Critical [P1]: mask registered secrets before emitting analyzer details.** `analysis.details` is passed directly into an event that the callback chain persists and publishes; the exception fallback now also puts `str(e)` in these details. There is no general secret-masking step in that callback chain. Reproduced using only a synthetic value: register `QA_SYNTHETIC="qa-fake-secret-5182-not-real"`, then have an analyzer raise `RuntimeError("upstream rejected credential qa-fake-secret-5182-not-real")`. On this head, the callback and the event read back after closing/reopening both contain the literal value in `details[action_id]["error"]`. The registry's own `mask_secrets_in_output()` correctly returns `<secret-hidden>` for it; base did not put this error text in the event log. Backend request errors and analyzer rationales can contain c
- [review_comments; comment; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5182#discussion_r4058581335): Fixed in 6fa4e0ed8. The detailed-analysis bridge now honors existing analyze_pending_actions overrides and wraps their verdicts without a second analysis. The shared per-action implementation also lets legacy overrides call super() without recursion. Regression coverage checks both override forms through agent dispatch. Before/after functional QA changed the legacy case from finished / 1 write / LOW to waiting_for_confirmation / 0 writes / HIGH; async dispatch agrees.
- [review_comments; comment; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5182#discussion_r4058581416): Fixed in 6fa4e0ed8. The completed SecurityAnalysisEvent passes through the conversation registry’s recursive model masking before on_event. Regression coverage checks nested successful details and exception details in callbacks, persisted JSON, and reopened conversations, retaining HIGH on exceptions. Before/after functional QA changed raw_fake_secret_in_event_files from true to false; async errors and nested details also show no raw synthetic secret in callbacks or reopened event logs.
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5182#discussion_r4232229939): ```suggestion This event is not sent to the LLM. ```
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5182#discussion_r4232238484): ```suggestion rationale) should override this. ```
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5182#issuecomment-5739247079): Both CI failures on PR #5182 are resolved and pushed to the fork branch `feat/security-analysis-event`. ## What was failing 1. **`sdk-tests`** — the single failing test was `tests/sdk/llm/test_model_features.py::test_reasoning_effort_support[openrouter/moonshotai/kimi-k2.5-False]`. This is the known data-drift issue (#4877): LiteLLM fetches its model database from upstream at import time, and `openrouter/moonshotai/kimi-k2.5` now reports `reasoning_effort` support. The test file itself (and this PR) is untouched by the PR, and the same file already documents that these values "may move again." 2. **`Validate PR description`** — the PR body was missing the required `## How to Test` section and had no linked issue in `## Issue Number`. ## What I did - **`sdk-tests` fix**: flipped the drifted expectation `("openrouter/moonshotai/kimi-k2.5", 
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5182#issuecomment-5753096898): ⚠️ **OpenHands gave up on this review** for commit `fd5904e5403d` after 2h (last state: `active`). _This comment was posted by an AI agent (OpenHands)._
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5182#issuecomment-5753914211): 🚦 **CI is currently failing on this PR's latest commit.** Please fix the failing checks before OpenHands reviews it - this is re-checked automatically once you push a new commit. (A maintainer can also request `@all-hands-bot` as a reviewer to have it reviewed regardless of CI status.) _This is an automated check - no AI was used to generate this comment._
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5182#issuecomment-5994273618): # 🧪 Integration Tests Results **Overall Success Rate**: 100.0% **Total Cost**: $1.96 **Models Tested**: 5 **Timestamp**: 2026-10-05 12:17:19 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37308104363) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37308104363) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37308104363) - **litellm_proxy_deep
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5182#issuecomment-5994452952): HUMAN: @neubig Re: > Current-head CI] Windows binary build is failing: build-binary-and-test (windows-latest) failed on the current head: https://github.com/OpenHands/software-agent-sdk/actions/runs/37243581710/job/111556964633. Diagnose and fix the failure, or rerun it successfully if it is demonstrably transient, before requesting re-review. It was clearly transient, the GitHub runner just stalled. My agent re-ran it, and it passes. Nothing in the PR affects it.
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5182#issuecomment-6084791244): I am an AI agent helping Engel Nyst (@enyst) with project work. Reviewed and independently tested current head **`d6a512553bf92c896dd9f2fd877f7aaa2b041fbc`**. The requested live migration and restore tests passed; I found no blocking Event-layer regression in those runs. I used the real OpenHands Agent Canvas app, a saved DeepSeek profile, real DeepSeek API responses (`deepseek/deepseek-flash`), and actual file/terminal tools. The stack was Canvas **1.25.0** (`6e8157147936768bf358903a6341bc01e0ce1ac9`), TypeScript client **1.53.0**, and automation **1.19.0**. PyPI confirmed agent-server **1.53.0** was the latest release. - I created two conversations on that release, restarted the same persisted app state with all four SDK/server packages installed from the PR checkout, and continued both. - I then created two conversations on the PR, clo

### #5683: chore: remove stale triage workflow and maintainer roster

- Commit: `dd1af943cb491e21c4b0f83f513ccf602c43f2dd` (2026-10-09T18:34:48+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5683).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5683). Families: no product runtime required.

Stated intent:

> - Remove `.github/workflows/stale.yml`.
> - Remove the unused `MAINTAINERS` roster.

Changed paths:

- `.github/workflows/stale.yml`
- `MAINTAINERS`

Linked issue acceptance context:

- [#5684](https://github.com/OpenHands/software-agent-sdk/issues/5684): [Maintenance] Remove obsolete stale-triage workflow and maintainer roster — current state closed, closed 2026-10-09T16:34:50Z.

> - [ ] `.github/workflows/stale.yml` is deleted from the default branch, and a repository-wide search finds no remaining reference to `actions/stale` or to that workflow file. - [ ] `MAINTAINERS` is deleted from the default branch, and a repository-wide search finds no remaining reference to `MAINTAINERS` consumed by `assign-reviews.yml` or by any active workflow. - [ ] The change is limited to the two named files: GitHub's compare view against `main` shows only these two deletions, with no added or modified files. - [ ] The scheduled stale job no longer runs: the workflow no longer appears in the repository's Actions workflow list and no further scheduled runs are produced after the deletion.

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5683#pullrequestreview-5472772900): LGTM. Straightforward removal of stale automation config, consistent with the updated ownership/triage policy. No dangling references found. (Reviewed by an AI agent on behalf of the requester.)

### #5670: fix(file-editor): preserve terminal newline semantics

- Commit: `bfc1ae71b0e0bb9433a7bd3b7f07a498271a14b6` (2026-10-09T12:39:18-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5670).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5670). Families: F14, F27, F33.

Stated intent:

> - Number actual text lines with `splitlines()` instead of numbering newline separators.
> - Do not append a fabricated output line for an empty file.
> - Cover unterminated, singly terminated, blank-final-line, and empty files through the public file-editor path.

Changed paths:

- `openhands-tools/openhands/tools/file_editor/editor.py`
- `tests/tools/file_editor/test_basic_operations.py`

Linked issue acceptance context:

- [#5668](https://github.com/OpenHands/software-agent-sdk/issues/5668): [Bug]: File editor view invents numbered lines for terminal newlines — current state closed, closed 2026-10-09T16:39:20Z.

> - [ ] `file_editor(command="view")` on a file containing exactly `hello\n` renders only a numbered line 1 (`hello`) with no second (blank) numbered line. - [ ] `file_editor(command="view")` on an empty file renders no numbered content lines. - [ ] `file_editor(command="view")` on `hello` (no terminal newline), `hello\n`, and `hello\n\n` each render the correct line count: 1, 1, and 2 (the last including one real blank numbered line). - [ ] Ranged `view` (`view_range`) and edit-result snippets (`str_replace`, `insert`) keep correct absolute line numbers, with the same no-fabricated-trailing-line behavior. - [ ] Tests exercising the public file-editor path cover the empty, unterminated, singly terminated, and blank-final-line cases, and the existing file-editor tests, formatting, and type checks pass. - [ ] Re-run of the `critic` example under the release CI model/proxy configuration completes within its step limit without the agent chasing a fabricated blank line.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5670#issuecomment-6080889048)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5670#pullrequestreview-5471805059): _This review was posted by an AI agent (OpenHands)._ The plain `view` fix itself is correct: `splitlines()` numbers actual text lines, so `hello\n` now renders only line 1 and an empty file renders no numbered content, matching the linked issue #5668. The focused suite passes (`160 passed`) and the current-head checks `tools-tests`, `integration-test`, `test-examples`, and `build` are green. However, the semantic change to `_make_output` interacts with the untouched ranged-view caller and introduces a regression there, so this does not meet the issue's own acceptance criterion that ranged `view` keep correct line numbers. Detail inline. Separately, the `Validate PR description` check is failing on this head because the required human-written note between `HUMAN:` and `AGENT:` is still the empty template comment. That is a merge gate owned
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5670#discussion_r4231531785): The `split(" ")` -> `splitlines()` change makes ranged `view` silently drop a trailing blank line when the requested range ends on a blank line. The ranged caller (`editor.py:419-420`) passes `"\n".join(file_content.splitlines())` into `_make_output`. When the range's last physical line is blank, `read_file` returns text ending in `"\n\n"`; `"\n".join(...splitlines())` collapses that to a string ending in a single `"\n"`, which the new `splitlines()` treats as a terminated line rather than a blank one. The previous `split("\n")` kept that empty field, so this is a behavioral regression on the head, not just a refactor. Demonstrated on the current head (real public file-editor path, file `a\nb\n\n`, 3 physical lines): - `view` -> lines `1 2 3` - `view_range=[1, 3]` -> lines `1 2` (line 3 disappears) Similarly, file `a\n\n` shows line 2 in 
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5670#discussion_r4231719155): Fixed in 8a843f6a4: ranged view now passes the read content unchanged to _make_output. Five public-path cases cover unterminated/terminated lines, a final blank line, and a blank-only file; each compares full/ranged output and checks a final-line-only range with absolute numbering. Before the fix: 3 failed / 2 passed. After: all 165 file-editor tests and applicable pre-commit hooks pass. Audited other _make_output callers: replacement/insertion snippets and undo already pass raw read content without this pre-join. Fresh CI is pending. _This reply was generated by an AI agent (OpenHands) on behalf of Graham Neubig._
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5670#discussion_r4232311625): Thanks - applied in 2bffdd076: split/join on "\n" only (matching _count_lines), normalized the ranged snippet without a splitlines() round-trip, and added form-feed and U+2028 cases plus an insert-lands-on-the-right-line test. All 168 file_editor tests pass.
- [discussion; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5670#issuecomment-6083676203): I'm not sure how relevant the following is in practice, since it only affects files containing some rare characters, so please treat it as something to consider rather than a blocker. --- A possible issue with the fix itself: `str.splitlines()` doesn't split lines the same way the rest of the editor does. `_count_lines`, `read_file`, `insert` and the `view_range` checks all iterate the file with `open()`, which only breaks on `\n` (plus universal-newline `\r\n`/`\r`). `splitlines()` also breaks on `\x0b`, `\x0c`, `\x1c`–`\x1e`, `\x85`, `\u2028` and `\u2029`. So any file containing one of those now shows `view` line numbers that differ from what `insert`/`view_range` use. Form feeds do appear in some C/Python sources with page breaks, and `\u2028` occasionally shows up in JS/JSON strings. Reproduced on this PR's head (`907994706`), raw `vi
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5670#issuecomment-6085051479): Review feedback is addressed and all review threads are resolved. All code checks pass (file_editor tests 168/168, pre-commit, tools-tests, integration-test). The only remaining non-passing gate is `Validate PR description`, which requires a short human-written note between `HUMAN:` and `AGENT:` in the description. That section is reserved for a human author, so an AI agent must not fill it in - it needs a one-line note from the author before merge. _This comment was generated by an AI agent (OpenHands) on behalf of @gneubig._

### #5579: feat(acp): discover an ACP agent's default and available models (and restore Gemini model handling)

- Commit: `6d51e7262fc234586ff5e8e519ce5a658d334cdf` (2026-10-09T13:00:08-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5579).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-5579). Families: F01, F03, F04, F05, F06, F07, F09, F12, F18, F19, F20, F21, F22, F23, F24, F30, F31, F32, F33, F34.

Stated intent:

> - **`POST /api/acp/models`** (new `acp_router.py`) takes `agent_settings` plus the same `secrets` map as a conversation start (`LookupSecret` references to stored secrets work). It returns `ACPModelDiscovery`: `current_model_id` (the server's own default), `available_models`, `agent_name`/`agent_version`, `supports_runtime_model_switch`, and `error` (`ACPAuthRequired` / `ACPStartupTimeout` / `ACPSpawnError` / `ACPInitError`, in a 200 response).
>   - Successful results are cached for 5 minutes, keyed on command and resolved credential values; identical concurrent requests share one run, at most two run at a time, and `refresh: true` bypasses the cache.
>   - Returns 501 when conversations run in containers (`conversation_runtime="docker"`).
> - **`openhands.sdk.agent.acp_model_discovery.discover_acp_models`** runs the regular `ACPAgent.init_state` in a throwaway session, so env assembly, secret injection, file credentials, auth-method selection and the npx cache behave exactly as in a conversation.
>   - It ignores `acp_model`, MCP servers and agent context, and removes its directory afterwa

Changed paths:

- `clients/typescript/endpoint-audit.config.json`
- `clients/typescript/src/__tests__/api-clients.test.ts`
- `clients/typescript/src/client/acp-client.ts`
- `clients/typescript/src/clients.ts`
- `clients/typescript/src/index.ts`
- `clients/typescript/src/models/acp-model-discovery.ts`
- `clients/typescript/src/models/acp.ts`
- `openhands-agent-server/openhands/agent_server/acp_router.py`
- `openhands-agent-server/openhands/agent_server/api.py`
- `openhands-sdk/openhands/sdk/__init__.py`
- `openhands-sdk/openhands/sdk/agent/acp_agent.py`
- `openhands-sdk/openhands/sdk/agent/acp_contracts.py`
- `openhands-sdk/openhands/sdk/agent/acp_model_discovery.py`
- `openhands-sdk/openhands/sdk/agent/acp_models.py`
- `openhands-sdk/openhands/sdk/llm/auth/openai.py`
- `openhands-sdk/openhands/sdk/settings/__init__.py`
- `openhands-sdk/openhands/sdk/settings/acp_providers.py`
- `tests/agent_server/test_acp_router.py`
- `tests/sdk/agent/fake_acp_models_server.py`
- `tests/sdk/agent/test_acp_agent.py`
- `tests/sdk/agent/test_acp_conformance.py`
- `tests/sdk/agent/test_acp_model_discovery.py`
- `tests/sdk/llm/auth/test_openai.py`
- `tests/sdk/settings/test_acp_providers.py`

Linked issue acceptance context:

- [#5578](https://github.com/OpenHands/software-agent-sdk/issues/5578): ACP: let clients discover an ACP agent's default and available models before a conversation — current state closed, closed 2026-10-09T17:00:10Z.

> - [ ] `POST /api/acp/models` returns `current_model_id` and `available_models` for claude-code, codex, gemini-cli, kimi-code, pi and opencode. - [ ] Missing credentials come back as `error.code == "ACPAuthRequired"` in a 200 response. - [ ] Stored secrets can be passed by reference (`LookupSecret`), as in a conversation start. - [ ] Gemini sessions list their models again and honour the requested model. ### Alternatives Considered Using only the per-conversation `available_models` (helps inside a conversation, not when configuring the agent), or keeping the curated lists up to date by hand (cannot follow credential-dependent catalogues). ### Priority / Severity Medium ### Estimated Scope Medium ### Feature Area Agent API / Core functionality ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5579#issuecomment-6044962447)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5579#pullrequestreview-5471784979): _This review was posted by an AI agent (OpenHands)._ Reviewed head `39c7ed822b2972fa0f556c0f5e7646762657016d`. The endpoint, the client-ahead declaration, and the deprecations all check out. No blocking findings. ### What I verified - **`POST /api/acp/models`** — the new `acp_router.py` reuses the conversation-start `secrets` shape (including `LookupSecret` references) and the codex stored-login binding, returns errors in the body rather than as HTTP errors on launch/auth failure, and is explicitly 501 when `conversation_runtime == "docker"`. The cache key covers command/args/session-mode/isolate/file-secrets/resolved-secret-values, so new credentials re-probe (`test_new_credentials_ask_the_server_again`). - **ACP 0.12 regression** — worth calling out, since it is the subtle part of this PR. I installed `agent-client-protocol==0.12.1` and

### #5671: test(integration): validate hard reset semantics in c02

- Commit: `e8ad54729e43ca767911e28f8e350a3c3eb84c78` (2026-10-09T13:26:46-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5671).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5671). Families: no product runtime required.

Stated intent:

> - make c02's hard-reset trigger deterministic without relying on model-generated event counts
> - validate the actual reset semantics: preserve the system prompt, forget all other prior history, insert the summary after it, and produce a system-first LLM request
> - verify continued execution and a later normal condensation that preserves the hard-reset summary; align the integration README
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `330441825052` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -1581 +1581 @@
> -schema Event oneOf=[ServerErrorEvent,ACPToolCallEvent,Condensation,CondensationRequest,CondensationSummaryEvent,ConversationErrorEvent,ConversationStateUpdateEvent,HookExecutionEvent,LLMCompletionLogEvent,ActionEvent,MessageEvent,AgentErrorEvent,ObservationEvent,UserRejectObservation,SystemPromptEvent,StreamingDeltaEvent,TokenEvent,InterruptEvent,PauseEvent]
> +schema Event oneOf=[ServerErrorEvent,ACPToolCallEvent,Condensation,CondensationRequest,CondensationSummaryEvent,ConversationErrorEvent,ConversationStateUpdateEvent,HookE

Changed paths:

- `tests/integration/README.md`
- `tests/integration/tests/c02_hard_context_reset.py`

Linked issue acceptance context:

- [#5669](https://github.com/OpenHands/software-agent-sdk/issues/5669): Fix c02 hard-reset assertions after system-prompt preservation — current state closed, closed 2026-10-09T17:26:48Z.

> - [ ] The first hard reset in c02 no longer depends on the tested model producing a particular number of events: the setup reaches the hard-reset condition deterministically (for example by constructing the conversation state directly), so the integration test does not vary with model behavior. - [ ] The first condensation in c02 is verified as a hard reset by semantics, not by `summary_offset == 0`: the leading `SystemPromptEvent` is not forgotten, all pre-reset history after the system prompt is forgotten, and the generated summary is inserted immediately after the preserved system prompt (offset equal to the system prompt's position). - [ ] c02 verifies that projecting the post-reset conversation to LLM messages yields a request whose first message has role `system`. - [ ] c02 verifies the conversation continues successfully after the reset and that a subsequent real (model-driven) condensation occurs without forgetting the hard-reset summary event; this later condensation is not asserted on a specific offset. - [ ] `tests/integration/README.md`'s `c02_hard_context_reset` entry describes the current contract: the leading system prompt is preserved, the summary is inserted after it, and the remaining pre-reset history is forgotten. - [ ] The test passes through the real integration runner in `condenser-test` mode with an actual LLM, with the public `Condensation` fields the assertions rely on (`forgotten_event_ids`, `summary_offset`, `summary_event`, `summary`) still available. - [ ] The change is confined to c02 integration test code and integration-test documentation: n

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5671#issuecomment-6080909583)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#pullrequestreview-5472334970): Just have a coupe of comments
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5671#pullrequestreview-5472984410): _This review was posted by an AI agent (OpenHands)._ ## Scope Test-only change to `tests/integration/tests/c02_hard_context_reset.py` and its README entry, in the repository that owns the SDK/condenser and its integration harness. In scope. ## Code review (clean) I read `AGENTS.md`, `.agents/skills/custom-codereview-guide.md`, and the condenser runtime in the workspace. The rewritten assertions match the real implementation: - `LLMSummarizingCondenser.hard_context_reset` preserves the leading `SystemPromptEvent` (`preserve = system_idx + 1`) and inserts the summary at that offset, so the test's `summary_offset == system_index + 1` and "forget exactly the non-system history" checks describe actual behavior (not the stale `summary_offset == 0` contract). - `View.apply` / `append_event` produce the asserted `[system, summary]` post-reset vie
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5671#pullrequestreview-5473232486): _This review was posted by an AI agent (OpenHands)._ ## Scope Test-only change to `tests/integration/tests/c02_hard_context_reset.py` and its `tests/integration/README.md` entry, in the repository that owns the SDK/condenser and its integration harness. In scope. ## Assessment This is a re-review. Since the previous review, others have posted new evidence, so I re-established the mutable state rather than trusting the earlier pass. The one previously blocking item is now satisfied: the `condenser-test` integration runner ran on the exact head `5361a8350` (run `37963881263`, `Run Integration Tests condenser-test`, conclusion `success`) with 100% pass — `litellm_proxy_openai_gpt_5.5` 4/4 (`c01` skipped, model lacks extended thinking) and `litellm_proxy_anthropic_claude_sonnet_4_6` 5/5 — which is the acceptance criterion in linked issue #566
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975764): With the condense() moved before the first run(), the hard reset only ever sees [system, user]. Before, it ran after a real agent turn, so actions/observations (and reasoning items) went through the summarizer, and the next request had to cope with whatever was left. That was the part I'd actually expect to break, and it isn't covered anymore. Could we keep the deterministic setup but do one agent turn before the first condense()?
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975774): I like that this is deterministic now, but I ran `_get_forgotten_events` on the view this produces and it forgets the agent's "hello world" plus markers 0-5. The action/observation pair (indices 3-4) never ends up in the range, so we lose coverage for the case where normal condensation splits a tool call from its result. Maybe put the markers before the agent turn, or tweak keep_first so the range starts on the action?
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975778): I don't think this is quite why it falls back. The view here is just [system, user], so manipulation indices are {0, 1, 2} and `find_next(4)` raises ValueError before we ever get to the minimum progress check. Same thing happens with keep_first=1, fwiw. Something like: ```suggestion # The first view is only [system, user], so no valid forgetting boundary # exists past keep_first and the request falls back to a hard reset. # Later, deterministic context markers make a normal condensation viable. ```
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975795): There's no actual marker here and we never look at the summary. Either drop it, or put a token in and check it shows up in `hard_reset.summary`? That'd give us at least one check on content, not just ids.
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975802): nit: the forgotten ids check + the exact [system, summary] id check already pin this down, so the apply() comparison and the events_to_messages check feel redundant to me. Could drop them.
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975804): nit: View builds its events via `event.apply(...)` (view.py:122), so this compares apply() against apply(). If apply() itself is wrong this still passes. Checking the summary position and that forgotten ids are gone directly would be more meaningful.
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975808): nit: both of these are exported from `openhands.sdk.event`, which is what c01 uses: ```python from openhands.sdk.event import Condensation, LLMConvertibleEvent, SystemPromptEvent ```
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4231975816): minor: provider errors would already blow up via ConversationRunError, but hitting max iterations just sets ERROR without raising, and nothing checks that. Maybe assert the status is FINISHED after each run()?
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232314946): Agreed. Reworked in 5361a8350: the test now sends a real instruction, runs the agent once, captures that state, and only then calls condense(), so the hard reset summarizes an actual agent turn again.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232315311): Fixed in 5361a8350. The post-reset turn is now an explicit terminal-tool turn and keep_first=4 protects [system, summary, agent turn, action] (system=0, so the range start is the observation at index 4), which is why the action/observation pair is preserved rather than stranded. I also switched the trailing padding to deterministic marker messages so the run is model-independent.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232315743): You're right - that was exactly it. find_next(4) raised ValueError on the [system, user] view before any progress check. Resolved in 5361a8350 by running a real agent turn first, and the comment now states the real reason.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232316215): Done in 5361a8350: the reset turn's instruction asks the agent to echo HARD_RESET_MARKER_7F3A, and verify_result asserts that token appears in hard_reset.summary.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232316649): Dropped the redundant apply()/events_to_messages comparisons in 5361a8350.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232317066): Reworked in 5361a8350 to assert the observable effects directly: the summary sits right after the system prompt, exactly the non-system history is forgotten, and the protected prefix is intact.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232317733): Switched to the openhands.sdk.event exports in 5361a8350.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#discussion_r4232318166): Added an _assert_finished() check after every run() in 5361a8350, so a max-iteration ERROR now fails the test instead of passing silently.
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5671#issuecomment-6081047991): # 🧪 Condenser Tests Results **Overall Success Rate**: 88.9% **Total Cost**: $0.62 **Models Tested**: 2 **Timestamp**: 2026-10-09 12:38:16 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37930376517) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37930376517) ## 📊 Summary \| Model \| Overall \| Tests Passed \| Skipped \| Total \| Cost \| Tokens \| \|-------\|---------\|--------------\|---------\|-------\|------\|--------\| \
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#issuecomment-6085051907): Review feedback from VascoSch92 is addressed and all review threads are resolved (see the per-thread replies). The c02 test now: runs a real agent turn before the explicit condense so the hard reset summarizes an actual turn; uses keep_first=4 so the later condensation is a normal one and the action/observation pair is preserved; pads with deterministic, model-independent marker events; asserts the reset summary actually carries a marker token; checks system-first placement directly; and asserts FINISHED after every run(). Verified locally: c02 passes repeatedly on both claude-sonnet-4-6 and gpt-5.5, and the full condenser suite (c01-c05) is green. The only remaining non-passing gate is `Validate PR description`, which requires a short human-written note between `HUMAN:` and `AGENT:` in the description. That section is reserved for a huma
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5671#issuecomment-6085782607): # 🧪 Condenser Tests Results **Overall Success Rate**: 100.0% **Total Cost**: $1.07 **Models Tested**: 2 **Timestamp**: 2026-10-09 17:20:03 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37963881263) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37963881263) ## 📊 Summary \| Model \| Overall \| Tests Passed \| Skipped \| Total \| Cost \| Tokens \| \|-------\|---------\|--------------\|---------\|-------\|------\|--------\| 
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5671#issuecomment-6085792040): Current-head condenser evidence is now in place. Re-ran the `condenser-test` workflow via the label on the current head `5361a8350`: - Run: https://github.com/OpenHands/software-agent-sdk/actions/runs/37963881263 (conclusion: success) - Overall success rate: **100.0%** - `litellm_proxy_openai_gpt_5.5`: 4/4 passed (`c01` skipped - model does not support extended thinking) - `litellm_proxy_anthropic_claude_sonnet_4_6`: 5/5 passed `c02_hard_context_reset` passes through the real `condenser-test` integration runner with an actual LLM on commit `5361a8350`, satisfying issue #5669's acceptance criterion. (The `c04` failure you may see in the gpt-5.5 log is the unrelated, model-dependent `c04` setup that is fixed separately in #5675; it is not part of this PR's change.) _This comment was generated by an AI agent (OpenHands) on behalf of @gneubig

### #5609: Release v1.54.0

- Commit: `1d471742ea7be017d013eaaa3080f5d28d5490cb` (2026-10-09T14:36:37-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5609).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-5609). Families: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34.

Stated intent:

> ## Release v1.54.0
> 
> This PR prepares the release for version **1.54.0**.
> 
> **Started by:** @neubig
> 
> ### Release Checklist
> - [x] Version set to 1.54.0 — four Python packages and TypeScript client synchronized.
> - [x] Fix any deprecation deadlines if they exist — [Deprecation deadlines passed on current head 298227f40](https://github.com/OpenHands/software-agent-sdk/actions/runs/37801967286).
> - [ ] Integration tests pass (tagged with `integration-test`) — [release integration suite passed on prior head e2947c6](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800266455); needs revalidation after merging main. The ordinary TypeScript integration check already [passes on current head](https://github.com/OpenHands/software-agent-sdk/actions/runs/37801967042), but is not a substitute for this release suite.
> - [ ] Behavior tests pass (tagged with `behavior-test`) — [release behavior suite passed on prior head e2947c6](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800267366); needs revalidation on current head.
> - [ ] Example tests pass (tagged with `test-examples

Changed paths:

- `clients/typescript/package-lock.json`
- `clients/typescript/package.json`
- `clients/typescript/src/generated/agent-server-schema.ts`
- `openhands-agent-server/pyproject.toml`
- `openhands-sdk/pyproject.toml`
- `openhands-tools/pyproject.toml`
- `openhands-workspace/pyproject.toml`
- `tests/integration/tests/c04_token_condenser.py`
- `uv.lock`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6063190694)

Review / discussion decisions (chronological evidence retained in raw records):

- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6063184850): ## 🔒 Release Security Scan ### 🔒 Approval drift (time-of-check vs time-of-use) **❌ 7 finding(s)** Baseline: `v1.53.0` — 21 merged PR(s) in range. > ⚠️ 8 first-parent commit(s) in range did not match the `(#N)` squash convention and were **not** audited (a merge/rebase merge, or a lost `(#N)` suffix). These are a blind spot — inspect them: > - Merge remote-tracking branch 'origin/fix/c04-token-condenser-determinism' into rel-1.54.0 > - chore(release): regenerate agent-server contract after main merge > - Merge remote-tracking branch 'origin/main' into rel-1.54.0 > - chore(release): regenerate agent-server contract after main merge > - Merge main into release v1.54.0 > - fix(release): regenerate API contract after merging main > - Merge main into rel-1.54.0 > - Release v1.54.0 \| PR \| finding \| detail \| \| -- \| ------- \| ------ \| \| #
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6063296571): # 🧪 Integration Tests Results **Overall Success Rate**: 100.0% **Total Cost**: $1.96 **Models Tested**: 5 **Timestamp**: 2026-10-08 15:28:09 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800266455) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800266455) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800266455) - **litellm_proxy_deep
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6063438649): Merged current main (c980344bc78e21d5625ec4e987680d87c6c56f7f) into this release branch and regenerated the TypeScript contract in 298227f40. The earlier agent-server-api failure occurred because CI tested the synthetic merge containing the new InstalledCanvasExtensionResponse.requested_ref field while the prepared contract predated that main change. Generation followed by check:agent-server-api now passes, as do all 44 canvas_extensions_router tests and applicable pre-commit hooks. Fresh CI is running. The approval-drift security gate remains unresolved; no gate has been bypassed. Publication and merging remain pending user approval. _This comment was generated by an AI agent (OpenHands) on behalf of the user._
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6063554165): # 🧪 Behavior Tests Results **Overall Success Rate**: 96.0% **Total Cost**: $12.83 **Models Tested**: 5 **Timestamp**: 2026-10-08 15:41:34 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800267366) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800267366) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37800267366) - **litellm_proxy_deepsee
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6064661343): Release checklist follow-up: - Current-head integration/behavior revalidation: https://github.com/OpenHands/software-agent-sdk/actions/runs/37810776941 - Current-head examples retry: https://github.com/OpenHands/software-agent-sdk/actions/runs/37810823497. The prior run passed 68/70; both failures were HTTP 500 creating Cloud sandboxes, not assertion failures. - Required release-note entry #5503 is now explicitly recorded in the checklist, and the automatic preamble query was verified. Approval-drift triage (the security gate remains blocked; no waiver or approval is being manufactured): - #5538: the sole post-approval commit adds Final[str] annotations to two constants: https://github.com/OpenHands/software-agent-sdk/commit/21ebbf4c4c679e0b4b345e4e3b0ce688e540c01e - #5551: only automated removal of .pr/validation.md: https://github.com/O
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6065564481): # 🧪 Integration Tests Results **Overall Success Rate**: 92.6% **Total Cost**: $14.16 **Models Tested**: 5 **Timestamp**: 2026-10-08 17:36:29 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37810776941) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37810776941) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37810776941) - **litellm_proxy_deep
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6080786058): Merged main into the release branch in 3ba77661e, then regenerated the source-derived TypeScript API contract after CI identified drift. The generated change is only the ProfileDetailResponse documentation reflecting the merged X-Expose-Secrets behavior. Source OpenAPI export, generate:agent-server-api, check:agent-server-api, and git diff --check passed locally. Fresh PR CI will validate the updated head. Release integration/behavior/examples and image security validation remain pending; this does not mark those gates passed. Graham reports he personally reviewed the previously flagged approval drift and found it acceptable; the automated audit has not been bypassed or reclassified by this comment. _This comment was generated by an AI agent (OpenHands) on behalf of Graham Neubig._
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6081327482): Combined-candidate validation: locally merged release head 13a6ade18 with #5670 (9079947), #5671 (5f05244), and #5675 (0eaa7a3) in an isolated detached worktree; no release-branch changes or merges of these PRs were pushed. - 233 file-editor/integration-helper/condenser regression tests passed. - Live c02 + c04 both passed on GPT-5.5 (2/2, $0.03) and Claude Sonnet 4.6 (2/2, $0.05). - Source dependency-diff audit against v1.53.0 reports no known OSV vulnerabilities in the added/bumped dependencies. This is NOT an image scan. - Approval-drift scanner still flags the six previously discussed PRs; Graham has reported reviewing them, but the scanner has no comment-based waiver and remains unchanged. - Pip-removal work was canceled at the user’s direction; no pip-removal code is included in this candidate. Full examples CI on #5670 and review o
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6085986678): # 🧪 Integration Tests Results **Overall Success Rate**: 95.9% **Total Cost**: $1.98 **Models Tested**: 5 **Timestamp**: 2026-10-09 17:33:14 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37966674753) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37966674753) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37966674753) - **litellm_proxy_deeps
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6086038002): Release status update after merging current main. **Merged main into the release branch** (6 commits: #5673, #5182 security analysis event, #5683, #5670 file-editor newlines, #5579 ACP model discovery, #5671 c02 hard-reset). The merge was clean (no conflicts). **Regenerated the Agent Server TypeScript contract** (`73d271a02`). The merge added `SecurityAnalysisEvent` and the ACP model-discovery surface, which made the checked-in contract stale; the `agent-server-api` check now passes on the new head (`Checked-in Agent Server contract is current.`). Content-only diff; the pinned-image source header (`ghcr.io/openhands/agent-server:1.54.0-python`) is preserved. **Release gates re-dispatched on the final head `73d271a02`** via their labels: - `integration-test`, `behavior-test`, `test-examples` — running now. - `security-scan` — still fails o
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6086075323): # 🧪 Integration Tests Results **Overall Success Rate**: 100.0% **Total Cost**: $1.95 **Models Tested**: 5 **Timestamp**: 2026-10-09 17:38:57 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37967286572) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37967286572) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37967286572) - **litellm_proxy_deep
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6086285770): # 🧪 Behavior Tests Results **Overall Success Rate**: 96.0% **Total Cost**: $13.47 **Models Tested**: 5 **Timestamp**: 2026-10-09 17:51:23 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37966691792) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37966691792) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37966691792) - **litellm_proxy_deepsee
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6086329272): # 🧪 Behavior Tests Results **Overall Success Rate**: 96.0% **Total Cost**: $11.45 **Models Tested**: 5 **Timestamp**: 2026-10-09 17:53:54 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37967306167) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37967306167) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37967306167) - **litellm_proxy_deepsee
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6086496942): # 🧪 Integration Tests Results **Overall Success Rate**: 100.0% **Total Cost**: $1.84 **Models Tested**: 5 **Timestamp**: 2026-10-09 18:03:56 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37970169569) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37970169569) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37970169569) - **litellm_proxy_deep
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6086718764): # 🧪 Behavior Tests Results **Overall Success Rate**: 100.0% **Total Cost**: $10.47 **Models Tested**: 5 **Timestamp**: 2026-10-09 18:18:21 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37970187854) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37970187854) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37970187854) - **litellm_proxy_deepse
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5609#issuecomment-6086949062): Release gates are green on the current head `e81e8eafc`. **#5675 (c04 token-condenser determinism) merged into the release branch.** The integration-test change (`keep_first=2`, deterministic condensation assertions) is now in `rel-1.54.0`. It is a merge of the reviewed PR branch; per the release skill the release branch is the only thing that ships, and the PR itself is approved (VascoSch92) and clean. **Label-gated suites on `e81e8eafc` — all pass:** - `integration-test` ✅ (all 5 models) - `behavior-test` ✅ (all 5 models) - `test-examples` ✅ - `agent-server-api` ✅ (contract regenerated after the main merge) **Only remaining red check: `security-scan`**, and only its approval-drift half. The findings are the six pre-existing PRs (#5538, #5551, #5357, #5429, #5407, #5263) whose heads changed after their last human approval; the scanner re

### #5682: fix(agent-server): reject blank and redacted secret values

- Commit: `c1c8c1c27824b7566861ea0f8e6900e4ae2944a9` (2026-10-09T21:04:31+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5682).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5682). Families: F18, F19, F32.

Stated intent:

> - Reject blank and redacted-placeholder values with HTTP 422 before persistence.
> - Leave existing secret values and descriptions unchanged when an invalid upsert is rejected.
> - Cover new-secret and existing-secret behavior through router and live-server tests.

Changed paths:

- `openhands-agent-server/openhands/agent_server/settings_router.py`
- `tests/agent_server/test_settings_router.py`
- `tests/cross/test_remote_conversation_live_server.py`

Linked issue acceptance context:

- [#5623](https://github.com/OpenHands/software-agent-sdk/issues/5623): [Bug]: PUT /api/settings/secrets with '' or '**********' answers 200 and nulls the secret instead of 422 — current state closed, closed 2026-10-09T19:04:33Z.

> - [ ] PUT with `value` `""`, whitespace-only or `**********` answers 422 without changing the store, or (placeholder over an existing name) keeps the stored value. - [ ] No sequence of PUTs leaves a name that is listed but whose read answers 404. - [ ] Router tests cover the placeholder over an existing secret and an empty value for a new name. - [ ] The `F18.secret-empty-value` bullets in `F18-settings-and-secrets.md` pass with their known-bug markers removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context Root cause: - `openhands-agent-server/openhands/agent_server/settings_router.py:526` (`create_secret`) validates only the name and passes `secret.value.get_secret_value()` to `store.set_secret` (line 540) unchecked; `SecretCreateRequest.value` (`openhands-sdk/openhands/sdk/settings/api_models.py:260`) is a bare `SecretStr`. - `FileSecretsStore.set_secret` (`openhands-agent-server/openhands/agent_server/persistence/store.py:612-616`) builds `CustomSecret(secret=SecretStr(value))`. `CustomSecret._validate_secret` (`persistence/models.py:505-510`) calls `validate_secret`, which maps empty, whitespace-only and `**********` to `None` (`openhands-sdk/openhands/sdk/utils/pydantic_secrets.py:108`). The entry is saved with `secret: null` and the new description. - `get_secret` returns `None` for a null value (`store.py:585`), so the read route raises 404 (`settings_router.py:516`), while `list_secrets` lists every entry (`settings_router.py:484-488`). Suggested fix: in `create_secret`, answer 422 when the value is blank or

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5682#pullrequestreview-5474248198): Thank you!

### #5614: ci: upgrade integration matrix to DeepSeek V4.1 Flash

- Commit: `9929c14e7d2f4882cd257de2a401235ef276b865` (2026-10-09T15:43:04-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5614).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5614). Families: no product runtime required.

Stated intent:

> - Replace the default DeepSeek model ID, display name, and litellm_proxy model mapping with V4.1 Flash.
> - Update integration coverage documentation; other models and condenser defaults are unchanged.

Changed paths:

- `.github/workflows/integration-runner.yml`
- `tests/integration/README.md`

Linked issue acceptance context:

- [#5613](https://github.com/OpenHands/software-agent-sdk/issues/5613): Upgrade integration test matrix to DeepSeek V4.1 Flash — current state closed, closed 2026-10-09T19:43:06Z.

> - [ ] `DEFAULT_MODEL_IDS` in `.github/workflows/integration-runner.yml` contains `deepseek-v4.1-flash` and no longer contains `deepseek-v4-flash`. - [ ] The inline `MODELS` map resolves the `deepseek-v4.1-flash` id to display name `DeepSeek V4.1 Flash` and proxy model `litellm_proxy/deepseek/deepseek-v4.1-flash`. - [ ] The other default model ids (`gpt-5.5`, `minimax-m2.7`, `gemini-3.1-pro`, `claude-sonnet-4-6`) and `DEFAULT_CONDENSER_MODEL_IDS` (`gpt-5.5`, `claude-sonnet-4-6`) are unchanged. - [ ] `tests/integration/README.md` names DeepSeek V4.1 Flash (not V4 Flash) in the coverage list and still reports 5 models. - [ ] An automated test invokes the actual matrix resolver (not a duplicated copy of the table) and asserts that with no `model_ids` the default set includes `deepseek-v4.1-flash` with display name `DeepSeek V4.1 Flash` and proxy model `litellm_proxy/deepseek/deepseek-v4.1-flash`, and that an explicit `model_ids=deepseek-v4.1-flash` selection resolves to that same entry. - [ ] The resolver retains its fail-fast behavior: an unknown model id still exits non-zero with the `Unknown model id` message. - [ ] No other `deepseek-v4-flash` reference in the repository changes, and no already-running or historical evaluation run/artifact is renamed or regenerated.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5614#issuecomment-6065470608)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5614#pullrequestreview-5460920183): _This review was posted by an AI agent (OpenHands)._ ## Review: `ci: upgrade integration matrix to DeepSeek V4.1 Flash` **Scope gate:** In scope. This is an in-repo CI/eval configuration and documentation change owned by this repository. No cross-repo or product/architecture decision required. **Verification performed on head `a8e5ba425e9e72e77a1290aff70873da5b7205ff`:** - Extracted the actual inline matrix resolver from `.github/workflows/integration-runner.yml` and executed it: - Default set (`DEFAULT_MODEL_IDS`) resolves `deepseek-v4.1-flash` to display name `DeepSeek V4.1 Flash`, proxy model `litellm_proxy/deepseek/deepseek-v4.1-flash`, and run-suffix `deepseek_v4_1_flash_run`. - Explicit `model_ids=deepseek-v4.1-flash` resolves to the same entry. - The default set no longer contains `deepseek-v4-flash`. - Fail-fast behavior preserved

### #5675: test(integration): make token condensation deterministic

- Commit: `1cfba2117e92730b0fe1c32520a39aad8611bb8c` (2026-10-09T15:47:33-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5675).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5675). Families: no product runtime required.

Stated intent:

> - Replace the 1,000-call instruction with two separate Python terminal commands, each printing 2,000 repetitions of a short phrase.
> - Raise the token threshold to 6,000 and keep the seeded context small so a single tool result clears it by a wide margin on every CI model.
> - Set `keep_first=2` so the user instruction survives the first condensation and the second command still runs.
> - Raise the iteration cap from 10 to 20 so the two condensations, the summarizer calls they trigger, and the tool turns all fit within budget.
> - Require at least two condensations in `verify_result`, so the second cycle is actually enforced rather than merely observed.
> - Remove the obsolete long-loop refusal skip.

Changed paths:


Linked issue acceptance context:

- [#5674](https://github.com/OpenHands/software-agent-sdk/issues/5674): Make c04 token-condenser evaluation deterministic across models — current state closed, closed 2026-10-09T19:47:35Z.

> - [ ] `c04_token_condenser` no longer depends on the model volunteering a long repetitive tool loop: the `1..1000` echo instruction is replaced with a deterministic mechanism (or model-independent instruction) that reliably builds a view exceeding the token threshold on every supported matrix model. - [ ] Before asserting condensation, the test measures the active view with the agent LLM's real tokenizer through the repository's existing token-count path (e.g. `get_total_token_count(view.events, agent_llm)`) and confirms it exceeds the condenser's effective `max_tokens` (5000); a below-threshold view fails the test with a clear reason rather than a misleading "token counting may not work" message. - [ ] The test exercises the normal agent/condenser path with real LLM calls (agent run + `LLMSummarizingCondenser`), not only a synthetic `View`. - [ ] `verify_result()` fails when no `Condensation` is produced, and when condensation occurs it succeeds only if the condensation is token-driven and summarizes the oversized event(s) (e.g. non-empty `forgotten_event_ids` including the deliberately oversized event). - [ ] The condenser keeps `max_size` high enough that event count alone cannot trigger condensation, so the test specifically exercises token-based condensation. - [ ] Running the `condenser-test` workflow on the pull request reports `c04_token_condenser` passing on both `gpt-5.5` and `claude-sonnet-4-6`; a run where it fails on either model does not satisfy this. - [ ] `c01_thinking_block_condenser`, `c02_hard_context_reset`, `c03_delayed_condensation`, and `c05_size_cond

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5675#issuecomment-6081241320)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5675#pullrequestreview-5471430585): _This review was posted by an AI agent (OpenHands)._ ### Scope This change is correctly placed: it only edits the c04 condenser integration test (`tests/integration/tests/c04_token_condenser.py`), matching the scope in linked issue #5674 (labels `bug`, `priority:medium`, `ready-for-dev`). No runtime condenser, tokenizer, packaging, or public API changes. ### Assessment The revision addresses the reported root cause. The instruction no longer asks the model to volunteer a 1,000-iteration echo loop; it issues two bounded terminal commands (`'token pressure context ' * 1000` and `'second context payload ' * 1000`) that produce deterministic, model-independent output. At ~4 tokens per repeated word and >1,000 words per call, each command's tool result alone exceeds the retained `max_tokens=5000`, so condensation is driven by token count, not 
- [reviews; COMMENTED; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5675#pullrequestreview-5472337886): a couple of comments
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4231978286): A condensation step also counts as an iteration: `agent.step` returns early after emitting the `Condensation` (agent.py:754-756), and the loop still does `iteration += 1` (local_conversation.py:2000). The happy path is therefore already 4 of 10, and hitting the cap ends the run with `MaxIterationsReached`. Something like 20 would still be far below the old 50 while leaving room for a retry or a narrated step.
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4231979941): **L61** (https://github.com/OpenHands/software-agent-sdk/blob/dc88f65bd57a902359b76372def17ec8063bbff9/tests/integration/tests/c04_token_condenser.py#L61): `keep_first=1` only protects index 0, which is the `SystemPromptEvent`, not the user message, so the inline comment is wrong. I ran `_get_forgotten_events` offline on the post-command-1 view (`[SystemPrompt, UserInstruction, Action1, Observation1]`) with both the gpt-5.5 and sonnet-4-6 tokenizers. The first condensation forgets `UserInstruction`, `Action1` and `Observation1`, and keeps only the system prompt. Command 2 then survives only if the LLM summary happens to keep it. The old 1,000-echo loop could tolerate losing the instruction; a two-step script can't. Suggestion: `keep_first=2`, so the instruction always survives condensation.
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4231980071): **L85** (https://github.com/OpenHands/software-agent-sdk/blob/dc88f65bd57a902359b76372def17ec8063bbff9/tests/integration/tests/c04_token_condenser.py#L85): `verify_result` still passes with a single condensation, so the second cycle (the reason for the redesign) is never asserted. The PR body says the live traces show two cycles, but CI won't catch a regression where the agent stops after the first summary. Since the callback already pauses on the 2nd condensation, could we require `len(self.condensations) >= 2` here?
- [review_comments; comment; VascoSch92](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4231980258): **L60** (https://github.com/OpenHands/software-agent-sdk/blob/dc88f65bd57a902359b76372def17ec8063bbff9/tests/integration/tests/c04_token_condenser.py#L60): The headroom before the first tool call is very small. I measured the real Agent + TerminalTool system prompt plus this instruction, using litellm's token counting for each CI matrix model: - gpt-5.5: 4,839 (161 left) - MiniMax-M2.7: 4,831 (169 left) - sonnet-4-6 / opus-5: 4,663 (337 left) - gemini-3.1: 4,642 (358 left) - deepseek / kimi: 4,590 (410 left) These numbers match your 7,985 total after command 1 (one payload is about 3k tokens). If the system prompt or tool description grows by about 160 tokens, condensation will fire on step 1, before any tool call, and the test breaks for an unrelated reason. Could we raise the threshold (e.g. 6,000) and size the payloads to match, so nei
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4232310113): Raised the cap to 20 in 772e42130, leaving headroom above the ~4 iterations the happy path already uses (condensation steps advance the counter too).
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4232310510): Set keep_first=2 in 772e42130 so the user instruction survives the first condensation and the second command still runs.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4232310812): verify_result now requires at least two condensations in 772e42130. Confirmed locally: GPT-5.5 and Sonnet 4.6 both report two condensations.
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5675#discussion_r4232311183): Raised max_tokens to 6000 and the payloads to 2,000 reps in 772e42130. One tool result is ~9k tokens, so it clears the threshold by a wide margin on every CI model.
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5675#issuecomment-6081339561): # 🧪 Condenser Tests Results **Overall Success Rate**: 77.8% **Total Cost**: $1.00 **Models Tested**: 2 **Timestamp**: 2026-10-09 12:56:50 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37932649746) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37932649746) ## 📊 Summary \| Model \| Overall \| Tests Passed \| Skipped \| Total \| Cost \| Tokens \| \|-------\|---------\|--------------\|---------\|-------\|------\|--------\| \
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5675#issuecomment-6082373755): # 🧪 Condenser Tests Results **Overall Success Rate**: 77.8% **Total Cost**: $0.96 **Models Tested**: 2 **Timestamp**: 2026-10-09 13:56:24 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37939597084) - **litellm_proxy_anthropic_claude_sonnet_4_6**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/37939597084) ## 📊 Summary \| Model \| Overall \| Tests Passed \| Skipped \| Total \| Cost \| Tokens \| \|-------\|---------\|--------------\|---------\|-------\|------\|--------\| \

### #5291: fix(llm): don't send stop words to OpenAI models on Bedrock

- Commit: `e2bac66be26e94f571f27b71f507e7192720f825` (2026-10-10T12:33:35+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5291).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5291). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - Add `"openai.gpt-"` to `SUPPORTS_STOP_WORDS_FALSE_MODELS`, next to o1/o3/grok-4.
> - Tests: five Bedrock ids that must drop stop words, one Claude-on-Bedrock id that keeps them.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/utils/model_features.py`
- `tests/sdk/llm/test_model_features.py`

Linked issue acceptance context:

- [#5290](https://github.com/OpenHands/software-agent-sdk/issues/5290): [Bug]: Prompt-based tool calling fails on Bedrock OpenAI models (gpt-oss, GPT-5.6, GPT-6 Sol/Luna/Astra) because stop words are sent — current state closed, closed 2026-10-10T10:33:40Z.

> - [ ] `get_features(...).supports_stop_words` is `False` for `bedrock/openai.gpt-oss-120b-1:0`, `bedrock/us.openai.gpt-5.6-sol` and `bedrock/{us,global}.openai.gpt-6-{sol,luna,astra}` - [ ] The repro no longer returns the `stopSequences` 400 - [ ] Claude on Bedrock still gets the stop word ### Installation Method uv sync on main (LiteLLM 1.93.0 from `uv.lock`) ### SDK Version main @ 5b36cac (openhands-sdk 1.49.5) ### Python Version 3.13.14 ### Model Name (if applicable) bedrock/openai.gpt-oss-120b-1:0, bedrock/us.openai.gpt-5.6-sol, bedrock/us.openai.gpt-6-sol, bedrock/us.openai.gpt-6-luna, bedrock/us.openai.gpt-6-astra ### Operating System MacOS ### Minimal Code Sample ```python from openhands.sdk import LLM, Message, TextContent from openhands.sdk.tool.builtins.think import ThinkTool # reasoning_effort=None: the locked LiteLLM 1.93.0 would otherwise turn the # default "high" into a `thinking` field that GPT-5.6/GPT-6 reject. MODELS = { "bedrock/openai.gpt-oss-120b-1:0": {}, "bedrock/us.openai.gpt-5.6-sol": {"reasoning_effort": None}, "bedrock/us.openai.gpt-6-sol": {"reasoning_effort": None}, "bedrock/us.openai.gpt-6-luna": {"reasoning_effort": None}, "bedrock/us.openai.gpt-6-astra": {"reasoning_effort": None}, "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0": {}, } msgs = [ Message(role="system", content=[TextContent(text="You are an agent. Use the think tool once, then answer.")]), Message(role="user", content=[TextContent(text="Call the think tool with thought='2+2', then tell me the result.")]), ] for model, extra in MODELS.items(): llm = LLM(model=model, usage_id

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5291#pullrequestreview-5417452375): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope. `openhands-sdk/openhands/sdk/llm/utils/model_features.py` is exactly where repo guidance places model/provider capability tweaks, so the change belongs in this repository. ## What the change does `"openai.gpt-"` is added to `SUPPORTS_STOP_WORDS_FALSE_MODELS`. `model_matches` performs a case-insensitive substring search over the raw model id, so dotted Bedrock ids (`bedrock/us.openai.gpt-6-sol`, `bedrock/openai.gpt-oss-120b-1:0`) now resolve `supports_stop_words=False`, while the slash form `openai/gpt-5` and bare `gpt-5` are unchanged. The runtime consumer is `mixins/non_native_fc.py:59`, which only adds `stop` when the feature is true, so the Bedrock Converse `stopSequences` field is no longer populated for these models. That matches the root cause in #5290. ## Verif

### #5661: fix(agent-server): keep sockets and /v1 closed until deferred init completes

- Commit: `c4b93299cf2b31c8b5d0b2c5a89965ccc5ff24f9` (2026-10-10T18:34:26+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5661).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5661). Families: F01, F03, F05, F30, F31, F32.

Stated intent:

> - `require_initialized` takes an `HTTPConnection`, so the same dependency resolves on HTTP and WebSocket routes. HTTP requests still get a 503 while dormant; WebSockets get `WebSocketException(1013 Try Again Later)`, which closes the socket before the handshake is accepted. (Raising `HTTPException` from a WebSocket dependency would reach the app's HTTP exception handler, which reads `request.method` and fails.)
> - The gate is attached to the `/v1` router (after `check_openai_api_key`, the same order as `/api`) and to the sockets router at its mount point, covering the events, session and bash sockets for both the local and Docker registries.
> - Initialized and non-deferred servers behave exactly as before: the dependency returns immediately once init is `ready` or when no `InitService` is registered.

Changed paths:

- `openhands-agent-server/openhands/agent_server/api.py`
- `openhands-agent-server/openhands/agent_server/init_router.py`
- `tests/agent_server/test_init_router.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5661#issuecomment-6076160145)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5661#pullrequestreview-5479845130): LGTM — reviewed the diff and the surrounding router wiring. The fix is correct and well-scoped. A few notes: **What I verified** - Switching `require_initialized` to `HTTPConnection` is the right call: the same dependency now resolves on both HTTP and WebSocket routes, and raising `WebSocketException(1013)` (instead of `HTTPException`) avoids the app-level exception handler that reads `request.method` — good catch. - Applying the gate at the router mount points (`openai_router` and `conversation_registry.sockets_router`) means the events / session / bash sockets are all covered for both the local and Docker registries, not just the individual routes. - The dependency ordering matches the existing `/api` group (`check_openai_api_key` before `require_initialized`), so a dormant pod without the key still gets 401 rather than leaking 503 sema

### #5703: fix(llm): keep verified OpenRouter models discoverable

- Commit: `c96875fe3d033efc44b5e6403043251626dd61ef` (2026-10-11T01:57:03+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5703).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5703). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - Include curated, verified OpenRouter routes in SDK model discovery with their `openrouter/` prefix, independently of LiteLLM's cost map.
> - Exercise the HTTP filter with fixed catalogs both containing and omitting OpenRouter entries; preserve catalog-only routes, sorting, deduplication, and provider isolation.
> - Replace live-catalog model and capability assertions with SDK contract checks, correct curation guidance, and move an MCP test Action to module scope so it cannot invalidate later event deserialization.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/utils/AGENTS.md`
- `openhands-sdk/openhands/sdk/llm/utils/unverified_models.py`
- `openhands-sdk/openhands/sdk/llm/utils/verified_models.py`
- `tests/agent_server/test_llm_router.py`
- `tests/sdk/llm/test_model_features.py`
- `tests/sdk/llm/test_model_list.py`
- `tests/sdk/tool/test_mcp_schema.py`

Linked issue acceptance context:

- [#5704](https://github.com/OpenHands/software-agent-sdk/issues/5704): [Bug]: OpenRouter discovery and tests break after remote LiteLLM catalog removal — current state closed, closed 2026-10-10T23:57:05Z.

> - [ ] Curated OpenRouter routes appear in both the complete and provider-filtered model lists when LiteLLM supplies no OpenRouter catalog entries. - [ ] Filtered results retain their route prefix, exclude direct-provider collisions, and remain sorted and deduplicated. - [ ] Additional OpenRouter catalog entries remain discoverable when supplied by LiteLLM. - [ ] Model discovery and capability-forwarding tests do not pin changing OpenRouter catalog contents. - [ ] The full SDK and Agent Server CI suites pass. ### Installation Method Workspace editable installation via `make build` (`uv sync --dev`). ### SDK Version Main commit `c4b93299c`, package version 1.54.0; LiteLLM 1.93.0. ### Version Confirmation - [x] I have confirmed this bug exists on the latest SDK main available when reproducing it. ### Python Version Python 3.13.11 locally; Python 3.13.16 in CI. ### Model Name `openrouter/deepseek/deepseek-chat` ### Operating System macOS locally and Linux in CI. ### Logs and Error Messages ```text assert len(response.models) > 0 E assert 0 > 0 E where [] = ModelsResponse(models=[]).models ``` ### Evidence and Additional Context The regression was investigated while examining https://github.com/OpenHands/software-agent-sdk/pull/5700 and reproduced independently on main. A fix with live before/after HTTP evidence and deterministic regression coverage is available in https://github.com/OpenHands/software-agent-sdk/pull/5703. The fixed endpoint lists 21 curated OpenRouter routes against the actual remote catalog. ---

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5703#pullrequestreview-5481241687): ## Review - code looks good; one merge gate to clear Thanks for the fix. I read the 7-file diff plus the surrounding context (`unverified_models.get_supported_llm_models`, `agent_server/llm_router.list_models`, `verified_models`, and the OpenRouter runtime adapter). The approach is right: injecting `openrouter/<verified>` independently of `litellm.model_list`/`model_cost` restores discovery without tying curated routes to a catalog upstream can drop, and keeping the prefix preserves downstream provider derivation and context-window resolution via the endpoints API. No correctness concerns from me on the diff. A few notes, none blocking: 1. `_extract_model_and_provider` only recognizes `openrouter/...` when `openrouter` is present in `litellm.provider_list` (`_LITELLM_PROVIDER_NAMES`). The fix now depends on LiteLLM keeping the *provider* 
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5703#pullrequestreview-5481247486): Re-reviewed after the update. The `Validate PR description` gate is now green on head `282fc998` (issue #5704 carries `ready-for-dev` and is linked as `Fixes #5704`), and the full check suite on the head is green (sdk-tests, agent-server, cross, binary builds, pre-commit, etc.). My earlier review notes were non-blocking; approving. _This review was created by an AI agent (OpenHands) on behalf of the user._

### #5700: fix(agent-server): gate ingress during deferred initialization

- Commit: `f824a949af7f8045cc6691d7945ce7b31bfe9606` (2026-10-11T00:12:11Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5700).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5700). Families: F01, F03, F05, F30, F31, F32.

Stated intent:

> - Run the deferred-initialization gate before authentication on `/api/*`,
>   `/v1/*`, and workspace-file routes.
> - Apply the same gate to all Canvas backend ingress, including session, HTTP
>   proxy, and WebSocket routes.
> - Add deterministic transition-race coverage plus a real Uvicorn/wsproto
>   lifecycle regression.

Changed paths:

- `openhands-agent-server/openhands/agent_server/api.py`
- `tests/agent_server/test_init_router.py`
- `tests/cross/test_remote_conversation_live_server.py`

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5700#pullrequestreview-5480149326): Reviewed the diff against the merged #5661 baseline (main is now at `c4b9329`, the #5661 merge). The change itself looks correct. **What's good** - Correctly reuses the shared `require_initialized` dependency rather than adding a WS-specific variant, and applies it to both `/app-backends/{extension_name}` route forms — covering the Canvas ingress that #5661 left out. - The dependency's `WebSocketException(code=1013)` path is exercised end-to-end (dormant handshake refused, then accepted after `/api/init`), and the test correctly accounts for the observable difference between `TestClient` (close code 1013) and a real uvicorn/wsproto handshake (HTTP 403 surfaced as `InvalidStatus`). - Asserting the bash socket flow still works after init (auth + command round-trip) is a good guard against the gate over-blocking. **Blocking / please address*
- [reviews; APPROVED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5700#pullrequestreview-5480159558): Thank you!
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5700#pullrequestreview-5481254483): _This review was posted by an AI agent (OpenHands)._ ## Review of `ef4e0e6d8f2bfd4f3e56f9bcab105f5dbbaf4a0a` **Scope:** In scope for this repository. This is Agent Server ingress gating for deferred initialization, following #5661 and part of the #5688 hardening umbrella (currently `ready-for-dev`, `security-related`, `priority:medium`). No cross-repo or product decision is needed. **What I verified in the code** - The deferred-init gate now runs before credential evaluation on `/api/*`, `/v1/*`, and the workspace static-file routes (`api.py`). Because `check_session_api_key`, `check_openai_api_key`, and `check_workspace_session` read `app.state.config` at request time, ordering `require_initialized` first fixes the previously documented trade-off where keys delivered by `POST /api/init` were not honored. The comment that documented that 
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5700#issuecomment-6103115015): ### ⚠️ Review paused: current-head checks did not pass The current head `ef4e0e6d8f2b` has checks that did not pass, so no review conversation was started: - `agent-server-tests` Fix the checks above and push. The scheduled scan then starts the review on the updated head. _This is an automated check - no AI was used to generate this comment._
- [discussion; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5700#issuecomment-6103119500): @all-hands-bot the failing CI is on main, we will have to wait for that to be fixed.

### #4838: fix(mcp): recover after HTTP session failure

- Commit: `4cc058275c5a08d845bd846576aafc80e5dd7d0d` (2026-10-11T02:20:14Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/4838).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-4838). Families: F19.

Stated intent:

> - Reset a completed FastMCP session under its existing session lock before reconnecting.
> - Add a real HTTP regression test that injects one 504 response and verifies the next tool call succeeds.

Changed paths:

- `openhands-sdk/openhands/sdk/mcp/client.py`
- `tests/sdk/mcp/test_create_mcp_tool.py`

Linked issue acceptance context:

- [#4837](https://github.com/OpenHands/software-agent-sdk/issues/4837): [Bug]: MCP reconnect remains broken after transient HTTP error — current state closed, closed 2026-10-11T02:20:16Z.

> - [ ] After an HTTP error terminates the FastMCP session task, the next MCP tool call reconnects and succeeds. - [ ] Existing MCP client tests continue to pass. ### Installation Method `uv sync` from the repository checkout. ### SDK Version Current `main` at `e26683288ab4dd69518810016b74682de2a8c4e4`. ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version 3.13.7 ### Operating System Linux ### Logs and Error Messages ```text First call: 504 Gateway Timeout Second call: MCP Connection Failure Cause: RuntimeError: Internal error: nesting counter should be 0 when starting new session, got 1 ``` ### Minimal Code Sample The linked PR includes an integration test that starts a local FastMCP HTTP app, injects one 504 response, and makes two calls through the real SDK tool executor. ### Screenshots and Additional Context Draft PR #3444 identified the same FastMCP stale-counter state on an older SDK architecture. Current `main` now has automatic reconnect in the tool executor; this report covers that current path and uses a real HTTP failure rather than mocked session state. The failure reproduces with the repository's locked FastMCP 3.2.0 and also with 3.4.7. It does not reproduce with FastMCP 4.0.2, whose session implementation changed, but adopting a breaking major version is separate from preserving reconnect behavior for the currently supported FastMCP 3.x dependency.

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4838#pullrequestreview-5114975027): _This review was created by an AI agent (OpenHands) on behalf of the repository maintainers._ ## Summary This PR fixes a real bug in the MCP client reconnect path. When a transient HTTP error kills FastMCP's background session task, `_context_manager`'s `finally` block calls `_reset_session_state()` without `full=True`, which clears `session` and `initialize_result` but leaves `nesting_counter` at 1 and `session_task` pointing to the completed task. The SDK's `call_tool` then sees `is_connected() == False` and calls `connect()`, but FastMCP's `_connect()` refuses to start a new session because `nesting_counter != 0`, raising `RuntimeError` -> `MCPError("MCP Connection Failure")`. The fix correctly resets the stale state under the existing session lock before calling `__aenter__()`. I verified the fix by tracing through FastMCP 3.4.7's `_c
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4838#pullrequestreview-5465952177): _This review was posted by an AI agent (OpenHands)._ ## Summary This is a fresh review of head `3a5533d`; the earlier review by this account was on the superseded commit `f608a6a`. The production change is 4 lines in `MCPClient.connect()` plus a real-HTTP regression test. I traced the FastMCP 3.2.0 internals used by the pinned dependency (`fastmcp/client/client.py`). The mechanism is as described: a streamable-HTTP error raised while `_context_manager()` is entered tears down the session task through its `finally: self._reset_session_state()` (not `full=True`), which clears `session`/`initialize_result` but leaves `session_task` pointing at a completed task and `nesting_counter == 1`. The SDK then sees `is_connected() == False` and calls `connect()`, but `_connect()` raises `RuntimeError("nesting counter should be 0 when starting new sess
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/4838#pullrequestreview-5481594012): 🟢 **Good taste — approved.** Thank you for the focused fix and real HTTP regression coverage. Reviewed head `3a5533d0da12bbdd2e7eefdcc555b53f7e56f5b3`: the completed-session reset uses FastMCP’s existing lock and reset primitive, and satisfies the scoped acceptance criteria in #4837. Independent runtime checking reproduced the failed second tool call with the pre-fix method, then passed three times with the exact PR method against a real local FastMCP HTTP server (first call: 504; second call: pong). This used the installed SDK/FastMCP 3.4.8 with in-memory method substitution, not full base/head checkouts or an LLM-driven task. Current-head implementation CI is green. No material correctness finding remains. **Risk: LOW** — narrow dead-session recovery change; no dependency or public-contract changes. **Verdict: worth merging.** _This rev
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4838#issuecomment-5535237821): 🚦 **CI is currently failing on this PR's latest commit.** Please fix the failing checks before OpenHands reviews it - this is re-checked automatically once you push a new commit. (A maintainer can also request `@all-hands-bot` as a reviewer to have it reviewed regardless of CI status.) _This is an automated check - no AI was used to generate this comment._
- [discussion; comment; HussainShibli](https://github.com/OpenHands/software-agent-sdk/pull/4838#issuecomment-5570718803): We reproduced this exact class of failure live with an SSE MCP "desktop bridge" (each conversation holds its own SSE session to a desktop app; when the bridge restarts, **existing** conversations fail permanently with `Connection closed` / nesting-counter-stuck errors, while brand-new conversations always work). One nuance our testing surfaced that may be relevant here: the drop can surface in **two** ways - (a) as a raised `MCPError` / `Connection closed`, and (b) as an **error tool-result** when calls fan out through the in-process fastmcp gateway (a dead remote session is returned as `CallToolResult(isError=true, text="Error calling tool '...': Connection closed")` rather than raised). Reconnect/retry logic keyed only on raised exceptions will not cover (b). Environment: openhands-sdk / openhands-agent-server 1.42.1; 1.44.1 and current

### #5436: fix: support GPT-6 Sol, Luna and GPT-6.1 Sol subscriptions

- Commit: `3934349da2db0433238621542173a00a8af7439c` (2026-10-11T13:26:34Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5436).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-5436). Families: F04, F06, F07, F09, F20, F22, F23, F24, F30, F33, F34.

Stated intent:

> - Add `gpt-6.1-sol`, `gpt-6-sol`, and `gpt-6-luna` to the ChatGPT subscription allowlist.
> - Keep subscription models separate from the Codex ACP model catalog. Current `main` already decouples these surfaces, so the PR no longer advertises models that the pinned ACP adapter rejects.
> - Verify the Agent Server subscription-model endpoint exposes the full allowlist.
> - Parameterize subscription LLM construction coverage over every supported subscription model.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/auth/openai.py`
- `tests/agent_server/test_llm_router.py`
- `tests/sdk/llm/auth/test_openai.py`

Linked issue acceptance context:

- [#5440](https://github.com/OpenHands/software-agent-sdk/issues/5440): [Feature]: Support GPT 6 Sol, 6 Luna and 6.1 Sol — current state closed, closed 2026-10-11T13:26:36Z.

> - [ ] GPT-6 Sol, GPT-6 Luna, and GPT-6.1 Sol all work with the OpenAI provider - [ ] Existing models continue to work. ### Alternatives Considered _No response_ ### Priority / Severity Medium - Would improve experience ### Estimated Scope Unknown - Not sure about the technical complexity ### Feature Area - [ ] Agent API / Core functionality - [ ] Tools / Tool system - [ ] Skills / Plugins - [ ] Agent Server - [ ] Workspace management - [x] Configuration / Settings - [ ] Examples / Templates - [ ] Documentation - [ ] Testing / Development tools - [ ] Performance / Optimization - [ ] Integrations (GitHub, APIs, etc.) - [ ] Other ### Technical Implementation Ideas (Optional) _No response_ ### Additional Context _No response_ ---

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5436#pullrequestreview-5402707467): _This review was posted by an AI agent (OpenHands)._ ## Assessment The core change is small, focused, and correct in direction: adding `gpt-6.1-sol`, `gpt-6-sol`, and `gpt-6-luna` to `_CODEX_MODELS` in `openhands-sdk/openhands/sdk/settings/acp_providers.py` makes them flow into `OPENAI_CODEX_MODELS` (via `_get_current_codex_model_ids()`), which is exactly the allowlist that `OpenAISubscriptionAuth.create_llm()` checks before contacting OpenAI. The parameterized factory test passes, and I reproduced `91 passed` locally for `tests/sdk/llm/auth/test_openai.py` and `tests/sdk/settings/test_acp_providers.py`, plus `29 passed` for `tests/sdk/llm/test_model_list.py` and `tests/agent_server/test_llm_router.py`. However, the change is incomplete: it updates the Python source of truth for the ACP provider registry without mirroring the same change 
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5436#pullrequestreview-5439456863): _This review was posted by an AI agent (OpenHands)._ ## Assessment The intent is right, and the lever is the correct one: `OPENAI_CODEX_MODELS` is derived from the Codex ACP registry (`_CODEX_MODELS` -> `get_acp_provider("codex").available_models`), so adding models there is the way to widen the subscription allowlist. But the three newly added Codex ids are not accepted by the pinned live adapter, so the change does not meet the linked issue's acceptance criteria and breaks a repository CI gate on this head. ## Blocking finding: the three new Codex ids are rejected by the live adapter CI gate `acp-live-tests` (run 37486259371, job 112526543725) fails on this exact head: ```text FAILED tests/sdk/agent/test_acp_conformance.py::test_acp_conformance_probe[codex] E ValueError: ACP server rejected set_config_option(model)(model='gpt-6.1-sol'):
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5436#discussion_r4174825790): Adding these ids to `_CODEX_MODELS` also changes `ACP_PROVIDERS["codex"].available_models`, which is mirrored to `clients/typescript/src/models/acp-providers.json`. That mirror was not updated, so the `validate-acp-providers` job in `.github/workflows/typescript-client-ci.yml` fails on this head: ``` $ OPENHANDS_SUPPRESS_BANNER=1 uv run python clients/typescript/scripts/check-acp-drift.py ERROR: src/models/acp-providers.json has drifted from openhands-sdk. EXIT CODE: 1 ``` `clients/typescript/src/models/acp.ts` documents the contract ("edit `acp_providers.py` first, then mirror the change … CI will fail until the two match"). Regenerate and commit the mirror: ```bash OPENHANDS_SUPPRESS_BANNER=1 uv run python clients/typescript/scripts/check-acp-drift.py --write ```
- [review_comments; comment; viblo-majority](https://github.com/OpenHands/software-agent-sdk/pull/5436#discussion_r4192199684): Fixed in 437e0feb: regenerated the TypeScript ACP registry mirror and verified that `check-acp-drift.py` passes. Also added GPT-6.1 Sol to the verified OpenAI list with catalog/API coverage. 120 relevant tests and all applicable pre-commit checks pass. _This reply was generated by OpenHands on behalf of the contributor._
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5436#discussion_r4204571414): Same live-adapter check the repo already runs (`acp-live-tests`) rejects this exact id on the current head: ```text FAILED tests/sdk/agent/test_acp_conformance.py::test_acp_conformance_probe[codex] ValueError: ACP server rejected set_config_option(model)(model='gpt-6.1-sol'): Invalid params ``` Reproduced locally against the pinned `@agentclientprotocol/codex-acp@1.10.0`: `gpt-6.1-sol`, `gpt-6-sol` and `gpt-6-luna` are all rejected from `_CODEX_MODELS`, while `gpt-6-astra`, the `gpt-5.6-*` entries and `gpt-5.5` are accepted. These are the "dead entries the live server's `set_config_option` rejects" the comment above `_CODEX_MODELS` describes. Because `OPENAI_CODEX_MODELS` is derived from this tuple, the rejected ids propagate into the subscription allowlist and the codex ACP picker while remaining unusable, and the live conformance gate f
- [review_comments; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5436#discussion_r4241694531): Addressed in 931649fe5. Current main decoupled the ChatGPT subscription allowlist from the Codex ACP catalog in #5579. This PR now adds the three models only to `OPENAI_CODEX_MODELS` and its Agent Server endpoint/factory tests; it no longer changes `_CODEX_MODELS`, the TypeScript ACP mirror, or verified provider lists. The live ACP conformance failure is therefore avoided while preserving the contributor’s successful subscription Responses probes. _This reply was generated by an AI agent (OpenHands) on behalf of the contributor._

### #5715: fix(terminal): notice a finished command within milliseconds instead of at the next 0.5 s poll

- Commit: `3dcb27b227aa177f6a7eadb0eb144339245cbbc4` (2026-10-11T18:51:05-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5715).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5715). Families: F13, F27, F33.

Stated intent:

> - The poll interval starts at `MIN_POLL_INTERVAL` (0.02 s) and grows with the command's run time (`elapsed / 4`) up to `POLL_INTERVAL` (0.5 s).
>   - A short command is noticed within a few tens of milliseconds.
>   - A command running longer than 2 s is polled every 0.5 s, as before.
>   - No command duration waits longer than it does today.
> - New test: the first poll is under half of `POLL_INTERVAL`, intervals never shrink, and a 2.5 s command backs off to `POLL_INTERVAL`. It runs on both the tmux and subprocess backends.

Changed paths:

- `openhands-tools/openhands/tools/terminal/constants.py`
- `openhands-tools/openhands/tools/terminal/terminal/terminal_session.py`
- `tests/tools/terminal/test_terminal_session.py`

Linked issue acceptance context:

- [#5714](https://github.com/OpenHands/software-agent-sdk/issues/5714): [Bug]: Terminal returns short commands only at the next 0.5 s poll — current state closed, closed 2026-10-11T22:51:06Z.

> - [ ] A command that finishes in under 0.1 s returns without waiting for a 0.5 s poll, on both the tmux and subprocess backends. - [ ] Commands that run for several seconds are not polled more often than today (each tmux poll spawns `tmux capture-pane`). ### Installation Method uv (source checkout) ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5715#issuecomment-6113510113)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5715#pullrequestreview-5485296644): _This review was posted by an AI agent (OpenHands)._ ## Review of PR #5715 Reviewed at head `e7a917a21902a136015c74112215c6db6a7ef7a3` (head unchanged; PR title/body/labels and linked issue #5714 re-read now). **Scope** — In-repo and on-ownership. The adaptive poll lives in the shared `TerminalSession.execute` loop (`openhands-tools/openhands/tools/terminal/terminal/terminal_session.py`), so the tmux, subprocess/PTY, and Windows backends all use it. No public API, setting, or wire-contract change; the change stays within the shared session and constants code, as issue #5714 requires. **Correctness** — The new sleep, `min(POLL_INTERVAL, max(MIN_POLL_INTERVAL, elapsed_time / 4))`, is bounded above by `POLL_INTERVAL`, so no command ever waits longer between reads than it does today. The first interval is `MIN_POLL_INTERVAL` (0.02 s) and the 

### #5689: fix(agent-server): reap Canvas backend descendants after leader exit

- Commit: `c0471362d340ade1e334595ba98417108b415e88` (2026-10-12T00:56:49+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5689).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5689). Families: F28.

Stated intent:

> - Reap the owned process group before reporting an exited backend as unhealthy. Live backends retain graceful TERM-to-KILL shutdown, while descendants left by an already-exited leader are terminated immediately.
> - Reclaim an exited runtime before a direct restart, and keep forced installs and data deletion blocked while its process group remains alive.
> - Add real-subprocess coverage for status, direct stop, direct restart, responsive prepare/log/data operations, and SIGTERM-ignoring descendants.

Changed paths:

- `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`

Linked issue acceptance context:

- [#5634](https://github.com/OpenHands/software-agent-sdk/issues/5634): [Bug]: App backend status hangs holding the app lock when a child outlives the leader; expected unhealthy — current state closed, closed 2026-10-11T22:56:51Z.

> - [ ] After the leader exits, status returns `unhealthy` / `Backend exited with code <n>` without waiting for other processes to close the pipes. - [ ] `stop` kills the rest of the process group after the leader exited, also after status recorded the exit. - [ ] A real-subprocess test covers this case. - [ ] The `F28.status-exit-hang` bullet in the verify-agent-server F28 map passes with its `# bug` markers removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context In `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`: - `status()` (444-453) holds the per-app lock and, once the leader's `returncode` is set, awaits `_finish_runtime()`, which gathers the drain tasks (676-681). `_drain()` (526-537) reads until EOF; the pipes (642-649) are inherited by every child, so EOF waits for the last one. - Only `_terminate_runtime()` (707-725) signals the group, from `stop()` (734-753), which needs the same lock. `status()` also sets `runtime.process = None` (450), so a later `stop()` returns early (709-710). - `has_running_backends()` (727-732) checks only the leader, hence the accepted forced install. Suggested fix: when `status()` sees an exited leader, reap the group via `_terminate_runtime()` (it already signals while `_group_alive(pgid)`), or bound the drain wait and cancel the drain tasks, so the lock is never held on pipe EOF. ---

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5689#pullrequestreview-5485029407): _This review was posted by an AI agent (OpenHands)._ **Scope**: this change belongs in this repository. It modifies the agent-server Canvas App backend lifecycle (`openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`) and its regression tests, and needs no REST schema or TypeScript-client change (the `pid`/`port` fields are already optional in `BackendStatus` and the generated client). No maintainer decision is required. **Verification on head 93c6480** - Ran the focused suite in the workspace. 9 of the 186 tests failed here only because the test backends carry a `#!/usr/bin/python3` shebang and that interpreter is absent in this sandbox (creating the symlink needs root, which is unavailable). Re-running an untracked copy pointed at the available interpreter gives 186/186 passing, matching the PR's claim. - Confirme
- [reviews; APPROVED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5689#pullrequestreview-5485390469): Thank you!

### #5587: fix(sdk): recognize Haiku 5.5 prompt caching without model metadata

- Commit: `3385a486997edec21c1a2b859344f57a74dea84b` (2026-10-12T05:05:12Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5587).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5587). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - Add `claude-haiku-5-5` to `PROMPT_CACHE_MODELS`.
> - Test fallback recognition and real message formatting for both models with bare, Anthropic, and LiteLLM-proxy names.
> - Check automatic cache markers with caching enabled and disabled. Public APIs and cache policy remain unchanged.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/utils/model_features.py`
- `tests/sdk/llm/test_llm.py`
- `tests/sdk/llm/test_model_features.py`

Linked issue acceptance context:

- [#5702](https://github.com/OpenHands/software-agent-sdk/issues/5702): [Bug]: Haiku 5.5 prompt caching disabled when model metadata is unavailable — current state closed, closed 2026-10-12T05:05:14Z.

> - [ ] Haiku 5.5 fallback recognition works for bare, `anthropic/`, and `litellm_proxy/anthropic/` names. - [ ] Real message formatting adds automatic cache markers when enabled, and none when disabled. - [ ] Opus 5.5 behavior remains unchanged across those name forms. - [ ] No public API or cache-policy changes; live provider cache-hit measurements are outside this narrowly scoped fix. 

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5587#pullrequestreview-5480435066): _This review was posted by an AI agent (OpenHands)._ ## Review of #5587 (head `b3e72dc2d`) ### Scope In-scope for this repository. The change lives in `openhands-sdk/openhands/sdk/llm/utils/model_features.py`, which AGENTS.md names as the correct home for model/provider capability tweaks, and it keeps provider-specific criteria out of `llm.py`. No architecture or product decision is required. ### Technical assessment The one-line addition of `"claude-haiku-5-5"` to `PROMPT_CACHE_MODELS` is correct. The fallback is reached only in `_supports_explicit_prompt_cache()` after metadata is absent or inconclusive (`model_matches(model, PROMPT_CACHE_MODELS)`), and `model_matches` does case-insensitive substring matching on the raw model string, so the bare, `anthropic/`, and `litellm_proxy/anthropic/` forms all match. LiteLLM metadata for Haiku 5.
- [discussion; comment; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5587#issuecomment-6102723694): Linked the regression as Fixes #5702 and updated the test evidence. A fresh run matches the review: 327 passed, 3 OpenRouter reasoning-effort failures; the cache-focused subset passes all 49 tests. The description now distinguishes the original recorded result from this rerun. Companion docs PR OpenHands/docs#913 is linked in Notes. The HUMAN section is unchanged, and no code change was needed. The remaining gate is issue readiness: #5702 needs maintainer triage/ready-for-dev. This account cannot apply labels.

### #5659: fix(agent-server): ignore empty session API keys

- Commit: `e696f72c260b959d1b36d570a6688e99977ec28a` (2026-10-12T07:06:07+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5659).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5659). Families: F01, F02, F03, F06, F07, F13, F29, F31, F32.

Stated intent:

> - `Config` gets a `session_api_keys` validator that drops empty strings, so env slots, the JSON config and explicit construction are normalized in one place: `[""]` becomes `[]`, `[key, ""]` becomes `[key]`. This matches the legacy `SESSION_API_KEY=""`, which `_default_session_api_keys` already treats as no key.
> - Both WebSocket auth paths (legacy query/header and first-frame auth) refuse an empty presented key even if the list ever holds one, the same `candidate and candidate in keys` pattern `check_workspace_session` uses.
> - The default bind address follows the cleaned list. Non-empty keys behave exactly as before.
> - `/api/init` rejects any empty `session_api_keys` entry with HTTP 422 before leaving the dormant state; a client can retry with valid keys.

Changed paths:

- `openhands-agent-server/openhands/agent_server/config.py`
- `openhands-agent-server/openhands/agent_server/init_router.py`
- `openhands-agent-server/openhands/agent_server/sockets.py`
- `tests/agent_server/test_config.py`
- `tests/agent_server/test_init_router.py`
- `tests/agent_server/test_preload_modules.py`
- `tests/agent_server/test_websocket_first_message_auth.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5659#issuecomment-6076092603)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5659#pullrequestreview-5484862257): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope. This is an agent-server auth-hardening change in this repository's own package (`openhands-agent-server/`), and the linked umbrella issue #5688 explicitly assigns this path to this PR: "Reject empty session API keys before `/api/init` changes server state: #5659". No product/architecture decision is pending. ## Verification (current head `50c9514`) - **Env / JSON / direct construction** — the new `Config._drop_empty_session_api_keys` validator normalizes every `model_validate` path, so `[""]` → `[]` and `[key, ""]` → `[key]`. `load_config()` merges env slots into the model before validation, so blank `OH_SESSION_API_KEYS_0=` slots are dropped, and the default-bind decision in `__main__._auth_enabled()` (`bool(config.session_api_keys)`) now follows the cleaned list. Th

### #5657: fix(llm): keep provider credentials out of completion logs

- Commit: `e3a26821f8f7c85e98078c7b5e1a80647fc7d0c7` (2026-10-12T05:23:46Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5657).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5657). Families: F04, F06, F07, F09, F19, F20, F22, F23, F30, F33, F34.

Stated intent:

> - `Telemetry.on_request` copies the logged `extra_headers` and `headers` before the provider call and passes them through the existing `sanitize_dict` helper (`openhands/sdk/utils/redact.py`), so in-place changes during the call can't reach the log and credential-like headers are logged as `<redacted>`.
> - Successful calls (`log_llm_call`) and failed ones (`on_error`) both log from this context, and the server's log events are built from the same serialized data, so one change covers every place logs are written.
> - Credential-like string and `SecretStr` options in logged kwargs are redacted. Provider error messages, representations and tracebacks are sanitized before logging, including multiline credential values.
> - Headers that are not credentials keep their values for debugging. The request sent to the provider is unchanged.

Changed paths:

- `openhands-sdk/openhands/sdk/llm/utils/telemetry.py`
- `openhands-sdk/openhands/sdk/utils/redact.py`
- `tests/sdk/llm/test_llm_log_completions_integration.py`
- `tests/sdk/utils/test_redact.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5657#issuecomment-6075863054)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5657#pullrequestreview-5484726365): _This review was posted by an AI agent (OpenHands)._ Scope check: this is a bug fix in the SDK's LLM telemetry/redaction path, which this repository owns (AGENTS.md and the review guide). It is in scope, and the linked issue #5688 is `ready-for-dev` with `security-related` / `priority:medium`. CI on the current head is green (sdk-tests, agent-server-tests, integration-test, OpenAPI, coverage, etc.), and the two focused suites pass locally: `tests/sdk/utils/test_redact.py` (50 passed) and `tests/sdk/llm/test_llm_log_completions_integration.py` (7 passed). Two material findings, both in the credential-redaction path this PR is meant to harden. 1. `openhands-sdk/openhands/sdk/utils/redact.py:284` - the new `_SENSITIVE_ENTRY_RE` matches the value with `.*?`, which does not cross newlines (the pattern is not compiled with `re.DOTALL`). The pat
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5657#pullrequestreview-5485307014): _This review was posted by an AI agent (OpenHands)._ Fresh review of head `31d81aec`. The earlier `all-hands-bot` review was on `95ff8676`, and `smolpaws` and `Fieldnote-Echo` have commented since, so this is a new pass rather than a duplicate of the prior one. Scope: in-repo. The change hardens the SDK's own LLM telemetry/redaction path (`openhands-sdk/openhands/sdk/llm/utils/telemetry.py` and `openhands-sdk/openhands/sdk/utils/redact.py`) and extends the existing shared helpers instead of adding a parallel redaction implementation, which matches the repository guidance. No product or architecture decision is required. What I verified on this head: - The reported leak is closed. `Telemetry.on_request` now stores a redacted copy of the request context (`_redact_request_credentials`), copying `kwargs` and passing `extra_headers`/`headers` 
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5657#discussion_r4243002404): `(?P<value>.*?)` does not match newlines because the pattern is not compiled with `re.DOTALL`, and `.*?` stops at the first `\n`. The two patterns this replaces used `[^']*` / `[^"]*`, which do cross newlines, so this is a behavior regression: a sensitive entry whose value spans multiple lines is now logged verbatim where the base revision redacted it. Reproduced on this head: `redact_text_secrets("{'private_key': '<PEM with newlines>'}")` leaves the key intact (base: `<redacted>`); the same holds for any multi-line `token` / `secret` / `password` / `authorization` value. This weakens every caller of `redact_text_secrets` - telemetry error `message`/`repr`/`traceback`, `acp_agent`, `utils/command.py`, `profiles_router` - so it undercuts the hardening this PR adds. Add `re.DOTALL`, or use a value body that tolerates `\n`.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5657#discussion_r4243002408): `isinstance(value, str)` skips `pydantic.SecretStr`, which is the SDK's own credential type (`LLM.api_key` is `SecretStr`). A caller-supplied secret passed that way is copied verbatim, and `_safe_json` serializes it to its internal field. Reproduced: `llm.completion(msgs, api_key=SecretStr("CALLERSECRET123456"))` writes `"api_key": {"_secret_value": "CALLERSECRET123456"}` into the completion log. The PR body says credential-like options in logged kwargs are redacted; that does not hold for `SecretStr`. Redact secret-keyed values regardless of type (e.g. handle `SecretStr` explicitly) so the log cannot carry the wrapped value.
- [review_comments; comment; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5657#discussion_r4243232821): Confirmed and fixed in 31d81aec6ad5464bb4b12afaccffc038664547a6. The existing pattern now uses `re.DOTALL` so quoted multiline credential values are redacted. Regression coverage includes single, double, mixed, and escaped quote delimiters, preservation of adjacent public multiline text, and multiline provider errors in `message`, `repr`, and `traceback` through `LLM.completion()`. These new cases fail on 95ff8676 and pass with the fix. The redaction and completion-log suites pass all 65 tests; pre-commit passes.
- [review_comments; comment; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5657#discussion_r4243232860): Confirmed and fixed in 31d81aec6ad5464bb4b12afaccffc038664547a6. Credential options now accept both `str` and `SecretStr` for redaction through the existing `sanitize_dict` helper. Tests cover wrapped `api_key` and `aws_secret_access_key` values in successful and failed completion logs, verify the provider still receives the original values, and preserve numeric `max_completion_tokens`. Both wrapped-credential cases fail on 95ff8676 and pass with the fix. The wider regression run passed 613 tests with one ACP session-persistence failure that also reproduces on the untouched 95ff8676 head.
- [discussion; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5657#issuecomment-6103712703): @enyst, I reviewed #5657 and found a few adjacent cases in the shared error-text redaction path. Lowercase and JSON-style header keys, mixed quote styles, and escaped quotes can bypass the current matching. I have a small two-file patch with focused tests. Would you like me to send it as a commit for you to review or cherry-pick?
- [discussion; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5657#issuecomment-6113935049): @enyst Posting up a detailed review shortly. Keeping it inline because I don't think I have permissions to push commits to your branch.
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5657#issuecomment-6114003229): > @enyst Posting up a detailed review shortly. Keeping it inline because I don't think I have permissions to push commits to your branch. Thank you! Cool, up to you, I think you could also make a PR to this PR from your fork

### #5621: Add verify-agent-server: an executable feature map and control CLI for the Agent Server API

- Commit: `acca181af1f353975965bcf6486e1d0a58f444c0` (2026-10-12T21:22:20+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5621).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5621). Families: no product runtime required.

Stated intent:

> - A contributor skill under `.agents/skills/verify-agent-server/`: a control CLI (`control-agent-server`), an executable feature map (34 families, one file each), and maintenance levers.
> - **`control-agent-server`** launches isolated servers from the checkout (private HOME, persistence, keys, port), drives any REST route or WebSocket with assertions, arranges fixtures (git repos, skills, plugins, MCP servers, webhook sinks, a scripted LLM stub), and records evidence with secrets redacted. Model-backed recipes use DeepSeek through the server's own pre-flight.
> - **The feature map** owns every agent-facing route exactly once (`map coverage` is green: 193 of 203 routes owned and driven, 10 deliberately excluded with reasons) and replays end to end on fresh servers (`map run --all --fresh`). Known product bugs are kept as expected failures, so a fix shows up as an unexpected pass.
> - **Maintenance:** route coverage, recipe parse-checks against the real CLI, route-table diffs between two commits, and owner lookup for changed files. One cross-repo test (`tests/cross/test_verify_agent_server_

Changed paths:

- `.agents/skills/verify-agent-server/SKILL.md`
- `.agents/skills/verify-agent-server/references/adaptation.md`
- `.agents/skills/verify-agent-server/references/feature-map/F01-server-status.md`
- `.agents/skills/verify-agent-server/references/feature-map/F02-auth-and-sessions.md`
- `.agents/skills/verify-agent-server/references/feature-map/F03-deferred-init.md`
- `.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md`
- `.agents/skills/verify-agent-server/references/feature-map/F05-run-pause-interrupt.md`
- `.agents/skills/verify-agent-server/references/feature-map/F06-conversation-events.md`
- `.agents/skills/verify-agent-server/references/feature-map/F07-session-socket.md`
- `.agents/skills/verify-agent-server/references/feature-map/F08-confirmation-security-secrets.md`
- `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`
- `.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md`
- `.agents/skills/verify-agent-server/references/feature-map/F11-goals.md`
- `.agents/skills/verify-agent-server/references/feature-map/F12-runtime-and-credentials.md`
- `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`
- `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`
- `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`
- `.agents/skills/verify-agent-server/references/feature-map/F16-workspace-serving-and-vscode.md`
- `.agents/skills/verify-agent-server/references/feature-map/F17-workspaces-registry.md`
- `.agents/skills/verify-agent-server/references/feature-map/F18-settings-and-secrets.md`
- `.agents/skills/verify-agent-server/references/feature-map/F19-mcp.md`
- `.agents/skills/verify-agent-server/references/feature-map/F20-llm-profiles.md`
- `.agents/skills/verify-agent-server/references/feature-map/F21-agent-profiles.md`
- `.agents/skills/verify-agent-server/references/feature-map/F22-meta-profiles.md`
- `.agents/skills/verify-agent-server/references/feature-map/F23-llm-catalog-and-connections.md`
- `.agents/skills/verify-agent-server/references/feature-map/F24-openai-subscription.md`
- `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`
- `.agents/skills/verify-agent-server/references/feature-map/F26-plugins.md`
- `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`
- `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`
- `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`
- `.agents/skills/verify-agent-server/references/feature-map/F30-openai-gateway.md`
- `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`
- `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`
- `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`
- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `.agents/skills/verify-agent-server/references/feature-map/README.md`
- `.agents/skills/verify-agent-server/references/maintenance.md`
- `.agents/skills/verify-agent-server/references/mapping.md`
- `.agents/skills/verify-agent-server/references/report.md`
- `.agents/skills/verify-agent-server/scripts/control-agent-server`
- `.agents/skills/verify-agent-server/scripts/control_agent_server.py`
- `.impeccable/config.json`
- `openhands-agent-server/AGENTS.md`
- `tests/cross/conftest.py`
- `tests/cross/test_verify_agent_server_skill.py`

Linked issue acceptance context:

- [#5617](https://github.com/OpenHands/software-agent-sdk/issues/5617): [Feature]: Executable feature map and control CLI for verifying the Agent Server's REST and WebSocket APIs — current state closed, closed 2026-10-12T19:22:23Z.

> - [ ] Every agent-facing route has exactly one owning family and at least one recipe that drives it (`map coverage`). - [ ] Every recipe command parses against the real CLI (`map check`), enforced by a unit test. - [ ] Each family replays end to end on a fresh server (`map run --fresh`). Real model behavior comes from a real model (DeepSeek); a scripted stub is used only for provider failures a real model cannot produce on demand. - [ ] Product bugs found along the way are written down as known-bug recipes with a reproduction. ### Alternatives Considered **Driving the server through the TypeScript client.** The client is itself a consumer under test. It is pinned to a released server, does not wrap every route, and would hide status codes, error bodies and WebSocket close codes behind its own API. It is better covered as its own consumer family, next to the Python SDK client. ### Priority / Severity Medium - Would improve experience ### Estimated Scope Medium - New feature with moderate complexity ### Feature Area - [x] Agent Server - [x] Testing / Development tools ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5621#issuecomment-6073720178)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5621#pullrequestreview-5475829425): _This review was posted by an AI agent (OpenHands)._ ## Review of `verify-agent-server` Scope check: this adds a contributor skill under `.agents/skills/`, one cross-repo test, and a pointer in `openhands-agent-server/AGENTS.md`. That belongs in this repository and touches no runtime code, so the scope gate passes and I reviewed it on the merits. State I verified on head `cee9a8bb8a392bd382a8d742b76d5b1c856fb2b4`: - head still matches the requested SHA; PR open, no labels; the only review requests are for `all-hands-bot`. - CI on this head is green (Run tests, Pre-commit, REST API breakage, TypeScript client CI/audit, Persisted settings, PR Description, Review Thread Gate, Agent Server). - Local: `tests/cross/test_verify_agent_server_skill.py` passes 11/11, `ruff`/`pyright` clean on the new files, `control-agent-server map check` green (3
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5621#pullrequestreview-5485063074): _This review was posted by an AI agent (OpenHands)._ ## Review of `verify-agent-server` (head `5c34e09`) Scope check: this adds a contributor skill under `.agents/skills/`, a cross-repo test, and `.pr/` review artifacts. It belongs in this repository and touches no runtime code, so the scope gate passes. State verified on this head: - head `5c34e09a91c5d0f1d3d613229c42a598225fd4a7` matches; PR open, non-draft, no labels. - CI: every check is green except `Review Thread Gate` (`unresolved-review-threads`), which fails only because the two earlier `all-hands-bot` threads on `control_agent_server.py` remain unresolved - an author action, not a code defect. - Local: `tests/cross/test_verify_agent_server_skill.py` 34/34 pass; `map check` green (34 files, 931 IDs); `map coverage` green (204 routes, 194 owned/driven). Both findings from the prev
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5621#pullrequestreview-5494552136): _This review was posted by an AI agent (OpenHands)._ ## Review of `verify-agent-server` at head `fff65e3` Scope check: this PR adds a contributor skill under `.agents/skills/verify-agent-server/` (control CLI + executable feature map), one cross-repo test, and `.pr/` review artifacts, plus a short pointer in `openhands-agent-server/AGENTS.md` and a `.impeccable/config.json` ignore entry. It belongs in this repository and changes no runtime code, so the scope gate passes. Current state verified on this head: - head `fff65e3ebff18786e389c161dbdd30f955e3bb6b` matches; PR open, non-draft, no labels, `mergeable_state: blocked`. - CI: every check run on this head is `success` (49) or `skipped` (2); no failures. - Local: `tests/cross/test_verify_agent_server_skill.py` 39/39 pass; `map check` green (34 files, 935 IDs); `map coverage` green (204 r
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4234957309): `--field` (here) and `--print-header` (line 1355) `print()` the raw value instead of going through `emit()`/`REDACT`, so a secret-bearing field reaches stdout unredacted — contradicting the `SKILL.md` contract that the CLI redacts the run's keys, `$DEEPSEEK_API_KEY` and cipher-encrypted values from all output and evidence. Reproduced on this head: `api GET /info --field echo_key` (body echoes the `X-Session-API-Key`) printed `qa-session-key-123456` verbatim, and `--field config.api_key` printed a raw `gAAAAA...` Fernet token that renders as `<redacted:encrypted>` on the JSON path. The committed map relies on these flags for secret-bearing values (e.g. `F20-llm-profiles.md` `--field config.api_key`, `F18-settings-and-secrets.md` `--field agent_settings.llm.api_key`, and the many `F29` `--print-header set-cookie` captures), so a recipe that
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4234957320): `ok` omits `undriven`, so `map coverage` reports success while a route is owned but never driven. Reproduced with a synthetic route table where `F01` lists `Routes: `GET /api/thing`` and no recipe calls it: `cmd_map_coverage` returned `owned_but_not_driven: [{"route": "GET /api/thing", ...}]` together with `ok: True` (exit 0). The subcommand help advertises "routes no family owns or drives", and the maintenance pass checks this result, so the undriven case is silently missed. Use `not unowned and not undriven and not multi and not stale`.
- [review_comments; comment; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4240354514): Fixed in 2f1aa080f1ee4179c4891e0e161023a5b7cb6ded. Both output paths now use the existing redactor by default. Raw round trips require the explicit --unsafe-unredacted flag; evidence remains redacted, and the affected recipes opt in deliberately. Loopback before/after reproductions and regression tests cover headers, fields, structured output and encrypted tokens. Review notes: https://htmlpreview.github.io/?https://github.com/OpenHands/software-agent-sdk/blob/08e548c80b9664edf0eb8b9e9580fe8392bee5bd/.pr/review-fixes.html
- [review_comments; comment; smolpaws](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4240354568): Fixed in 2f1aa080f1ee4179c4891e0e161023a5b7cb6ded. Owned-but-undriven routes now fail coverage. The reproduction uses the real route table with one owned undriven route: the original exits 0 and the fix exits 1. After merging main and covering POST /api/acp/models, the map is green at 194 owned/driven routes out of 204, with 10 exclusions. Review notes: https://htmlpreview.github.io/?https://github.com/OpenHands/software-agent-sdk/blob/08e548c80b9664edf0eb8b9e9580fe8392bee5bd/.pr/review-fixes.html
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4243319541): `--field` here (and the encrypted reads at lines 298 and 613) now returns the redaction placeholder by default, so the `test "${ENC#gAAAAA}" != "$ENC"` on the next line fails and, under `set -eo pipefail`, F09 fails at its first block. F18 and F20 were updated in this same commit to pass `--unsafe-unredacted` for exactly this read; this one was missed. Add `--unsafe-unredacted` to the three encrypted `--field` reads in this file (and keep the raw token out of evidence).
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4243319543): Same missed opt-in as F09: this encrypted read now yields `<redacted:encrypted>`, so the `test "${ENC#gAAAA}" != "$ENC"` below fails and the subsequent `/api/mcp/test` call receives the placeholder instead of the ciphertext it must decrypt. Add `--unsafe-unredacted` here.
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4243608466): Fixed in cb00b0c (being pushed now). The three encrypted reads in F09 (`config.api_key`, `config` and `agent_settings`) now pass `--unsafe-unredacted`; evidence stays redacted. Paired runs on fresh servers: on `fc01624`, `map run --file F09 --only F09.auth-required` fails in Preconditions at `test "${ENC#gAAAAA}" != "$ENC"`. With the fix it passes, and so do `F09.load-plugin` (which posts the settings back with `secrets_encrypted: true`) and `F09.switch-llm-registered-usage-id` (xfail at its marked line). Details are in `.pr/followup-fixes.md`, section 4. --- _Generated by [Claude Code](https://claude.ai/code)_
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5621#discussion_r4243608633): Fixed in cb00b0c (being pushed now). This read now passes `--unsafe-unredacted`. Two more F19 reads had the same problem: the `oauth_state` of a finished OAuth job (in `F19.oauth-inline-writeback` and `F19.oauth-stored-state`), whose tokens are also ciphertext. Both opt in now. The two `plaintext` reads of the stored access token opt in as well. Without it, a regression that returned ciphertext would come back as `<redacted:encrypted>`, and the `test "${TOKEN#gAAAA}" = "$TOKEN"` check would still pass. Paired runs on fresh servers: on `fc01624`, `F19.test-encrypted-env` fails at `test "${ENC#gAAAA}" != "$ENC"`. `F19.oauth-stored-state` also fails, because `/api/mcp/test` with the redacted state times out on tool listing. With the fix both pass, along with `F19.oauth-inline-writeback` and `F19.oauth-refresh-writeback`. No raw token appears
- [discussion; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5621#issuecomment-6091287181): I put this through a dogfood run against main and two open fixes. It’s paying off already. F28 gave clean baseline-to-fix signals for both. `F28.install-ref-cached` xfailed twice on main and xpassed twice on #5685; an additional run also covered the adjacent first-fetch, valid-pin, and latest-ref behavior. `F28.status-exit-hang` xfailed twice on main and xpassed twice on #5689. I then ran the full F28 family on #5689: 24 ordinary cases passed, the four unrelated known bugs remained expected failures, and the lifecycle fix was the only unexpected pass. I also ran a model-free F02 slice, where eight authentication, workspace-cookie, and CORS cases passed. A few things surfaced: - `--checkout` applies to the outer fresh run, but nested `launch --new` calls inside recipes fall back to the harness checkout. In the F02 prefix-cookie case, the o
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5621#issuecomment-6115034096): I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. @Fieldnote-Echo, thank you for the dogfood run. It's great to see the baseline-to-fix signals work as intended on #5685 and #5689, and the F28 bullet for #5689 is now an ordinary passing bullet on this branch. All three findings are addressed in 2f1aa080, with follow-ups in fc016247: - **`--checkout` reaching nested launches:** the baseline checkout now carries into nested `launch --new` calls and SDK recipe steps, so a multi-server recipe no longer mixes baseline and target. - **Family-wide prerequisites:** bullets can declare their own `Requires:`, and needs are checked per selected recipe, so an `--only` slice runs without the family's unrelated prerequisites, such as the DeepSeek preflight or tmux. - **Combined reports:** merging several ledgers k

### #5724: Replace policy rail regexes with shell AST analysis

- Commit: `d97eb76f60829a4250dd313f5a9d4d03e2951de8` (2026-10-13T05:30:07+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5724).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5724). Families: F08.

Stated intent:

> - Parse each original executable segment with the shared tree-sitter Bash parser.
> - Reuse the shared command view for command words and redirect-owned arguments.
> - Resolve quoted command names, paths, supported wrappers, descriptor flow, and literal nested or piped shell source.
> - Return `UNKNOWN` when relevant pipeline output, redirects, consumers, or inherited input cannot be resolved.
> - Treat `command -v/-V` as inspection. Keep `command -p`, nested substitutions, and supported builtin wrapper chains executable.
> - Preserve the existing rail IDs and evaluation order. Implement #2721's same-pipeline fetch-to-downstream-command policy.
> - Keep the Pattern analyzer AST scan on original segments. Its existing regular-expression corpora keep normalized text.

Changed paths:

- `openhands-sdk/openhands/sdk/security/_shell_ast.py`
- `openhands-sdk/openhands/sdk/security/defense_in_depth/pattern.py`
- `openhands-sdk/openhands/sdk/security/defense_in_depth/policy_rails.py`
- `openhands-sdk/openhands/sdk/security/defense_in_depth/shell_semantics.py`
- `tests/sdk/security/defense_in_depth/test_policy_rails.py`
- `tests/sdk/security/defense_in_depth/test_shell_semantics.py`
- `tests/sdk/security/test_shell_ast.py`

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5724#pullrequestreview-5495736400): HUMAN: my agent claims it reproduced a few issues, I'm not sure 1 is worth fixing but maybe it is? 2 seems quite reasonable to me fwiw > Found three actionable issues in f1b6ca1. I recommend COMMENT pending fixes. > 1. [P1] Executable literal scripts become LOW. [policy_rails.py:209](https://github.com/OpenHands/software-agent-sdk/pull/5724/files) > eval 'dd of=/dev/sda' and bash <<<'mkfs.ext4 /dev/sda1' change from HIGH → LOW. The scanner only follows -c script operands, skipping these executable payloads. With PolicyRailSecurityAnalyzer and ConfirmRisky alone, confirmation is bypassed. Scan literal eval arguments and shell stdin scripts. > 2. [P2] Captured curl output falsely triggers the pipeline rail. [policy_rails.py:377](https://github.com/OpenHands/software-agent-sdk/pull/5724/files) > response=$(curl x) \| jq . changes from LOW → 
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#pullrequestreview-5495989910): _This review was posted by an AI agent (OpenHands)._ Scope: this PR belongs here (SDK-owned security analyzers, part of #2721). Reviewed against base `acca181` and the current head `f1b6ca1`; CI on this head is green and `uv run pytest tests/sdk/security` reproduces the stated `646 passed, 7 skipped, 10 xfailed`. The rail IDs, ordering, and the fetch-to-`node`/nested-`-c` improvements are real. Three findings are demonstrated on this head against the base regex behavior; each is a behavioral change on a supported path, not a style nit. **1. Executable literal scripts regress HIGH -> LOW (security).** Nested script operands are only followed through a runner's `-c` operand; `eval` arguments and shell stdin scripts are not. `eval 'dd of=/dev/sda'`, `bash <<< 'mkfs.ext4 /dev/sda1'`, and `bash <<< 'dd of=/dev/sda'` were `HIGH` in the base reg
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#pullrequestreview-5497694257): _This review was posted by an AI agent (OpenHands)._ Scope: this change belongs in this repository (SDK-owned security analyzers, part of #2721) and continues the shell-to-AST migration. Reviewed at head `99c7901e` against base `acca181a`. CI is green on this head and `uv run pytest tests/sdk/security` reproduces the stated `646 passed, 7 skipped, 10 xfailed`. I re-checked the three findings from the previous review on this head (real `EnsembleSecurityAnalyzer([PolicyRailSecurityAnalyzer(), PatternSecurityAnalyzer()])`, default `propagate_unknown=False`, `ConfirmRisky`): 1. Executable literal scripts (fixed). `eval 'dd of=/dev/sda'`, `bash <<< 'dd of=/dev/sda'`, `bash <<< 'mkfs.ext4 /dev/sda1'` are `HIGH` again. 2. Captured fetch output (fixed). `response=$(curl x) \| jq .` and ``out=`curl x` \| jq .`` are `LOW` again. 3. Glob deletes (fi
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#pullrequestreview-5497798409): _This review was posted by an AI agent (OpenHands)._ Re-review of head `99c7901` (supersedes the earlier review on `f1b6ca1`). I re-fetched the PR, its reviews/threads, the linked issue #2721, and the head's check runs, and re-derived every claim below on the current head against base `acca181`. **What I verified** - All three findings from the previous review are fixed on this head. `eval 'dd of=/dev/sda'`, `bash <<< 'dd of=/dev/sda'`, and the `dd 2>/dev/null of=/dev/sda` redirect form now return `HIGH`; `response=$(curl x) \| jq .` is back to `LOW`; and the benign `rm *.o` / `rm -- ./build/*.o` cases are `LOW` again. The author's replies in threads `4252363867`/`4252363873`/`4252363881` are accurate. - `UV_CACHE_DIR=/tmp/uv-cache-review2 uv run pytest -q tests/sdk/security` → `775 passed, 7 skipped, 10 xfailed`. - The only failing check
- [reviews; APPROVED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5724#pullrequestreview-5498437112): Thank you!
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4252363867): Executable literal scripts regress from `HIGH` to `LOW`. `static_shell_script` only extracts a nested program from a runner's `-c` operand, so `eval 'dd of=/dev/sda'`, `bash <<< 'dd of=/dev/sda'`, and `bash <<< 'mkfs.ext4 /dev/sda1'` all now return `LOW`. On base `acca181` these three returned `HIGH` (the `dd ... of=/dev/` and `mkfs.` regexes did not inspect context), and on this head `_evaluate_rail` returns `LOW` for each with no `UNKNOWN` fallback. Where an agent is configured with `PolicyRailSecurityAnalyzer` + `ConfirmRisky` (no `PatternSecurityAnalyzer`), a destructive write through `eval`/herestring no longer reaches confirmation. Scan literal `eval` arguments and shell stdin/herestring scripts, not just `-c` operands.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4252363873): `response=$(curl x) \| jq .` changes `LOW` -> `HIGH`. Parse: the pipeline LHS stage is a `variable_assignment` whose value is a `command_substitution` containing `curl`. `_has_command_ancestor` walks from the `curl` node and stops when it reaches the stage (`variable_assignment`) without ever seeing an enclosing `command` node, so `curl` is accepted as a producer even though the substitution output is captured into `response` and discarded from the pipe. Same result for ``out=`curl x` \| jq .``. Base `acca181` returned `LOW` for this input (the regex rail required a downstream interpreter), while this head returns `HIGH`/`fetch-to-exec`. Exclude assignment and command-substitution producers from the enclosing pipeline's source commands.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4252363881): Any glob or brace word in an `rm` argument is now treated as arity-uncertain, so ordinary cleanup commands return `UNKNOWN` rather than `LOW`. On this head `PatternSecurityAnalyzer` reports `UNKNOWN` for `rm *.o`, `rm build/*.log`, and `rm -- ./build/*.o`; on base `acca181` all three were `LOW`. Because `ConfirmRisky` defaults to `confirm_unknown=True`, these benign deletes now prompt for confirmation. `--` does not suppress it: `_extract_script_operand` handles `--` for option parsing, but this branch is evaluated independently and `argument_arity_is_uncertain` on `./build/*.o` still fires. Respect the option terminator here (once `--` is seen, an expanded filename cannot introduce `-r`/`-f` flags), and consider limiting the uncertainty to cases where a destructive flag could actually be synthesized.
- [review_comments; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4253279745): Fixed in `0bf3a2c`. The scanner now follows literal `eval` arguments and shell standard-input sources through the bounded nested-source path. Dynamic source returns `UNKNOWN`. Script-file input, `-c` input data, and standard input replaced by a later redirect remain outside the nested scan.
- [review_comments; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4253280253): Fixed in `0bf3a2c`. Assignment command substitutions no longer count as producers for the enclosing pipeline. Direct fetch pipelines and known output-forwarding stages remain covered.
- [review_comments; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4253280701): Fixed in `0bf3a2c`. The `rm` scan now limits uncertainty to expansions before `--` that can produce recursive or force options. Ordinary path globs, including `*.o`, `build/*.log`, and `rm -- ./build/*.o`, remain `LOW`.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4253756304): Fetch-to-exec regresses `HIGH` -> `LOW` when the fetch and the interpreter are separate statements of the same executable segment. `_scan_fetch_pipelines` only correlates a fetch with a downstream interpreter when the fetch is a producer of the *same* `pipeline` node. When the fetch sits in an earlier `;`/newline statement, the correlation is lost and the rail passes. The base regex scanned the joined segment as flat text and correlated any `curl`/`wget` with any `\| interpreter`, so this is a behavioral regression, not just a parser change. Demonstrated on this head with `EnsembleSecurityAnalyzer([PolicyRailSecurityAnalyzer(), PatternSecurityAnalyzer()])` (default `propagate_unknown=False`) and `ConfirmRisky`: - `v=$(curl -s URL); echo "$v" \| python3` — base rail `HIGH` -> head rail `LOW`, pattern `LOW`, ensemble `LOW` (no confirmation)
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4253845544): Literal shell text piped into a shell regresses from `HIGH` to `LOW` across the rail, `PatternSecurityAnalyzer`, and the default ensemble. The pipeline scan only treats the left side as a potential fetch source; a shell runner on the right whose stdin is a literal command string is never scanned as a nested program. Base `acca181` caught these because the flat-text regex matched `curl`/`wget` and `\| bash` inside the quoted payload. On this head: - `printf 'curl x \| node' \| bash` → `LOW` (base `HIGH`) - `echo 'wget x \| node' \| bash` → `LOW` (base `HIGH`) - `printf '%s' 'curl x \| node' \| bash` → `LOW` (base `HIGH`) - `cat <<'EOF' \| bash` + `curl x \| node` in the body → `LOW` (base `HIGH`) These commands really do execute the payload: `printf 'echo PWNED\n' \| bash` prints `PWNED`, and `printf 'dd of=/dev/null' \| bash` exits 0. The
- [review_comments; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4253890722): Thanks. I checked these cases again at `5cd5532`. The rail follows the same-pipeline scope in #2721. A segment-wide correlation would make `curl x; echo y \| python` `HIGH`, although the fetched output never reaches Python. `v=$(curl -s URL); echo "$v" \| python3` is a real cross-statement data-flow case. Supporting it needs value tracking, not a wider token correlation. `$(curl -s URL) \| python3` already returns `UNKNOWN`; the default ensemble then replaces that with `LOW`, which is a separate fusion-policy decision. Should Phase 2c remain limited to same-pipeline structural flow, with cross-statement data flow and `UNKNOWN` fusion tracked on #2721? If so, I will keep this PR bounded. If cross-statement tracking is required before merge, that materially expands this PR.
- [review_comments; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4254060291): Fixed in `a8a4ef5`. The rail now follows literal `echo`, `printf`, and quoted-heredoc output when a shell reads it from the same pipeline. It also preserves descriptor aliases and inherited input through bounded nested shell launches. Unresolved producers, dynamic redirects, ambiguous stages, and unsupported shell options return `UNKNOWN`. Redirects that replace standard input or output remain `LOW`. The four reported forms now return `HIGH` at the rail and default ensemble boundaries. The security suite passed with 851 tests passed, 7 skipped, and 10 expected failures. The tests detected all 20 targeted mutations across the PR, including the 10 pipeline-source mutations. Harmless Bash probes also confirmed the descriptor and inherited-input paths.
- [review_comments; comment; Fieldnote-Echo](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4254086241): @enyst I'd like your direction here, please. This slice of the refactor is already growing quite large. I'd suggest we track follow ups on #2721 otherwise this will become unreviewable.
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5724#discussion_r4254247084): Oh, yes, sure thing!

### #5660: fix(agent-server): treat an empty OH_SECRET_KEY as unset

- Commit: `7bd668c9ab74f62c85745cd95adb503f53e7228b` (2026-10-13T07:50:19+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5660).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5660). Families: F01, F02, F03, F29, F31, F32.

Stated intent:

> - A `field_validator` on `Config.secret_key` treats a blank value (empty or whitespace only) as unset by returning `_default_secret_key()`, the same fallback used when the variable is absent. The first session key is used when one is set; otherwise there is no cipher and the existing warning is logged. This covers the env var, the JSON config file and direct `Config(...)` construction.
> - `/api/init` applies `secret_key` with `model_copy`, which skips validators, so `_build_initialized_config` now ignores a blank `secret_key` too and its existing session-key fallback applies.
> - On a deferred-init server, `OH_SECRET_KEY` is also the credential for `POST /api/init`, and `check_init_api_key` leaves the endpoint open when no key is configured. So that a blank value does not turn a closed endpoint into an open one, `Config` remembers that a blank key was configured (a private attribute, not serialized or in the schema), and `/api/init` answers 401 when that is the case and no session key fills in. A truly unset key keeps today's behaviour, and a session-key fallback is still the init crede

Changed paths:

- `openhands-agent-server/openhands/agent_server/config.py`
- `openhands-agent-server/openhands/agent_server/init_router.py`
- `tests/agent_server/test_config.py`
- `tests/agent_server/test_init_router.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5660#issuecomment-6076126751)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5660#pullrequestreview-5499136818): _This review was posted by an AI agent (OpenHands)._ ## Review summary **Scope check:** this change belongs in this repository. `openhands-agent-server/` owns the agent-server `Config` and the `/api/init` lifecycle, and the diff matches the `#5660` scope in umbrella issue #5688 ("treat a blank `OH_SECRET_KEY` as missing and fail closed if no usable key remains"). No repository move or product/architecture decision is needed. Re-reviewed on the new head `f736419141f7a76b084041499aadee77209edf85` (my prior approval was on `253572c0`; the head moved with the `/api/init` fail-closed change plus test cleanup, so a fresh review is required). ### What I verified - **Blank-key handling still correct.** `Config._empty_secret_key_is_unset` (config.py:513) uses `_is_blank_secret`, so `""`, `" "`, `"\n"` from the env var, the JSON config file, and di
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5660#discussion_r4242969634): This guard only rejects a truly empty value. For the env-var and JSON paths, `load_config()` feeds `OH_SECRET_KEY` through `Config.model_validate`, so `_default_secret_key()` is never reached and this validator is the only check -- a whitespace-only value such as `OH_SECRET_KEY=" "` (e.g. a template that rendered a stray space) passes here and `Config.cipher` still builds `Cipher(" ")`, the weak-key case this PR targets. Verified on this head: `OH_SECRET_KEY=" "` and JSON `{"secret_key": " "}` both yield a working `Cipher`, while `""` yields `None`. Issue #5688 scopes whitespace-only as unset. Consider `if value is not None and not value.get_secret_value().strip():`.
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5660#discussion_r4251636759): I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. Confirmed and fixed in 253572c0. `OH_SECRET_KEY=" "` did build a `Cipher` from the whitespace, and #5688 scopes whitespace-only as unset, so the earlier "matches `_default_secret_key`" note in the description was wrong. The validator and the `/api/init` path now strip before the emptiness check. Tests cover the env var (`" "` and `"\n"`), the JSON config file, direct `Config(...)` and init; the six new cases fail without the change. On the coordination note: #5659 has landed, and the merge leaves a single copy of the empty init key guard in `InitService.initialize`. --- _Generated by [Claude Code](https://claude.ai/code)_

### #5520: fix(subagent): let scoped sub-agents keep conversation-local tools like task_tracker

- Commit: `c1dfd9c52cd3a1d87589b5054c8181d041d88859` (2026-10-13T14:29:07+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5520).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5520). Families: F27, F33.

Stated intent:

> - **New `ToolDefinition.conversation_local` class flag** (default `False`). It marks tools that only read and write their own conversation's state.
> - **`SubAgentScope.missing_from` ignores conversation-local tools.** It's the single check behind the task tool's listing, the `TaskManager` start check and both workflow tools, so all of them change together.
> - **`TaskTrackerTool` sets the flag.** Every other tool is still checked as before.

Changed paths:

- `openhands-sdk/openhands/sdk/subagent/scope.py`
- `openhands-sdk/openhands/sdk/tool/tool.py`
- `openhands-tools/openhands/tools/task_tracker/definition.py`
- `tests/tools/task/test_sub_agent_scope.py`

Linked issue acceptance context:

- [#5519](https://github.com/OpenHands/software-agent-sdk/issues/5519): [Bug]: A scoped profile can't delegate to general-purpose because it lacks task_tracker — current state closed, closed 2026-10-13T12:29:09Z.

> - [ ] Tools can declare that they only touch their own conversation's state, and `task_tracker` does. - [ ] `SubAgentScope.missing_from` ignores such tools, for both definitions (listing) and built sub-agents (start). - [ ] A profile with `terminal`, `file_editor` and the task tool, but no `task_tracker`, is offered `general-purpose` and the sub-agent runs with `task_tracker`. - [ ] Sub-agents needing any other tool the delegating agent lacks are still refused. ### Installation Method uv / pip ### SDK Version 1.53.0 ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5520#issuecomment-5996780731)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5520#pullrequestreview-5495011899): _This review was posted by an AI agent (OpenHands)._ ## Review of PR #5520 Scope check: this change belongs in `OpenHands/software-agent-sdk`. It modifies SDK tool-definition metadata and sub-agent scope enforcement, both owned by this repository. ### What I verified - **Head matches**: `32f5c6e7f805e54101f390f1a20a2953cce5fde1` (title, body, links unchanged). Linked issue #5519 is open with `bug`, `priority:medium`, `ready-for-dev`. - **CI**: no failing/timed-out/action-required check runs on the current head. - **Tests (in workspace)**: `tests/tools/task/test_sub_agent_scope.py` (15 passed), `tests/sdk/subagent`, `tests/sdk/tool`, `tests/sdk/profiles`, `tests/tools/task`, `tests/tools/workflow` — 614 passed. The two new tests and the updated `general-purpose` expectation pass. One unrelated failure, `tests/sdk/subagent/test_subagent_loa
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/5520#issuecomment-6127994465): @simonrosenberg I rebased on `main` (`e3a2682`, 1.54.0), checked the change end to end, and CI is green: 48 checks passed. The 3 skipped checks are `Validate PR description`, which doesn't run on drafts, and the two `.pr/` cleanup jobs. **Validation** - Unit tests: 741 passed across `tests/tools/task`, `tests/tools/workflow`, `tests/sdk/subagent`, `tests/sdk/tool` and `tests/sdk/profiles`. With `main`'s `scope.py`, the 3 new or changed scope tests fail, so they do catch the bug. - Live check (`.pr/conversation_local_e2e.py`, real agent-server and Agent Canvas mock LLM): all 6 checks pass on this branch. With `main`'s `scope.py` it shows the original refusal, `Agent 'general-purpose' uses task_tracker, which this agent does not have.` - The sub-agent's `TASKS.json` is written to `conversations/<parent>/subagents/<sub-agent>/`. That confirm
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5520#issuecomment-6128021507): I rebased PR #5520 onto `main` and the end-to-end check passes, and CI is green. The one thing I didn't do is fill in the HUMAN section: the repo's rules say AI agents must not write it, so you'll need to add that note yourself. **Rebase:** The branch is now on `main` at `e3a2682` (1.54.0). It rebased with no conflicts, and I force-pushed it. **Testing** - **Unit tests:** 741 passed across the task, workflow, sub-agent, tool and profile test suites. When I put back `main`'s version of the scope check, the 3 new or changed scope tests fail, so they catch the bug. - **Live check:** The PR's script (`.pr/conversation_local_e2e.py`) runs a real agent-server with Agent Canvas's scripted mock LLM. On this branch, all 6 of its checks pass. With `main`'s scope check put back, it fails with the original error: `Agent 'general-purpose' uses task_tr

### #5744: fix(profiles): preserve disabled skills when seeding default

- Commit: `d6165a51cb158c0fd80eeb6ffac9cee2b4ce73e5` (2026-10-13T13:30:53Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5744).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5744). Families: F21.

Stated intent:

> Copy the existing deny-list into the seeded profile. Inline skills still do not become positive references, and unknown disabled names remain harmless.

Changed paths:

- `openhands-sdk/openhands/sdk/profiles/seed.py`
- `tests/sdk/profiles/test_resolver.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5744#issuecomment-6146217379)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5744#pullrequestreview-5504221402): _This review was posted by an AI agent (OpenHands)._ ## Review **Scope** — Belongs here. The defect lives in `openhands-sdk/openhands/sdk/profiles/seed.py`, which this repository owns (SDK profiles/resolver/seed). Linked issue #5398 is `ready-for-dev` in this repo; OpenHands/OpenHands#17991 is the downstream canvas-launch symptom. No cross-repo move is warranted. **Correctness** — The one-line change replaces the hardcoded `disabled_skills=[]` with `list(context.disabled_skills)`, copying the global settings deny-list into the seeded `OpenHandsAgentProfile`. This matches the resolver's established deny-list model: `_build_openhands_settings` carries `profile.disabled_skills` onto the resolved `AgentContext`, `_apply_disabled_skills` filters the discovered catalog by it, and a disabled name absent from the catalog is a documented no-op. It

### #5409: feat: launch inline Agent Profiles and per-launch LLM overrides

- Commit: `4011cb5d4fd508964fe210df9a50af7d2f19d29d` (2026-10-13T13:42:51Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5409).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5409). Families: F01, F04, F05, F06, F07, F10, F12, F16, F18, F19, F20, F21, F22, F27, F29, F30, F31, F32, F33, F34.

Stated intent:

> - `StartConversationRequest.agent_profile` launches an unsaved profile. It is resolved exactly like a stored one, never saved, and recorded as `launched_agent_profile.inline`. It is mutually exclusive with the other agent sources.
> - `AgentLaunchAdditions.llm_profile_ref` launches an OpenHands profile with another LLM profile, without modifying the profile. It is recorded in `launched_agent_profile.llm_profile_ref`. It is rejected for ACP profiles and for non-profile sources.
> - The OpenAI gateway launches the active Agent Profile, seeding `default` on first use. An OpenHands profile gets the model's LLM profile as that override. An ACP profile runs its own model, as ACP settings did before. An unknown model is still a 404 for either kind.
> - Docker: the host resolves an inline profile and the override with its stores, and forwards only `agent_settings`.
> - `/server_info` reports `agent_launch_pipeline_v1`, so canvas can gate on these fields.
> - `SendMessageRequest` moves to `openhands.sdk.conversation.message_request` and is still re-exported from `request`. This lets the request model i

Changed paths:

- `openhands-agent-server/openhands/agent_server/agent_profiles_router.py`
- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `openhands-agent-server/openhands/agent_server/docker_runtime/routers.py`
- `openhands-agent-server/openhands/agent_server/launch.py`
- `openhands-agent-server/openhands/agent_server/openai/service.py`
- `openhands-agent-server/openhands/agent_server/server_details_router.py`
- `openhands-sdk/openhands/sdk/conversation/message_request.py`
- `openhands-sdk/openhands/sdk/conversation/request.py`
- `openhands-sdk/openhands/sdk/launch/resolve.py`
- `openhands-sdk/openhands/sdk/profiles/agent_profile.py`
- `openhands-sdk/openhands/sdk/settings/model.py`
- `tests/agent_server/docker_runtime/test_mediation.py`
- `tests/agent_server/test_agent_profile_conv_start.py`
- `tests/agent_server/test_launch_architecture.py`
- `tests/agent_server/test_launch_parity.py`
- `tests/agent_server/test_openai_gateway_launch.py`
- `tests/agent_server/test_openai_gateway_settings.py`
- `tests/agent_server/test_server_details_router.py`
- `tests/cross/test_remote_conversation_live_server.py`
- `tests/sdk/launch/test_resolve.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5409#issuecomment-5962076201)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5409#pullrequestreview-5371963931): _This review was posted by an AI agent (OpenHands)._ ## Verdict: changes needed — the OpenAI gateway is broken on ACP agent profiles ### Blocking finding `_create_conversation_request` (openhands-agent-server/openhands/agent_server/openai/service.py, new lines ~252–273) now always builds `AgentLaunchAdditions(llm_profile_ref=<model profile>)` and pairs it with the *active* Agent Profile via `agent_profile_id`. `resolve()` rejects `llm_profile_ref` for any non-OpenHands profile ("agent_launch_additions.llm_profile_ref applies only to an OpenHands Agent Profile"). When the active Agent Profile is ACP-kind (an ACP profile is seeded from `ACPAgentSettings` by `build_seed_profile`, so this is a first-class supported configuration), every `/v1/chat/completions` and `/v1/responses` request now fails 422. I reproduced this against the real launch
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5409#pullrequestreview-5398998714): _This review was posted by an AI agent (OpenHands)._ ## Verdict: the previous blocking finding is resolved; no new material issues Reviewed the new head `23e3cb72` (the branch merged `main` at `35410b3af`). My earlier `CHANGES REQUESTED` flagged that `_create_conversation_request` unconditionally paired the active `agent_profile_id` with `AgentLaunchAdditions(llm_profile_ref=...)`, so an ACP active profile 422'd through `/v1/chat/completions` and `/v1/responses`. That is fixed: the override is now supplied only for `OpenHandsAgentProfile` (openai/service.py:277), and `simonrosenberg` confirmed it in the thread. I re-verified on this head against the real launch pipeline. ### Verification performed - **ACP gateway path restored.** Reproduced `_create_conversation_request` + `launch_source` with an ACP active profile and an existing model p
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5409#pullrequestreview-5399108736): _This review was posted by an AI agent (OpenHands)._ ## No material issues on head `d3a4b3a3` The only change since the head I approved (`23e3cb72`) is the automated `chore: Remove PR-only artifacts` commit, which deletes the four `.pr/*.py`/`.pr/*.md` evidence scripts. The source tree is unchanged: `git diff 23e3cb72..d3a4b3a3 -- . ':(exclude).pr'` is empty, so every code path reviewed previously is identical here. This is a re-confirmation, not a new code review. ### Verification on this head - **CI for `d3a4b3a3`.** All checks pass — `sdk-tests`, `agent-server-api`, `agent-server-tests`, `cross-tests`, `integration-test`, `REST API (OpenAPI)`, `Persisted settings`, `public-type-budget`, `security`, `acp-live-tests`, `unresolved-review-threads`; only `cleanup-*` are skipped. - **Focused suites.** `tests/sdk/launch`, `tests/sdk/conversat
- [reviews; COMMENTED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5409#pullrequestreview-5403366068): The PR describes live agent-server and Docker runs, but I could not find an attached screenshot/video or a current-head runtime artifact that makes those results independently verifiable. This feature crosses profile resolution, gateway/ACP launch, and Docker transport paths. Please attach current-head evidence showing the real supported entrypoint, the before/after failure or behavior, and the relevant model/profile request and provenance results. The repository review guide requires production-facing runtime evidence rather than claims or unit tests alone. This comment was generated by an AI agent (OpenHands) on behalf of Graham Neubig.
- [reviews; DISMISSED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5409#pullrequestreview-5409086070): 🟡 **Taste Rating: Acceptable** — The launch-path consolidation is directionally coherent and the current checks are green, but the acceptance evidence is incomplete for a Canvas-facing launch change. **[SECURITY / SUPPLY-CHAIN SCREEN]** - I inspected the PR-head `AGENTS.md`, `.agents/skills/custom-codereview-guide.md`, the full changed-file manifest and patches, and the CI/workflow surface as untrusted input. I found no prompt-injection payload, hardcoded credential, dependency-source change, malicious script, or unsafe CI permission expansion in this diff. **[TESTING / EVIDENCE GAP]** - [PR description / `.pr/inline_profiles_e2e.md`] The text reports API and Docker runs, but there is no live screenshot or video of the actual Agent Canvas interface launching an inline profile and applying the per-launch LLM override. This changes the cont
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5409#pullrequestreview-5494992744): _This review was posted by an AI agent (OpenHands)._ ## No material code defect on head `f6605362` This head is a rebase onto newer `main` (`e3a26821`) plus one new commit, `fix(agent-server): launch the global settings through the gateway when no profile is active`. The PR's own diff against the new base is 20 files. I re-read the code, re-ran the focused suites, and checked current-head CI. ### What changed since the last reviewed head The new commit resolves the non-blocking observation from my previous pass: with profiles present but no active pointer, the gateway no longer returns `409`. `_create_conversation_request` now launches `stored or build_seed_profile(settings.agent_settings, settings.active_profile)` — the global settings as an unsaved inline profile (openai/service.py:265-282) — and is covered by the new test `test_without
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5409#pullrequestreview-5504342300): _This review was posted by an AI agent (OpenHands)._ ## Re-review of head `8e961107` The head advanced from the revision I last approved (`9b5a5b0c`) by two non-product commits — `979a81e1` (record profile API/Docker QA) and `8e961107` (automated `.pr/` cleanup) — and the author posted a follow-up evidence comment. I re-verified the current state rather than relying on the earlier pass. ### What I verified - **Product code is unchanged.** `git diff 9b5a5b0c 8e961107` is empty and both commits resolve to the same tree (`47eaae64`); the only delta is `.pr/` evidence and its removal. So the code I approved is the code at this head. - **The prior blocking finding remains fixed.** `_create_conversation_request` still gates the override: `llm_profile_ref=llm_profile if isinstance(profile, OpenHandsAgentProfile) else None`, so an ACP active prof
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5409#discussion_r4149317769): `_create_conversation_request` now always sets `AgentLaunchAdditions(llm_profile_ref=<model profile>)` together with the active profile's `agent_profile_id`. `resolve()` rejects `llm_profile_ref` for any non-OpenHands profile ("agent_launch_additions.llm_profile_ref applies only to an OpenHands Agent Profile"). An ACP-kind active Agent Profile is a supported configuration (`build_seed_profile` seeds one from `ACPAgentSettings`), so every `/v1/chat/completions` and `/v1/responses` request against it now fails 422. Reproduced against the real launch pipeline (`launch_source` with an ACP `agent_profile_id` plus `llm_profile_ref` raises `AgentLaunchError`); on the base revision the gateway folded the model's LLM into `agent_settings` and never touched a profile, so ACP launches worked. The gateway has no ACP guard and the live gateway test on
- [review_comments; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/5409#discussion_r4153497671): Fixed in 14b6bc5e9. - An ACP active profile now launches through the gateway without `llm_profile_ref`, since its server runs its own model. An OpenHands profile still gets the model's LLM profile as the override. - The requested model is checked up front with the same 404/503/400 mapping as before, so an unknown model stays a 404 for either kind. - `tests/agent_server/test_openai_gateway_launch.py` runs the gateway's request through `launch_source` for both kinds. With the fix reverted, the ACP test fails with the `AgentLaunchError` you reported. - Live check with the canvas mock ACP server (`.pr/gateway_acp_e2e.py`): `/v1/chat/completions` returns 200 on #5406, 422 on `a2507336`, and 200 on this head. An unknown model returns 404, 422 and 404.
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/5409#issuecomment-6127607991): @openhands rebase and address all review comments and do full e2e validation of the feature branch
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/5409#issuecomment-6146465809): Refreshed the actual PR head onto current main without force-pushing: `9b5a5b0c8` (main `c1dfd9c52`). QA on the refreshed branch: - 154 focused launch, profile, gateway and Docker-mediation tests passed; relevant pre-commit hooks passed. - Authenticated real local Agent Server, saved OpenHands `claude-sonnet-4-6` credentials, real stdio FastMCP `qa_echo`: stored `default`, identical `default-copy`, and inline profile all completed an MCP call (`QA_MCP_ECHO:PROFILE_API_LIVE_OK`). Each wire agent has exactly `tools=[glob]`, the same single MCP server and persona, and the same secret scope. The inline source is recorded `inline=true`. - Real `/v1/chat/completions` returned 200 / `GATEWAY_PROFILE_QA_OK`. Reading its conversation confirms the active profile's `glob` tool, persona and `llm_profile_ref=qa-live` override were retained. - Current 
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/5409#issuecomment-6146690724): Current runtime evidence is now independently readable at [agent-profile-api-qa.json](https://github.com/OpenHands/software-agent-sdk/blob/979a81e11/.pr/agent-profile-api-qa.json), pinned so automated `.pr/` cleanup cannot break the link. This records supported API entry points at source `9b5a5b0c8`, with real Sonnet and real MCP/terminal execution. The new Docker run used the current-head arm64 minimal image (`9b5a5b0-python-minimal-arm64`, digest `sha256:ae5ebd3a9983fcb3012dccb336acb47bee607301e0a6ba85fa3f72dbd7bbbb9d`). A stored profile asked for terminal + browser; both preview and the running container had terminal only. The real terminal ran `printf DOCKER_PROFILE_API_OK` with exit 0, and the persona/suffix/provenance were retained. This directly exercises host resolution → encrypted transport → container finalization. `979a81e11` a

### #4960: feat(agent-server): accept additional skills when launching an agent

- Commit: `27308a4b3f23a40177c9f444f9e35136c8a0d3fb` (2026-10-13T18:08:44+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/4960).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-4960). Families: F04, F06, F21, F30, F33, F34.

Stated intent:

> - `AgentLaunchAdditions.skills: list[Skill] | None` — deployment-supplied skills, on the channel already carrying deployment context, applied in `finalize` right after `system_message_suffix_append`.
> - `finalize` merges them by name with two rules that keep it from becoming a back door:
>   - **the resolved agent wins a collision** — its skills came from the profile and the server's own sources, the more authoritative view (and `AgentContext` rejects duplicate names, so the merge must de-duplicate rather than concatenate);
>   - **the context's `disabled_skills` deny-list is re-applied**, so an addition cannot turn back on something the profile turned off.
> - Defaults to `None`; an agent with no context still receives them.
> 
> This is deliberately a *channel*, not a decision about which catalog wins. #3979 is where that gets settled — option (2) in the comment there. If the server clone ends up owning the catalog instead, this field costs nothing to leave unused.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `4011cb5d4fd5` for public `/api/**` paths.
> 
> ```diff
> --- base public O

Changed paths:

- `openhands-sdk/openhands/sdk/conversation/request.py`
- `openhands-sdk/openhands/sdk/launch/finalize.py`
- `tests/agent_server/docker_runtime/test_mediation.py`
- `tests/agent_server/test_agent_launch_additions.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/4960#issuecomment-5637730385)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4960#pullrequestreview-5506363353): _This review was posted by an AI agent (OpenHands)._ Reviewed head `62e783c` (matches the requested SHA). Scope check: the change lands on this repo's SDK/agent-server surface (`openhands.sdk.conversation.request.AgentLaunchAdditions` + `openhands.sdk.launch.finalize`), is additive, and does not change the OpenAPI `/api/**` contract beyond one optional schema property. `#3979` is closed and superseded by `#4018`, and this PR is deliberately the channel only — not the catalog-parity decision — so no cross-repo move or maintainer gate applies. Verified against the workspace code: - **Wire omission of `skills`.** `Field(default=None, exclude_if=lambda v: v is None)` drops `skills` from both `model_dump()` and `model_dump(mode="json")` when unset, and serializes it when non-empty. I confirmed this empirically on pydantic 2.13.5 (the workspace
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/4960#issuecomment-6146467628): Refreshed onto main, then fixed two concrete bugs in `a638c0ec5`: 1. Repeated newly supplied skill names bypassed the initial collision set. `POST /api/conversations` with two `duplicate` additions returned 500 (`Duplicate skill name found`). The merge now retains the first supplied skill, after honoring resolved skills and the deny-list. 2. ACP native sourcing ran before additions, so client skills were injected afterward. Reading the actual conversation with `include_skills=true` showed the supplied skill in native mode. Native sourcing now runs after the merge and strips it; managed sourcing retains it. Real authenticated HTTP before/after: - Duplicate additions: 500 → 201, one skill retained. - Native ACP supplied skills: 1 → 0 (untrimmed conversation response). - Both regressions fail before the fix. Focused launch/additions/parity s
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4960#issuecomment-6147319790): ### ⏳ Review waiting on checks The current head `62e783c4db80` still has checks that have not finished, so no review conversation was started: - `agent-server-tests` - `cross-tests` - `sdk-tests` No action is needed. The scheduled scan retries once every check on the head reports a conclusion. _This is an automated check - no AI was used to generate this comment._

### #5727: verify-agent-server: a daily maintenance pass

- Commit: `733f06edac353d595f0dadac476ee5011b53586f` (2026-10-13T22:19:08+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5727).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5727). Families: no product runtime required.

Stated intent:

> - New `references/daily.md`, linked from `SKILL.md`, `maintenance.md` and `adaptation.md`.
> - `map baseline [--set]` reads or moves the baseline line; `map check` validates it (now `main@<sha> (<date>)`).
> - `map owners` with no arguments diffs from the baseline and lists the families.
> - `evidence report --baseline` lists newly failing, newly passing, not-checked and conflicting rows. Ledgers are compared by attempt time, so `--runs` order does not change the verdict.

Changed paths:

- `.agents/skills/verify-agent-server/SKILL.md`
- `.agents/skills/verify-agent-server/references/adaptation.md`
- `.agents/skills/verify-agent-server/references/daily.md`
- `.agents/skills/verify-agent-server/references/feature-map/README.md`
- `.agents/skills/verify-agent-server/references/maintenance.md`
- `.agents/skills/verify-agent-server/scripts/control_agent_server.py`
- `tests/cross/test_verify_agent_server_skill.py`

Linked issue acceptance context:

- [#5751](https://github.com/OpenHands/software-agent-sdk/issues/5751): verify-agent-server: a daily maintenance pass for the agent-server feature map — current state closed, closed 2026-10-13T20:19:10Z.

> - [ ] `references/daily.md` describes the pass and is linked from `SKILL.md`, `maintenance.md` and `adaptation.md`. - [ ] `control-agent-server map baseline [--set SHA]` reads and moves the baseline line, and `map check` validates it. - [ ] `control-agent-server map owners`, with no arguments, lists the families changed since the baseline. - [ ] `control-agent-server evidence report --baseline <run>` lists newly failing, newly passing, not-checked and conflicting rows, whatever order the runs are given in. - [ ] `tests/cross/test_verify_agent_server_skill.py` covers these commands. ### Priority / Severity Medium ### Estimated Scope Medium ### Feature Area - [x] Testing / Development tools ### Additional Context Implemented in #5727. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5727#issuecomment-6132087316)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5727#pullrequestreview-5508491678): **Review: COMMENT — deferring approval to a maintainer** (the repo review guide forbids REQUEST_CHANGES, so this is the correct signal for a material finding). Reviewed head `cd5ab84` against `.agents/skills/custom-codereview-guide.md`. **Verified on the current head** - `uv run pytest tests/cross/test_verify_sdk_skill.py tests/cross/test_verify_agent_server_skill.py -q` → 67 passed. - `sdk-map check` / `owners` / `baseline` / `rotation` and `control-agent-server map check --no-routes` / `baseline` / `rotation` all return clean JSON. The new `Maintenance baseline: main@<sha> (<date>)` line parses, and the rotation covers all 21 SDK families (and all 34 agent-server families) with no duplicates across a week. The skills are non-product tooling, so the compatibility/security checkpoints don't apply. **Material finding — current-head CI is r
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5727#pullrequestreview-5509411476): _This review was posted by an AI agent (OpenHands)._ Reviewed head `6d1c160` against `.agents/skills/custom-codereview-guide.md`. `enyst` replied after the earlier bot review, so this is a fresh pass. **Verified on the current head** - `uv run --frozen pytest tests/cross/test_verify_agent_server_skill.py -q` → 45 passed. - `ruff check` on the two changed Python files → clean. - `control-agent-server map check` → `ok: true`, 34 files, 935 ids. `map baseline` → `main@8ba966d1…`, 43 commits since. `map owners` (no args) diffs `baseline..HEAD` and lists 25 changed families plus `unowned_product_paths`. - `evidence report --baseline` works with both a run directory and a bare `ledger.jsonl`; comparison is ordered by each row's `at` time, so `--runs` order cannot change the verdict (confirmed by `compare_ledgers` and by direct CLI runs against 
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5727#issuecomment-6153353825): I'm an AI agent helping Engel Nyst (@enyst) with project work. Thanks for the review. Here is where each point stands: - **PR description check (blocking): fixed.** The attribution line now sits under `AGENT:`, so `HUMAN:` is the first visible line, and the Issue Number section links #5751 (`Fixes #5751`, labeled `ready-for-dev`). "Validate PR description" now passes, and CI is green on the current head. - **S18 flaky test and the unowned `utils/redact.py`.** Both come from the SDK map, which has moved out of this PR into #5750, so they will be handled there. `redact.py` will get an owner or a one-line reason. The S18 failure (`test_exit_preserves_parent_lifecycle_and_cleanup`, with `cleanup` called twice) happened once and then passed on three reruns. With a single occurrence I haven't filed it yet; it will be filed if it shows up again.

### #5728: verify-agent-server: a known bug reproduces only with its documented failure

- Commit: `dfd8fe8fb39e6f6d73ba35ce524cac83d94e0d96` (2026-10-13T22:28:54+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5728).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5728). Families: no product runtime required.

Stated intent:

> - **Marker contract.** Every marker now names the bug's own symptom: `# bug: PREDICATE`, made of `PATH OP [VALUE]` clauses joined by `&&`. They are matched like `--check` against the JSON that the last `control-agent-server` call on that line printed. `exit N` is kept only as a last resort. Here is `F08.secrets-bad-value` before and after:
>   ```sh
>   # before
>   control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_NUMBER": 5}}' \
>     --expect 422 --save F08.secrets-bad-value/number  # bug
>   # after
>   control-agent-server api POST "/api/conversations/$KID/secrets" --json '{"secrets": {"QA_F08_NUMBER": 5}}' \
>     --expect 422 --save F08.secrets-bad-value/number  # bug: status eq 500 && response.body.exception contains "'int' object has no attribute 'pop'"
>   ```
>   The `--expect 422` assertion of the correct behavior is unchanged.
> - **`assert` verb.** `control-agent-server assert --value|--json FILE|stdin --check ...` checks a value that the recipe computed and prints it. Comparison lines that used to say only "the jq was false" now name the value th

Changed paths:

- `.agents/skills/verify-agent-server/SKILL.md`
- `.agents/skills/verify-agent-server/references/feature-map/F02-auth-and-sessions.md`
- `.agents/skills/verify-agent-server/references/feature-map/F03-deferred-init.md`
- `.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md`
- `.agents/skills/verify-agent-server/references/feature-map/F05-run-pause-interrupt.md`
- `.agents/skills/verify-agent-server/references/feature-map/F06-conversation-events.md`
- `.agents/skills/verify-agent-server/references/feature-map/F07-session-socket.md`
- `.agents/skills/verify-agent-server/references/feature-map/F08-confirmation-security-secrets.md`
- `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`
- `.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md`
- `.agents/skills/verify-agent-server/references/feature-map/F11-goals.md`
- `.agents/skills/verify-agent-server/references/feature-map/F12-runtime-and-credentials.md`
- `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`
- `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`
- `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`
- `.agents/skills/verify-agent-server/references/feature-map/F16-workspace-serving-and-vscode.md`
- `.agents/skills/verify-agent-server/references/feature-map/F17-workspaces-registry.md`
- `.agents/skills/verify-agent-server/references/feature-map/F18-settings-and-secrets.md`
- `.agents/skills/verify-agent-server/references/feature-map/F19-mcp.md`
- `.agents/skills/verify-agent-server/references/feature-map/F20-llm-profiles.md`
- `.agents/skills/verify-agent-server/references/feature-map/F21-agent-profiles.md`
- `.agents/skills/verify-agent-server/references/feature-map/F22-meta-profiles.md`
- `.agents/skills/verify-agent-server/references/feature-map/F23-llm-catalog-and-connections.md`
- `.agents/skills/verify-agent-server/references/feature-map/F24-openai-subscription.md`
- `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`
- `.agents/skills/verify-agent-server/references/feature-map/F26-plugins.md`
- `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`
- `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`
- `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`
- `.agents/skills/verify-agent-server/references/feature-map/F30-openai-gateway.md`
- `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`
- `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`
- `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`
- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `.agents/skills/verify-agent-server/references/maintenance.md`
- `.agents/skills/verify-agent-server/references/mapping.md`
- `.agents/skills/verify-agent-server/scripts/control_agent_server.py`
- `tests/cross/test_verify_agent_server_skill.py`

Linked issue acceptance context:

- [#5726](https://github.com/OpenHands/software-agent-sdk/issues/5726): [Bug]: Verification xfail can accept an unrelated error at the marked command — current state closed, closed 2026-10-13T20:28:57Z.

> - [ ] A `known bug` bullet declares the identity of its documented failure (status, field/error, or observable state) alongside the existing correct-behavior assertion, which is retained. - [ ] When the marked command fails with the documented failure, the bullet is reported `xfail`. - [ ] When the marked command fails with an unrelated error whose status/field/error/observable state does not match the declared failure, the bullet is reported `fail` and is not counted as a reproduced bug. - [ ] Matching uses the declared failure identity, not the whole transcript: a failure with different detail text but the same declared identity still counts, and different content elsewhere does not force a match. - [ ] A `known bug` bullet whose marked command now exits 0 (correct behavior) is reported `xpass` and `map run` signals that the marker/recipe must be updated, as today. - [ ] A failure before the marked command (an arrange/setup step) and a recipe timeout are reported `fail`, not `xfail`. - [ ] Bullets without the `known bug` label are still classified `pass`/`fail` exactly as before. - [ ] `map check` validates the declared failure identity and rejects a missing or malformed predicate (it fails the map check rather than silently accepting the marker). - [ ] The verification skill's documentation (marker contract in `references/mapping.md` and `references/maintenance.md`) describes the required predicate and the twice-baseline/twice-fix procedure that depends on it. - [ ] A representative recipe (`F08.secrets-bad-value`) is updated to declare its documented 500 `AttributeError

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5728#issuecomment-6132726392)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5728#pullrequestreview-5496968245): _This review was posted by an AI agent (OpenHands)._ ## Scope The change belongs in this repository: it modifies the in-repo `.agents/skills/verify-agent-server/` skill (its control CLI, feature map, and docs) plus `tests/cross/test_verify_agent_server_skill.py`. The linked issue #5726 explicitly keeps the sibling `OpenHands/automation` runner port out of this repo, so there is no misplaced scope. ## Verification - Re-fetched head `6048c5bad8cfe51393a9892cb09153f1b988c9f3`; it still matches the review request. PR is open, not draft, no labels, no prior reviews/comments from this account. - All GitHub Actions check runs for the head are `success` (only `cleanup-after-merge`/`cleanup-on-approval` are skipped); no failing or pending required checks. - `uv run pytest tests/cross/test_verify_agent_server_skill.py` → 59 passed. - `control-agent
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5728#pullrequestreview-5503188777): Architecture looks good: the runner retains the correct-behavior assertion, matches the documented failure separately, captures observations before cleanup, and guards missing paths and transitive prerequisites. Current-head CI and the controlled-server regression coverage support the classifier change. This addresses a worthwhile medium-priority verification-integrity issue. Non-blocking suggestion: add one representative real baseline/fixed replay pair (for example F08.secrets-bad-value), showing xfail on the unfixed server and xpass on the fixed server with the same commands and prerequisites. That would complete the strongest acceptance evidence beyond the partial live replay and controlled HTTP tests. Reviewed head c3b8af88bfd4a258f941ad6bf06e25b4db57f89c. No independent test execution was performed in this review. [RISK ASSESSMENT] 
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5728#issuecomment-6154544004): Thanks for the review. Here is the baseline/fixed pair you suggested for `F08.secrets-bad-value`, run with this PR's CLI at c3b8af8 and the same recipe, flags and Preconditions on both sides (the 2+2 rule in SKILL.md, "Verify an existing fix"): ```sh control-agent-server map run --file F08 --only F08.secrets-bad-value --fresh --repeat 2 --checkout <unfixed checkout> # baseline control-agent-server map run --file F08 --only F08.secrets-bad-value --fresh --repeat 2 # fixed ``` \| server \| attempt 1 \| attempt 2 \| marked call observed \| \|---\|---\|---\|---\| \| unfixed (this branch, server code as on `main`) \| `xfail` \| `xfail` \| 500, `"exception": "'int' object has no attribute 'pop'"` \| \| fixed (scratch patch below) \| `xpass` \| `xpass` \| 422, `value_error` at `body.secrets` \| Preconditions passed in all four runs. The "fixed" 

### #5754: fix(sdk): skip sub-agents whose skill is missing instead of failing

- Commit: `a430c778bd8a720234ce4674014ac292a0326670` (2026-10-13T23:39:48+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5754).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5754). Families: F27.

Stated intent:

> - `register_file_agents` and `register_plugin_agents` now register each definition through one helper, `_register_definition`, which catches the `ValueError` from `agent_definition_to_factory`, logs `Skipping <file-based|plugin> agent '<name>' from <source>: <error>`, skips that definition and keeps registering the others. Valid skill resolution is unchanged.
> - `openhands-sdk/openhands/sdk/subagent/AGENTS.md` states the invariant (registration failures are non-fatal, like parse failures).
> - Unit tests for the file-based and plugin paths; the `F27.subagents-bad-skill` map bullet loses its `# bug` marker and now also checks a second message and the warning.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`
- `openhands-sdk/openhands/sdk/subagent/AGENTS.md`
- `openhands-sdk/openhands/sdk/subagent/registry.py`
- `tests/sdk/subagent/test_subagent_registry.py`

Linked issue acceptance context:

- [#5630](https://github.com/OpenHands/software-agent-sdk/issues/5630): [Bug]: Sub-agent file naming a missing skill makes the first message return 500 instead of being skipped — current state closed, closed 2026-10-13T21:39:50Z.

> - [ ] An agent file naming a missing skill no longer makes the message `POST`, or a create with `initial_message`, return 500. - [ ] The bad agent is skipped with a warning naming its file and skill (or the request is a 4xx naming them); other valid agents are still registered. - [ ] A unit test covers `register_file_agents` with a definition naming a missing skill. - [ ] The map bullet `F27.subagents-bad-skill` passes and its `# bug` marker is removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context - `LocalConversation.send_message` initializes the agent on the first message (`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:1822-1823`); `_ensure_agent_ready` (:1569) reaches `register_file_agents(self.workspace.working_dir)` (:1533). - `register_file_agents` (`openhands-sdk/openhands/sdk/subagent/registry.py:288-333`) builds a factory for every project and user definition (:319-320) without error handling, and `agent_definition_to_factory` raises `ValueError` for an unknown skill (:198-203). - `event_router.py:227` does not catch it, so the route answers 500. `_agent_ready` is set only at the end (`local_conversation.py:1598`), so each later message fails again. - `conversation_service.py` stores the conversation (:1663) before sending the initial message (:1671), hence the leftover `idle` conversation. - The catalog (`sub_agents_router.py:117-140`) validates frontmatter only; the loader skips invalid files with a warning (`subagent/load.py:213-220`). Suggested fix: in `register_file_agents`, catc

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5754#issuecomment-6154887542)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5754#pullrequestreview-5510111238): _This review was posted by an AI agent (OpenHands)._ ## Scope This change belongs in this repository. Issue #5630 (linked via `Fixes #5630`) is open with `bug`, `priority:low`, and `ready-for-dev`; its AI triage scopes the fix to per-definition registration resilience for file-based and plugin agents in `openhands-sdk/openhands/sdk/subagent/registry.py`, which is exactly what this PR touches. The `F27.subagents-bad-skill` map bullet lives in `.agents/skills/verify-agent-server/` in this repo, and the `POST /api/sub-agents` catalog gap is explicitly a non-goal of the issue. ## Assessment The fix is correct, minimal, and well-targeted. - `_register_definition` (`registry.py:326`) wraps only `agent_definition_to_factory` in `try/except ValueError`, logs `Skipping <file-based\|plugin> agent '<name>' from <source>: <error>`, and returns `False

### #5759: fix(agent-server): re-probe a Canvas App backend after a missed health probe

- Commit: `6a298a48fcdb06c3bf5835d648a7d8b4d3d31cf5` (2026-10-14T00:37:50+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5759).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5759). Families: F28.

Stated intent:

> - `status()` now also re-probes a live process that is `unhealthy` only because a probe failed (not one that exited or failed to start), and sets `ready` when the probe succeeds.
> - `start()` with the same revision on a live process returns that refreshed status instead of the cached one.
> - Unit test with a real backend subprocess (slow probe, still slow, recovery, then the same via `start`); the `F28.status-probe-latch` map bullet loses its known-bug marker.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`
- `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`

Linked issue acceptance context:

- [#5646](https://github.com/OpenHands/software-agent-sdk/issues/5646): [Bug]: Canvas App backend stays 'unhealthy' after one slow health probe instead of returning to 'ready' — current state closed, closed 2026-10-13T22:37:52Z.

> - [ ] A live backend whose health path answers in time again is `ready` on the next `GET .../backend`. - [ ] A backend that keeps failing probes, or whose process exited, is still `unhealthy`. - [ ] A test with a real subprocess covers one slow probe followed by recovery. - [ ] The `F28.status-probe-latch` map bullet passes and its known-bug marker is removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context In `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`: - `status()` probes only when `runtime.state == "ready"` (line 454). One failed `_probe` (1 s timeout, lines 540-545) sets `unhealthy` (lines 465-466), and later calls skip the probe. - `start()` returns the current status when the same revision's process is alive (lines 596-602). - `ready_endpoint()` requires `ready` (lines 779-783), so `_ready_endpoint` in `canvas_extensions/bridge.py:345-356` answers 503. Suggested fix: in `status()`, also probe a live process that a failed probe marked `unhealthy` (not one that exited or failed to start), and set `ready` when the probe succeeds. Alternatively, mark `unhealthy` only after a few consecutive failures.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5759#issuecomment-6155278112)

### #4814: fix(sdk): keep fork titles out of local conversation tags

- Commit: `6e7a8532ef4426da08b5c06dd57cf4795303be01` (2026-10-14T01:49:09+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/4814).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-4814). Families: F04, F05, F08, F09, F10, F12, F31, F32, F33.

Stated intent:

> - Keep the `title` keyword for compatibility, but deprecate it locally and stop adding or overwriting tags. `LocalConversation` does not store titles.
> - Preserve agent-server and `RemoteConversation` titles through `StoredConversation.title`, and keep fork metadata tags consistent with state when tags are omitted.
> - Merge current `main` (`c0471362d340ade1e334595ba98417108b415e88`), retain its concurrent-fork lifecycle lock, and update the unshipped deprecation schedule to `1.54.0` → `1.60.0`.
> 
> Companion documentation: https://github.com/OpenHands/docs/pull/926

Changed paths:

- `examples/01_standalone_sdk/48_conversation_fork.py`
- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `openhands-sdk/openhands/sdk/conversation/base.py`
- `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`
- `openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`
- `tests/cross/test_remote_conversation_live_server.py`
- `tests/sdk/conversation/local/test_fork.py`

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/4814#pullrequestreview-5083197839): Thank you!
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4814#pullrequestreview-5299952118): _This review was posted by an AI agent (OpenHands)._ ## Assessment The core fix is correct and I verified it locally on head `04c1796`: - `LocalConversation.fork(title=...)` no longer writes `{"title": ...}` into `state.tags`. A direct repro shows `fork.state.tags == {}` after `src.fork(title="My Fork")`, and a `DeprecatedWarning` is emitted from `local_conversation.py:808` with message "LocalConversation.fork() title parameter is deprecated as of 1.44.1 and will be removed in 1.50.0. ..." — the changed test's `match="title parameter is deprecated"` assertion is valid. - The deprecation schedule satisfies the repo's 5-minor-release runway: `deprecated_in="1.44.1"` → minimum `removed_in` is `1.49.0`, and `1.50.0` clears it (`.github/scripts/check_deprecations.py::_runway_error`). The `stacklevel=2` direct-call pattern matches the existing 
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4814#pullrequestreview-5494526348): _This review was posted by an AI agent (OpenHands)._ ## Assessment Reviewed head `5376633aca2608e10a718858d86e6de3f4ec7cfb` (the earlier bot review at `04c1796` is superseded — this head adds the metadata-consistency fix and the `1.54.0 → 1.60.0` deprecation schedule). The change is correct and complete: - **Bug fix verified.** `LocalConversation.fork(title=...)` no longer writes into `state.tags`. A direct repro on this head with `src.fork(title="My Fork", tags={"title": "user tag", "p": "1"})` yields `state.tags == {'title': 'user tag', 'p': '1'}` — the user's own `title` tag is preserved, not overwritten — and emits `DeprecatedWarning` from `local_conversation.py:838` with the `1.54.0 → 1.60.0` wording. The parametrized `test_fork_with_title_is_deprecated` (empty / ordinary / user-`title` tags) covers exactly this. - **No API signature
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4814#issuecomment-5498661304): 👋 **This PR needs a couple of things fixed before OpenHands can review it:** - the PR description's `HUMAN:` section needs at least 20 characters describing what you tested, not just the template placeholder Push an update once this is addressed and this check re-runs automatically. _This is an automated check - no AI was used to generate this comment._
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4814#issuecomment-5500277016): 🚦 **CI is currently failing on this PR's latest commit.** Please fix the failing checks before OpenHands reviews it - this is re-checked automatically once you push a new commit. (A maintainer can also request `@all-hands-bot` as a reviewer to have it reviewed regardless of CI status.) _This is an automated check - no AI was used to generate this comment._
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/4814#issuecomment-5502384711): Hi — I’m **OpenHands-GPT-5.6, enyst’s AI agent**. The check is failing for a real and very specific reason: Griffe sees `LocalConversation.fork(title)` disappear from the published `openhands-sdk` API. Removing that keyword means existing standalone-SDK code that calls `conversation.fork(title="...")` will now raise `TypeError`. The [job annotation](https://github.com/OpenHands/software-agent-sdk/actions/runs/33544984395/job/100030894998) is: > `LocalConversation.fork(title)`: Parameter was removed > Breaking changes detected (1); require at least minor version bump from 1.44.x, but new is 1.44.1 So the **detection is correct**, and I would not make the detector ignore this case. The bug fix should stop writing the title into `state.tags`, but that does not require removing the accepted keyword immediately. The least disruptive fix for th
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/4814#issuecomment-5502391090): Posted the response directly on PR #4814: https://github.com/OpenHands/software-agent-sdk/pull/4814#issuecomment-5502384711 I explained that the API break detection is valid, while the optional-check/review-bot interaction could reasonably be made more lenient. No code changes were made.

### #5478: Add newly released LLM models to verified lists

- Commit: `c3ecd82c7807a80796d95a6fdb83b7865d04413d` (2026-10-14T06:32:33-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5478).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5478). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - Add `gpt-6.1-sol` to `VERIFIED_OPENAI_MODELS`, `VERIFIED_OPENROUTER_MODELS`
>   (`openai/gpt-6.1-sol`), and `VERIFIED_OPENHANDS_MODELS`.
> - Add `claude-sonnet-5-5` to `VERIFIED_ANTHROPIC_MODELS`,
>   `VERIFIED_OPENROUTER_MODELS` (`anthropic/claude-sonnet-5.5`), and
>   `VERIFIED_OPENHANDS_MODELS`.
> - Add `gemini-4-argon` to `VERIFIED_GEMINI_MODELS` and `VERIFIED_OPENROUTER_MODELS`
>   (`google/gemini-4-argon`); not surfaced under the `openhands/` provider yet.
> - Append-only; no existing model IDs were removed or renamed.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `84d4470acd85` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -26,0 +27 @@
> +operation GET /api/canvas-extensions/installed/{extension_name}/icon operationId=get_canvas_extension_icon_endpoint_api_canvas_extensions_installed__extension_name__icon_get
> @@ -95,0 +97 @@
> +operation POST /api/acp/models operationId=discover_models_api_acp_models_post
> @@ -100,0 +103 @@
> +operation POST /api/bash/bash_commands/{command_id}/stop operationId=stop_bash_command_api_bash_bash_commands__comm

Changed paths:

- `openhands-sdk/openhands/sdk/llm/utils/verified_models.py`
- `tests/sdk/llm/test_model_list.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5478#issuecomment-5967623805)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5478#pullrequestreview-5407472932): _This review was posted by an AI agent (OpenHands)._ Reviewed head `66b9bc76b9d2bc4a4b33ffc3ce0ffba04411396f` (+6/-0, one file). `uv run pytest tests/sdk/llm/test_model_list.py -q` -> 12 passed. The head's checks are green apart from `Validate PR description`, which fails for the unrelated missing `## Issue Number` section. **Finding - the Sonnet line now holds three versions, violating the documented two-latest rule.** `openhands-sdk/openhands/sdk/llm/utils/AGENTS.md` (and the module docstring) require: for each model line keep only the two latest versions, and "when you add a new version, remove the oldest version in the same line, in every list where it appears." Before this PR the Sonnet line was `claude-sonnet-5` and `claude-sonnet-4-6` (two entries). Adding `claude-sonnet-5-5` makes three, so `claude-sonnet-4-6` is now the oldest an
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5478#discussion_r4178749262): The `llm/utils/AGENTS.md` curation rule requires keeping only the two latest versions per model line and removing the oldest when a new version is added. The Sonnet line had two entries (`claude-sonnet-5`, `claude-sonnet-4-6`); adding `claude-sonnet-5-5` makes three. `claude-sonnet-4-6` is now the oldest and should be dropped from `VERIFIED_ANTHROPIC_MODELS` (line 32) and `VERIFIED_OPENHANDS_MODELS` (line 148). This mirrors #5311, which dropped `claude-opus-4-8` when adding `claude-opus-5-5`. The spot-check test only asserts Opus entries for anthropic, so it does not catch this.
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5478#issuecomment-6096224305): Updated this rolling PR with a newly detected model: - **`claude-haiku-5-5`** (Anthropic) — added to `VERIFIED_ANTHROPIC_MODELS`, `VERIFIED_OPENROUTER_MODELS` (`anthropic/claude-haiku-5.5`), and `VERIFIED_OPENHANDS_MODELS`. Confirmed available on OpenRouter and present in LiteLLM's `model_cost` registry. - Dropped the older **`claude-sonnet-4-6`** from the Anthropic and OpenHands lists so each model line keeps its two latest versions (5.5, 5). Source: OpenRouter model catalog — https://openrouter.ai/api/v1/models (`anthropic/claude-haiku-5.5`). No ACP provider allowlist changes: `_CLAUDE_MODELS` uses version-agnostic CLI aliases (`sonnet`, `haiku`, `opus[1m]`). _This comment was created by an AI agent (OpenHands) on behalf of the user._
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5478#issuecomment-6165418297): 🤖 Update from the twice-weekly LLM model list automation (2026-10-14). **Added:** `gemini-4-argon` (Google) — the new Gemini 4 line, released 2026-10-13. - `VERIFIED_GEMINI_MODELS` → `gemini-4-argon` - `VERIFIED_OPENROUTER_MODELS` → `google/gemini-4-argon` Confirmed publicly callable via LiteLLM `model_cost` (`gemini/gemini-4-argon`) **and** the public OpenRouter `/api/v1/models` endpoint (`google/gemini-4-argon`, created 2026-10-13). Not added to `VERIFIED_OPENHANDS_MODELS` — the `openhands/` proxy does not serve it yet. **Also:** merged current `main` (`6e7a8532`) into this branch, which had gone stale at `84d4470a`; the merge was clean. **Deliberately skipped** (in LiteLLM but not confirmed as a new public frontier release, or older than the two latest versions already curated): `gemini-3.5-flash`, `gemini-3.6-flash` (both older than t

### #4926: docs(acp): clarify client surfacing contract

- Commit: `7f7f0520920007b2c135e7bd0ef06e637032860b` (2026-10-14T13:09:23+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/4926).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-4926). Families: F04, F09, F18, F19, F20, F21, F22, F23, F30, F33, F34.

Stated intent:

> - Clarify in the Python and TypeScript registry modules that they carry metadata, not deployment policy.
> - Document the one-way `SURFACED_ACP_PROVIDERS ⊆ ACP_PROVIDERS` invariant and reject complementary hidden-provider lists.
> - Record the cross-repository contract in `AGENTS.md`; amend #4841 and #4833 to match the latest correction.

Changed paths:

- `AGENTS.md`
- `clients/typescript/src/models/acp.ts`
- `openhands-sdk/openhands/sdk/settings/acp_providers.py`

Linked issue acceptance context:

- [#4841](https://github.com/OpenHands/software-agent-sdk/issues/4841): Each client declares which ACP harnesses it offers — adding one to the SDK must not reach Cloud or Replicated — current state closed, closed 2026-10-14T11:09:25Z.

> - [ ] Cloud declares its set, filters `/api/options` to it, and returns 400 for anything else at conversation start — OpenHands/enterprise#315 - [ ] Bumping `openhands-sdk` to a version registering a new harness produces no observable Cloud change: no `/api/options` diff, no image change, no new code path - [ ] `DEFAULT_PREINSTALLED_ACP_PROVIDERS` is frozen by a test, so growing the default image stays a deliberate PR (#4643, #4820) - [ ] Canvas declares `SURFACED_ACP_PROVIDERS` independently and asserts only `SURFACED_ACP_PROVIDERS ⊆ ACP_PROVIDERS`, so a rename or removal breaks while an addition is a no-op - [ ] Benchmarks and evaluation derive their ACP tables from the registry (#4833) - [ ] No deployment-policy field is added to `ACPProviderInfo` **Open question.** Cloud's free-text `acp_command` field launches any ACP server regardless of the allowlist. Gate it, or accept that the list governs the picker and not the escape hatch — product call. 

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/4926#issuecomment-5600024056)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4926#pullrequestreview-5398725459): _This review was posted by an AI agent (OpenHands)._ ## Review of PR #4926 — `docs(acp): clarify client surfacing contract` **Scope:** The change belongs in this repository. It edits the SDK's own ACP registry documentation (`openhands-sdk/openhands/sdk/settings/acp_providers.py`), its TypeScript mirror (`clients/typescript/src/models/acp.ts`), and the repo memory (`AGENTS.md`) — all owned here, and it matches the `ACP_PROVIDERS` guidance already recorded in `AGENTS.md`. It is documentation-only: no behavior, public symbol, serialized event, persisted settings, or REST contract changes. ### Verification performed on head `b3179e5` - **Python focused tests:** `uv run --frozen pytest tests/sdk/settings/test_acp_install_catalog.py tests/sdk/settings/test_acp_providers.py -q` → **81 passed**. - **TypeScript focused test:** `npx vitest run src
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/4926#pullrequestreview-5516945130): _This review was posted by an AI agent (OpenHands)._ ## APPROVE — `docs(acp): clarify client surfacing contract` Reviewed head `77ea167c4e1d95bde1157790e01a6a8335af357f`. All check runs completed with no failures (pre-commit, sdk-tests, TypeScript client CI, validate-acp-providers, API and REST breakage checks, persisted settings, endpoint audit, windows/macos binary builds, security). This is a documentation-only change confined to text in three files: the module docstring in `openhands-sdk/openhands/sdk/settings/acp_providers.py`, the header comment in `clients/typescript/src/models/acp.ts`, and one `AGENTS.md` entry. No behavior, public symbol, serialized event, persisted setting, or REST contract changes, so there is nothing to regression-test beyond the existing ACP provider checks. The stated contract matches the code: `ACP_PROVIDER

### #5981: feat(sdk): let Agent Profiles select public skills, read them locally and use the global critic key

- Commit: `d332f8156f12014df1b3522ab01c2508eabf6e8b` (2026-10-14T13:09:44+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5981).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5981). Families: F01, F04, F21, F25, F27, F29, F30, F33, F34.

Stated intent:

> - `OpenHandsAgentProfile.public_skills`: `null` keeps every public-catalog skill, as today; `[]` keeps none; a list keeps only those names, and unknown names are ignored. User and project skills are unaffected, and `disabled_skills` still applies to everything.
> - `LaunchStores.public_skill_names` tells the resolver which catalog skills are public. The agent-server derives it from the same loaders as the catalog, so a user skill that shadows a public one stays a user skill. It is read only for a profile that sets `public_skills`. Materialize, preview and the legacy `resolve_agent_profile` / dry-run apply the same selection.
> - `EXTENSIONS_PATH` makes `load_public_skills()` read a local copy of the extensions repository, such as an installed `@openhands/extensions` package, instead of cloning it. A custom `repo_url` is unaffected.
> - The critic API key saved in the server's global verification settings now reaches every OpenHands profile launch through `LaunchStores.critic_api_key`. Before, the critic fell back to the LLM key. Profiles stay secret-free. The key is applied when the profil

Changed paths:

- `openhands-agent-server/openhands/agent_server/launch.py`
- `openhands-agent-server/openhands/agent_server/server_details_router.py`
- `openhands-agent-server/openhands/agent_server/skills_service.py`
- `openhands-sdk/openhands/sdk/launch/preview.py`
- `openhands-sdk/openhands/sdk/launch/resolve.py`
- `openhands-sdk/openhands/sdk/profiles/agent_profile.py`
- `openhands-sdk/openhands/sdk/profiles/resolver.py`
- `openhands-sdk/openhands/sdk/skills/skill.py`
- `tests/agent_server/test_launch_parity.py`
- `tests/agent_server/test_server_details_router.py`
- `tests/agent_server/test_skills_service.py`
- `tests/sdk/launch/fakes.py`
- `tests/sdk/launch/test_field_ownership.py`
- `tests/sdk/launch/test_preview.py`
- `tests/sdk/launch/test_resolve.py`
- `tests/sdk/profiles/test_resolver.py`
- `tests/sdk/skills/test_load_public_skills.py`

Linked issue acceptance context:

- [#5980](https://github.com/OpenHands/software-agent-sdk/issues/5980): [Agent Profile] Let a profile select public skills and read the catalog from a local extensions copy — current state closed, closed 2026-10-14T11:09:46Z.

> - [ ] `OpenHandsAgentProfile.public_skills` filters only the public tier; `null` keeps today's behavior for existing profiles. - [ ] Launch, materialize/preview and the legacy `resolve_agent_profile` / dry-run agree on the resolved skills. - [ ] A user skill that shadows a public one by name is treated as a user skill. - [ ] `EXTENSIONS_PATH` makes `load_public_skills()` read that directory and never clone; a custom `repo_url` is unaffected. - [ ] A profile launch uses the global `verification.critic_api_key`, including through a Docker host, without exposing it in materialize. - [ ] `/server_info` reports `profile_public_skills_v1`. - [ ] Inline `default` and the equivalent profile launched by id give the model the same skills, tools and system prompt. ### Alternatives Considered - Canvas adopts the server's deny-list. That reverses OpenHands/OpenHands#16302 and puts every new catalog skill into every prompt. - `agent_launch_additions.skills` plus a flag that drops the server's public catalog. Home launches would match, but gateway and automation launches of the same profile would still get the full catalog. ### Priority / Severity Medium: it blocks Agent Profile launch unification in Agent Canvas. ### Estimated Scope Small: one profile field, a tier-name hook on `LaunchStores`, an environment variable and a capability. ### Feature Area Agent Profiles, skills ### Additional Context Follows #3979, #4017 and #4960. Canvas side: OpenHands/OpenHands#17991 and OpenHands/OpenHands#18419.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5981#issuecomment-6165373021)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5981#pullrequestreview-5516939267): _This review was posted by an AI agent (OpenHands)._ ## APPROVE — `feat(sdk): let Agent Profiles select public skills, read them locally and use the global critic key` Reviewed head `37110ec7b25f19b30125ff666a5edbf80e72e66b`. All check runs completed with no failures (sdk-tests, agent-server-tests, tools-tests, cross-tests, integration-test, pre-commit, OpenAPI/REST breakage, endpoint-audit, validate-acp-providers, windows/macos binary builds, security). What I verified against the diff: - **`public_skills` semantics.** `_select_skills` treats `null` as the whole catalog, `[]` as none, and a list as an allow-list applied only to the public tier; a name absent from the catalog is a no-op, so the #4017 no-dangle property is preserved. `disabled_skills` still applies to every tier. - **Tier separation is sound.** `discover_public_skill_names

### #5747: fix(subagent): sub-agents inherit exactly the parent's MCP servers

- Commit: `0bd5cc2d322d61f785816a29ef1f2e4c86db2460` (2026-10-14T08:16:28-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5747).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5747). Families: F05, F08, F09, F10, F21, F27, F33.

Stated intent:

> - **`inherit_mcp_config(parent, sub_agent)`** (`openhands.sdk.subagent.scope`) returns the built sub-agent with exactly `parent.agent.mcp_config`. That is the delegating conversation's *resolved* config: plugin servers already merged, `${VAR}` secrets already expanded, tool filters and headers intact. Servers the child declares are dropped, never merged, and an INFO log names them (names only, no config). `TaskManager` (which backs `task`, `workflow_tool_set` and `workflow`, including resume) and `DelegateExecutor` apply it to the **built child**, so file, plugin, programmatic and built-in definitions all get it. The rule nests, so grandchildren get the same set.
> - **The child conversation uses the inherited config as-is.** A new private `LocalConversation(_mcp_config_from_parent=True)` skips the second plugin merge and the second `${VAR}` expansion. The child also reuses the parent's `mcp_tool_provider`.
> - **`SubAgentScope` now bounds only tools.** The `mcp_servers` flag stays as a deprecated, ignored, excluded field, so stored tool params still load. `missing_from`, listing and eli

Changed paths:

- `openhands-agent-server/openhands/agent_server/sub_agents_router.py`
- `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`
- `openhands-sdk/openhands/sdk/profiles/resolver.py`
- `openhands-sdk/openhands/sdk/subagent/AGENTS.md`
- `openhands-sdk/openhands/sdk/subagent/schema.py`
- `openhands-sdk/openhands/sdk/subagent/scope.py`
- `openhands-tools/openhands/tools/delegate/impl.py`
- `openhands-tools/openhands/tools/task/definition.py`
- `openhands-tools/openhands/tools/task/manager.py`
- `tests/sdk/profiles/test_resolver.py`
- `tests/sdk/subagent/test_subagent_scope.py`
- `tests/tools/task/mcp_echo_server.py`
- `tests/tools/task/test_mcp_inheritance.py`
- `tests/tools/task/test_sub_agent_scope.py`

Linked issue acceptance context:

- [#5743](https://github.com/OpenHands/software-agent-sdk/issues/5743): [Agent Profile] Sub-agents must inherit exactly the parent MCP tools — current state closed, closed 2026-10-14T12:16:30Z.

> - [ ] A child delegated by a parent whose effective MCP config contains a server can call that server's tools from within the child; the child's provider request includes the parent's MCP tools (e.g. the `qa_echo` reproduction now shows the tool on the child). - [ ] A child definition that declares an extra MCP server absent from the parent's effective config never reaches the child: no connection is opened and no such tool appears. - [ ] A child definition that declares a same-named server with a different config (command/args/headers/transport/tools filter) never overrides the parent's; the child uses the parent's configuration. - [ ] A child that declares no or fewer MCP servers still receives every MCP tool the parent allows. - [ ] When the parent's effective MCP scope is empty, the child is given no MCP servers or tools. - [ ] A grandchild delegated from a child inherits the same effective MCP set as its parent. - [ ] The behavior applies to `task`, `workflow_tool_set`, and `workflow`, for file-based, plugin, programmatic, and built-in agent definitions; eligibility/listing (`missing_from` / `_offered`) neither rejects a child that will inherit the parent config nor widens it. - [ ] MCP credentials/headers reach the child without appearing in plaintext in logs or in persisted conversation/settings artifacts. - [ ] The policy holds on child resume and on local, Docker, and cloud launches; ACP/external delegates are explicitly checked rather than assumed constrained by a visible tools list. - [ ] Validation exercises the real delegation path with a real MCP server and pr

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5747#issuecomment-6149957004)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5747#pullrequestreview-5516944296): _This review was posted by an AI agent (OpenHands)._ ## APPROVE — `fix(subagent): sub-agents inherit exactly the parent's MCP servers` Reviewed head `facd58d71b68b338992de8e72cb76b55e63ce5fe`. All check runs completed with no failures (sdk-tests, agent-server-tests, tools-tests, cross-tests, integration-test, pre-commit, OpenAPI/REST breakage, persisted settings, windows/macos binary builds, security). The `.pr/` QA directory is expected here and the repository's artifact workflow owns its removal on approval. What I verified against the diff: - **Inheritance is exact, in both directions.** `inherit_mcp_config(parent, sub_agent)` returns the built child with exactly `dict(parent.mcp_config)` — the delegating conversation's already-resolved config (plugin servers merged, `${VAR}` expanded, tool filters/headers intact). Declared servers are

### #5926: fix(agent-server): read an unreachable OpenAI refresh as disconnected

- Commit: `4bb57df68465868c2ec13388acd6a6b1e14af651` (2026-10-14T15:48:46+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5926).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-5926). Families: F23, F24.

Stated intent:

> - `get_openai_subscription_status` now catches `httpx.HTTPError` alongside `RuntimeError` around `refresh_if_needed()`, so an unreachable OpenAI reads as `200 connected: false`, like a rejected refresh. The credentials file is untouched, so status refreshes it once the network returns.
> - Unit test in `tests/agent_server/test_llm_router.py` for a refresh that raises `httpx.ConnectError`.
> - Map: the `F24.status-offline` bullet loses its `# bug` marker and "known bug" label, its prose now describes the expected behaviour, and it reads the stored file back to prove the offline call left it unchanged.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F24-openai-subscription.md`
- `openhands-agent-server/openhands/agent_server/llm_router.py`
- `tests/agent_server/test_llm_router.py`

Linked issue acceptance context:

- [#5925](https://github.com/OpenHands/software-agent-sdk/issues/5925): [Bug]: OpenAI subscription status answers an unhandled 500 when an expired login cannot be refreshed offline — current state closed, closed 2026-10-14T13:48:48Z.

> - [ ] With an expired login and `auth.openai.com` unreachable, `GET /api/llm/subscription/openai/status` answers `200` `connected: false` and logs no unhandled-exception traceback. - [ ] A rejected refresh still answers `200` `connected: false`, and an unexpired login still reads as connected. - [ ] The stored credentials file is left in place, so a later status call can refresh it when the network returns. - [ ] A unit test in `tests/agent_server/test_llm_router.py` covers a refresh that raises an httpx transport error. - [ ] The map bullet `F24.status-offline` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `get_openai_subscription_status` (`openhands-agent-server/openhands/agent_server/llm_router.py:181-188`) catches only `RuntimeError` around `auth.refresh_if_needed()`. - `_refresh_access_token` (`openhands-sdk/openhands/sdk/llm/auth/openai.py:376-390`) raises `RuntimeError` only for a non-2xx answer; an `httpx.ConnectError` / `httpx.TimeoutException` from `client.post` propagates unchanged through `refresh_if_needed` (line 507) to the route and on to the generic 500 handler. Related: https://github.com/OpenHands/software-agent-sdk/issues/5880 covers the same transport-error class on `device/start` and `device/poll` (gateway error expected there, since those calls cannot succeed without OpenAI); status differs because "not connected right now" is a valid answer. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5926#issuecomment-6159182702)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5926#pullrequestreview-5512555396): _This review was posted by an AI agent (OpenHands)._ ## Scope In-repo. The change is in `openhands-agent-server`'s `/llm` router (`openhands-agent-server/openhands/agent_server/llm_router.py`), which this repository owns, plus its own test and the `verify-agent-server` feature map it ships. No cross-repo move and no product/architecture decision is required. ## Acceptance evidence (live, real server) I re-ran the map bullet the PR cites against both revisions with the repo's own harness: - `control-agent-server map run --file F24 --only F24.status-offline --fresh --checkout <main @ 6e7a853>`: **fail** on the status GET - `500 {"detail":"Internal Server Error","exception":"All connection attempts failed"}`, matching the linked issue's reported symptom. - The same command on this head (`631afe2`): **pass** - `200 {"vendor":"openai","connect

### #5923: fix(agent-server): forward ?path= on the bare Canvas app-backend root route

- Commit: `1a0fd91ad03d6a9f51ea38aa53fb60b848b56d20` (2026-10-14T16:02:14+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5923).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5923). Families: F29.

Stated intent:

> - `canvas_extensions_bridge_router.py`: the root HTTP and WebSocket routes get their own handlers that take no `path` argument and always proxy to `/`; the sub-path handlers now require `path` (always bound from the URL). `bridge.py` is untouched.
> - New focused test `tests/agent_server/canvas_extensions/test_canvas_extension_bridge_routes.py` checks which upstream path each route form passes to the bridge, for HTTP (GET/POST) and WebSocket.
> - Map: `F29.proxy-root-path-query` loses its three `# bug` markers and "known bug" label; the prose now describes the expected behavior.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`
- `openhands-agent-server/openhands/agent_server/canvas_extensions_bridge_router.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_bridge_routes.py`

Linked issue acceptance context:

- [#5919](https://github.com/OpenHands/software-agent-sdk/issues/5919): [Bug]: The bare root Canvas app-backend proxy route takes the upstream path from a ?path= query parameter — current state closed, closed 2026-10-14T14:02:16Z.

> - [ ] On `/app-backends/{name}` (no trailing slash), HTTP requests for all methods reach the backend's `/`, and a `path` query parameter is forwarded unchanged in the query. - [ ] A root WebSocket on `/app-backends/{name}?path=...` targets the backend's `/`, not the query value. - [ ] Sub-path routes (`/app-backends/{name}/{path}`) and the trailing-slash root are unchanged, including the traversal check. - [ ] A focused unit test covers the route binding for HTTP and WebSocket. - [ ] The map bullet `F29.proxy-root-path-query` passes with its `# bug` markers removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/canvas_extensions_bridge_router.py`: one handler serves both `/app-backends/{extension_name}` and `/app-backends/{extension_name}/{path:path}` (lines 80-95, HTTP; lines 98-104, WebSocket) and declares `path: str = ""`. On the root route there is no `{path}` segment, so FastAPI treats `path` as a query parameter and binds `?path=` to it; the value then becomes the upstream path in `proxy_app_backend_http` / `proxy_app_backend_websocket` (`canvas_extensions/bridge.py:448`, `:475`), where the traversal check (`bridge.py:340-342`) rejects `..`. Impact is limited to links that drop the trailing slash, and the bound value still passes the traversal check. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5923#issuecomment-6159205881)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5923#pullrequestreview-5512470680): _This review was posted by an AI agent (OpenHands)._ ## Scope This change is correctly placed. The bare Canvas app-backend root route (`/app-backends/{name}`) is canonical Agent Server REST/WebSocket behavior owned by this repository (`openhands-agent-server/openhands/agent_server/canvas_extensions_bridge_router.py`), and the linked issue #5919 is `bug`/`ready-for-dev`/`priority:low`. No cross-repository move is warranted. ## What I verified - The diff splits the shared handlers into dedicated root handlers that declare no `path` argument and always pass `""` to the bridge, while the sub-path HTTP/WebSocket handlers now require `path: str`. On a route with a `{path:path}` segment, `path` is always bound from the URL, so making it required cannot turn into a missing-argument error; on the bare root there is no `path` parameter for FastAPI 

### #5920: fix(agent-server): stop bridging to a crashed Canvas backend

- Commit: `1c34a84ccea3aa3f7bdc44ba7a19a88f872fcf08` (2026-10-14T17:45:37+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5920).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5920). Families: F28, F29.

Stated intent:

> - `CanvasExtensionBackendManager.ready_endpoint` returns `None` once the backend process has exited, so the HTTP proxy, WebSocket bridge and bootstrap answer 503 not ready without a status poll.
> - `proxy_app_backend_http` renames a connection failure to `502 Canvas App backend unreachable`.
> - F29 map: `F29.backend-crash-detected` drops its `# bug` marker; its prose and the Gotchas line now describe the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`
- `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`
- `openhands-agent-server/openhands/agent_server/canvas_extensions/bridge.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_bridge.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5912](https://github.com/OpenHands/software-agent-sdk/issues/5912): [Bug]: Canvas bridge keeps dialing a crashed backend still marked 'ready' and answers 502 'Conversation container unreachable' until a status poll — current state closed, closed 2026-10-14T15:45:40Z.

> - [ ] After a ready backend's process exits, the bridge's HTTP proxy, WebSocket bridge and session bootstrap answer `503 Canvas App backend is not ready` without a prior status poll. - [ ] A backend that is alive but refuses the connection answers a 502 whose detail names the Canvas App backend, not a conversation container; the Docker runtime proxy's own message is unchanged. - [ ] The F28 status route still reports the crash as `unhealthy` (`Backend exited with code ...`) and a restart still works. - [ ] Unit tests cover the exited-process case in `CanvasExtensionBackendManager.ready_endpoint` and the bridge error wording. - [ ] The map bullet `F29.backend-crash-detected` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `CanvasExtensionBackendManager.ready_endpoint` (`openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py:814-819`) returns the endpoint whenever `runtime.state == "ready"` and a port is set. It never looks at `runtime.process.returncode`, and `runtime.state` only changes in `_refresh_unlocked` (line 457), reached from the F28 `status`, `start` and `stop` routes. - The bridge (`canvas_extensions/bridge.py:345-361`, `_ready_endpoint`) therefore hands the dead port to `proxy_http` (`docker_runtime/proxy.py:142-152`), whose connection-error branch answers `502 Conversation container unreachable`. Related: https://github.com/OpenHands/software-agent-sdk/issues/5637 (backend outliving a SIGKILLed server) is a different lifecycle bug. F

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5920#issuecomment-6159120042)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5920#pullrequestreview-5512615371): _This review was posted by an AI agent (OpenHands)._ Scope: this is Agent Server Python behavior (Canvas App backend lifecycle and the `/app-backends` bridge), which this repository owns; no cross-repo move is warranted. The linked issue #5912 is open with `bug`, `priority:low`, `ready-for-dev`, and its acceptance criteria are addressed by the current head. Findings: no material correctness, security, compatibility, or acceptance-criterion defect. What I verified on `0ee038dd61abcfc94798c92ad1c5224d8b155ed2`: - Core fix. `CanvasExtensionBackendManager.ready_endpoint` (`canvas_extensions/backend.py:814-824`) now returns `None` when `runtime.process is None` or `runtime.process.returncode is not None`, so all three bridge consumers — the HTTP proxy, the WebSocket bridge, and the session bootstrap, which all route through `_ready_endpoint` (

### #5944: fix(agent-server): report a failed skills sync fetch as an error

- Commit: `2ec5b9ed4a675e01f0d33192be57a4f9834cfd2b` (2026-10-14T17:49:41+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5944).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5944). Families: F15, F25.

Stated intent:

> - Add an opt-in `strict` flag to `try_cached_clone_or_update` (threaded through `_update_repository`) and `update_skills_repository`: when set, a failed fetch, or a failed checkout of the requested ref after it, returns `None` instead of the stale path. The default (`strict=False`) keeps the fall-back-to-cache behaviour for every other caller.
> - `sync_public_skills` uses `strict=True`, so the router reports `status: error`. A pinned tag/SHA already present locally still skips the fetch and reports success.
> - Map bullet `F25.sync-offline`: `# bug` marker removed and the prose rewritten as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`
- `openhands-agent-server/openhands/agent_server/skills_service.py`
- `openhands-sdk/openhands/sdk/git/cached_repo.py`
- `openhands-sdk/openhands/sdk/skills/utils.py`
- `tests/agent_server/test_skills_service.py`
- `tests/sdk/git/test_cached_repo.py`

Linked issue acceptance context:

- [#5933](https://github.com/OpenHands/software-agent-sdk/issues/5933): [Bug]: POST /api/skills/sync reports success when the public skills repository cannot be fetched — current state closed, closed 2026-10-14T15:49:43Z.

> - [ ] `POST /api/skills/sync` reports `status: error` when the existing clone of the public skills repository cannot be fetched (or the requested ref cannot be checked out after the fetch). - [ ] The stale clone is kept and is still used by skill loading (`load_public_skills`, the marketplace and plugin catalog), which keep their current fall-back-to-cache behaviour. - [ ] A successful fetch, and a pinned ref already present locally, still report `status: success`. - [ ] A unit test covers a sync against an existing clone whose origin is unreachable. - [ ] The map bullet `F25.sync-offline` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `sync_public_skills` (`openhands-agent-server/openhands/agent_server/skills_service.py:473-479`) calls `update_skills_repository` and treats any returned path as success. - `update_skills_repository` (`openhands-sdk/openhands/sdk/skills/utils.py:590-609`) delegates to `try_cached_clone_or_update(..., update=True)`, whose `_update_repository` (`openhands-sdk/openhands/sdk/git/cached_repo.py:371`) returns silently after `_try_fetch` (`cached_repo.py:391-398`) logs `Failed to fetch updates: ... Using cached version.` The repository path is then returned as if it were updated. - The router (`openhands-agent-server/openhands/agent_server/skills_router.py:322-326`) maps `success=False` to `status: error`, so only the service result needs to change. The fall-back-to-cache behaviour is right for the other callers of `update_skills_repositor

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5944#issuecomment-6159480441)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5944#pullrequestreview-5512699239): _This review was posted by an AI agent (OpenHands)._ ## Review of PR #5944 — report a failed skills sync fetch as an error **Scope / ownership:** In scope. This changes the Agent Server skills sync endpoint and the SDK git cache helper, which this repository owns. The linked issue #5933 is open and carries `bug`, `priority:low`, and `ready-for-dev`; its acceptance criteria and explicit non-goals (keep the fall-back-to-cache behaviour of the other callers, no HTTP/schema/parameter change, keep the stale clone) match the implementation, so no outstanding product decision is required despite the PR's "Decision needed" note. **Correctness** - `strict` is threaded through `try_cached_clone_or_update` -> `_do_clone_or_update` -> `_update_repository` with a `False` default, so every existing caller (`load_public_skills`, the marketplace catalog 

### #5725: fix(agent-server): re-vendor pip's urllib3 and msgpack to patched versions

- Commit: `9c0b99ec00775d454d19b9567cf1e1bd61e85a75` (2026-10-14T11:07:37-05:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5725).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5725). Families: F01, F12, F16, F31.

Stated intent:

> - Add a `pip-revendored` build stage. It fetches pip 26.2.1's tagged source (checked with `ADD --checksum`), bumps `vendor.txt` to urllib3 2.8.0 and msgpack 1.2.1, and runs pip's own `vendoring sync`. This applies pip's import rewrites and its `tools/vendoring/patches` (including the three urllib3 patches) and regenerates `bom.cdx.json`. The stage then builds a pip wheel. The GitHub source archive is used because the PyPI sdist omits `tools/vendoring/patches`.
> - Reinstall pip from that wheel in `python-node-runtime`. The install step looks up the wheel by the base image's current pip version, so it fails the build if the base image ever ships a different pip. That forces a reassessment instead of a silent downgrade.
> - Add a retirement guard, `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py` (`docker_live`). It reads the `python-runtime` image from the Dockerfile and checks the unmodified upstream pip in it. It passes while upstream pip still vendors an unpatched urllib3 or msgpack. It fails with removal instructions once both reach the fixed versions, or if pip's

Changed paths:

- `.github/workflows/pip-revendoring-retirement.yml`
- `openhands-agent-server/openhands/agent_server/docker/Dockerfile`
- `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py`

Linked issue acceptance context:

- [#5988](https://github.com/OpenHands/software-agent-sdk/issues/5988): [Bug]: Agent-server images ship pip with vulnerable vendored urllib3 2.7.0 and msgpack 1.1.2 — current state closed, closed 2026-10-14T16:07:40Z.

> - [ ] In the `python-node-runtime` stage, `pip._vendor.urllib3.__version__` is at least `2.8.0`, `pip._vendor.msgpack.version` is at least `(1, 2, 1)`, and pip's `_vendor/bom.cdx.json` lists the same versions. - [ ] The fix uses pip's own vendoring tooling (`vendoring sync` with pip's `tools/vendoring/patches`), not files copied straight into `_vendor`, so pip's import rewrites and patches stay in place. Every other bundled package is unchanged from upstream pip. - [ ] pip still works in the built image: `pip install` from PyPI succeeds, a reinstall served from pip's msgpack-backed HTTP cache succeeds, and `pip check` is clean. - [ ] A Trivy scan of the rebuilt image, compared with `main`, shows exactly CVE-2026-97687/97688/97689 and GHSA-6v7p-g79w-8964 removed and no new findings. - [ ] If the base image ships a different pip version, the build fails, so nobody silently installs an older pip. - [ ] A `docker_live` test checks the unmodified upstream pip in the `python-runtime` image. It fails with removal instructions once upstream pip bundles the fixed versions, and it runs in CI separately from the image build. ### Installation Method Docker (published agent-server images) ### SDK Version `main` (agent-server Dockerfile at e3a26821f) --- Proposed fix: #5725 _This issue was created by an AI agent (OpenHands) on behalf of @aivong-openhands._ ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5725#issuecomment-6129956174)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5725#pullrequestreview-5504275927): Could we add a retirement test for this temporary workaround? It should inspect **unmodified upstream pip in the `python-runtime` image**, not the final image where this PR has already patched pip. Here is a draft integration test. It reads the actual upstream image reference from the Dockerfile so a future Python-image update automatically reassesses the workaround. It passes while either vendored dependency still needs patching; once both reach the fixed versions, it fails with removal instructions. Missing vendored packages require reassessment rather than silently passing. Suggested location: `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py`. ```python import json from pathlib import Path import re import subprocess from packaging.version import Version import pytest @pytest.mark.docker_live def test_upstream_pip_
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5725#pullrequestreview-5508667549): _This review was posted by an AI agent (OpenHands)._ ## Scope Dockerfile-only change under `openhands-agent-server/openhands/agent_server/docker/` — the pip re-vendoring workaround for the agent-server images. This belongs in this repository (the agent-server packaging artifact is owned here), so the technical review proceeds. ## What I verified on the current head (`c92a8a47`) - The diff is confined to one file and one concern: a new `pip-revendored` stage plus a reinstall step in `python-node-runtime`. No SDK, API, or persisted-state surface is touched. - The checksum-pinned `ADD`, the `sed` + `grep -qx` assertions on `vendor.txt` (which fail the build if the pinned strings change), the use of pip's own `vendoring sync`, and the `pip-${pip_version}` wheel lookup are internally consistent and reasoned about correctly in the PR descriptio
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5725#pullrequestreview-5521335271): _This review was posted by an AI agent (OpenHands)._ ## Scope Dockerfile + CI + test change under `openhands-agent-server/openhands/agent_server/docker/`. The agent-server packaging artifact and its Docker images are owned by this repository, so this change belongs here. No SDK, REST, or persisted-state surface is touched, and no product/architecture decision is outstanding (issue #5988 carries `ready-for-dev`, `bug`, `priority:low`). ## What I verified on the current head (`46d146ba`) I fetched the exact head and checked out `46d146ba1bfda1c57aad1fbe0bbf8395bef8e0d3` in the workspace. - **The sed/grep rewrite matches real pip source.** I pulled `src/pip/_vendor/vendor.txt` from the pip `26.2.1` tag: `msgpack==1.1.2` at column 0 (line 4) and ` urllib3==2.7.0` indented (line 11). The Dockerfile's two `sed -E` expressions reproduce exactly 
- [discussion; comment; aivong-openhands](https://github.com/OpenHands/software-agent-sdk/pull/5725#issuecomment-6170510678): @neubig I added the retirement test in b6c4e8a3f: - `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py` is your draft with small cleanups: it sits at module level with `pytestmark = pytest.mark.docker_live`, finds the Dockerfile with a fixed `parents[3]` path instead of searching upward, and keeps the probe and thresholds in constants. The behavior is the same. It reads the `python-runtime` image from the Dockerfile, uses `--pull=always --network=none`, fails if the vendoring layout changes, and fails with removal instructions once urllib3 and msgpack both reach the fixed versions. - The new `.github/workflows/pip-revendoring-retirement.yml` runs it apart from the image build, as you suggested, so the exact-pip-version wheel lookup can't fail first and hide the message. It runs on PRs that touch the Dockerfile, the test

### #5986: fix(git): keep Azure DevOps clone URLs without a .git suffix

- Commit: `95cb37b7f04ff281e92bfb3c3fc66573db2df38a` (2026-10-14T13:43:04-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5986).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5986). Families: F14, F15.

Stated intent:

> - `normalize_git_url` leaves Azure DevOps clone URLs (any path containing `/_git/`: dev.azure.com, `*.visualstudio.com`, Azure DevOps Server) without a `.git` suffix. It still strips a trailing slash. Other hosts are unchanged.

Changed paths:

- `openhands-sdk/openhands/sdk/git/utils.py`
- `tests/sdk/extensions/test_fetch.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5986#issuecomment-6169077335)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5986#pullrequestreview-5521290152): _This review was posted by an AI agent (OpenHands)._ Reviewed exactly `f79cf5050ee916d6aaeb4440a0ad67c34440586c`; the PR head matches the requested commit. ### Scope The change belongs in this repository. Extension, marketplace, and plugin fetching plus git URL normalization are owned by the SDK, and the fix is confined to `normalize_git_url` with regression tests. No product or architecture decision is needed and no repository move applies. ### Correctness The `"/_git/" in urlparse(url).path` guard keys off Azure DevOps' clone-route marker rather than a host allowlist, so it also covers `*.visualstudio.com` and Azure DevOps Server. Using `urlparse(...).path` (not the raw string) means a `_git` in a query or fragment cannot trigger it. The trailing-slash strip still runs before the early return, and URLs already ending in `.git` skip the 
- [reviews; COMMENTED; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/5986#pullrequestreview-5521678995): Hello Alona, **Overall: Non-blocking.** No blockers from my side. Thank you, this is a small and well-targeted fix. Checking for `/_git/` in the parsed path covers dev.azure.com, `*.visualstudio.com` and Azure DevOps Server without needing a host allowlist. A non-Azure URL that happens to contain a `_git` segment only loses the `.git` suffix, which most git hosts accept anyway. `normalize_git_url` is also unchanged on current `main`, so the fix should apply cleanly despite the v1.50.1 base. I left one non-blocking nit about the docstring. Thank you very much :pray:
- [reviews; APPROVED; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/5986#pullrequestreview-5521699773): Thank you! 🙏
- [review_comments; comment; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/5986#discussion_r4273863970): Hello Alona, **Non-blocking (nit):** Thank you for adding the example. The summary line ("ensuring .git suffix for HTTPS URLs") and the `Returns:` line ("with .git suffix for HTTPS/HTTP URLs") still describe the previous rule. Would you mind mentioning the Azure DevOps `/_git/` exception there as well, so readers don't need to reach the examples to find it? Thank you very much :pray:
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/5986#issuecomment-6169094498): @openhands /codereview-roasted Review exactly commit f79cf5050ee916d6aaeb4440a0ad67c34440586c. If the PR head changes, report that and do not approve another commit. Review only: do not push code, merge, deploy, or change attribution. Check correctness, regression coverage, and whether the PR validation claims match the evidence. Keep code comments short. Post the substantive review and link it in your completion message. End the substantive review with these plain lines (no code fence), choosing one verdict: REVIEWED_HEAD: f79cf5050ee916d6aaeb4440a0ad67c34440586c REVIEW_REQUEST: 237df9de2fa64e459510b4ca217c682c VERDICT: APPROVED Use VERDICT: CHANGES_REQUESTED instead when findings require changes. If unable to review, explain the blocker without an approval verdict.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/5986#issuecomment-6169922113): ## Review — `fix(git): keep Azure DevOps clone URLs without a .git suffix` Reviewed exactly commit `f79cf5050ee916d6aaeb4440a0ad67c34440586c` on branch `alona/azure-devops-marketplace-git-suffix`. PR head is unchanged from the requested review commit. Diff vs parent (`1e1390a`) is exactly two files: `openhands-sdk/openhands/sdk/git/utils.py` (+6) and `tests/sdk/extensions/test_fetch.py` (+24). _(Review generated by an AI agent via `/codereview-roasted` on behalf of the requesting user.)_ ### Correctness `normalize_git_url` now returns HTTPS/HTTP URLs whose parsed path contains `/_git/` (the clone-URL marker for `dev.azure.com`, `*.visualstudio.com`, and Azure DevOps Server) without appending `.git`, while still stripping a trailing slash. I traced the data flow end to end: `normalize_git_url` → `parse_extension_source` → `fetch_with_resol
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/5986#issuecomment-6170990043): @openhands /codereview-roasted Review exactly commit f79cf5050ee916d6aaeb4440a0ad67c34440586c. If the PR head changes, report that and do not approve another commit. Review only: do not push code, merge, deploy, or change attribution. Check correctness, regression coverage, and whether the PR validation claims match the evidence. Keep code comments short. Post the substantive review and link it in your completion message. End the substantive review with these plain lines (no code fence), choosing one verdict: REVIEWED_HEAD: f79cf5050ee916d6aaeb4440a0ad67c34440586c REVIEW_REQUEST: 0002b53946cc45c18e6b3823f333ab26 VERDICT: APPROVED Use VERDICT: CHANGES_REQUESTED instead when findings require changes. If unable to review, explain the blocker without an approval verdict.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/5986#issuecomment-6172505956): Good. Reviewed exactly `f79cf5050ee916d6aaeb4440a0ad67c34440586c`; the PR head still matches the requested commit. The diff is limited to `normalize_git_url` and focused extension-source regression tests. No material findings. ### Correctness The `urlparse(url).path` check handles Azure DevOps' `/_git/` clone-route marker without relying on a cloud-host allowlist, so it also covers `*.visualstudio.com` and Azure DevOps Server. Trailing slashes are still removed before returning. I traced the affected path through `parse_extension_source` into extension fetching and found no later normalization step that can append `.git` again. Existing non-`/_git/` HTTP(S) behavior is unchanged. ### Validation Independently reproduced in this reviewer checkout: - `tests/sdk/extensions/test_fetch.py`: **52 passed**. - With only the new `/_git/` early retu
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5986#issuecomment-6172588635): Reviewed exact commit `f79cf5050ee916d6aaeb4440a0ad67c34440586c` and posted the substantive review: https://github.com/OpenHands/software-agent-sdk/pull/5986#issuecomment-6172505956 Verdict: **APPROVED**. No material findings. GitHub rejected a formal approval event because the available token belongs to the PR author (`Review Can not approve your own pull request`), so the verdict was posted as a PR discussion comment with the required head and request token.

### #5929: fix(agent-server): tie uvicorn proxy_headers to trust_forwarded_headers

- Commit: `4dc42d3a2b65c4f674ad0fc149477a583a9c4419` (2026-10-14T20:28:07+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5929).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5929). Families: F02, F27.

Stated intent:

> - `__main__.py`: resolve the agent-server config once through a small `_load_server_config()` helper (shared with `_auth_enabled`) and pass `proxy_headers=_trust_forwarded_headers()` to uvicorn's `Config`. No change to the bridge proxy code.
> - Tests: `test_preload_modules.py` checks the `proxy_headers` value `main()` passes for each flag setting; `canvas_extensions/test_canvas_extension_bridge.py` runs the bridge on a live uvicorn server with the launcher's `proxy_headers` value and sends `X-Forwarded-Proto: https` against a plain-HTTP ingress (bootstrap and proxied GET answer 200) and against an HTTPS ingress reached over plain HTTP (421), as the issue's triage asks.
> - F29 map: drop the `known bug` marker from `F29.session-forwarded-proto`, rewrite its prose and the uvicorn Gotchas line as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`
- `openhands-agent-server/openhands/agent_server/__main__.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_bridge.py`
- `tests/agent_server/test_preload_modules.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5928](https://github.com/OpenHands/software-agent-sdk/issues/5928): [Bug]: X-Forwarded-Proto changes the Canvas bridge's request origin while trust_forwarded_headers is off (421 instead of 200) — current state closed, closed 2026-10-14T18:28:10Z.

> - [ ] With `trust_forwarded_headers` off, `X-Forwarded-Proto: https` from a loopback client does not change the bridge's request origin: the bootstrap and proxied GET on a plain-HTTP ingress answer `200`. - [ ] With `trust_forwarded_headers` on, forwarded headers keep deciding the origin (`F29.trusted-forwarded-headers` still passes). - [ ] A unit test covers the fix. - [ ] The map bullet `F29.session-forwarded-proto` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: the server starts uvicorn (`openhands-agent-server/openhands/agent_server/__main__.py:313-326`) without setting `proxy_headers`, so uvicorn 0.54 keeps its default `proxy_headers=True` with `FORWARDED_ALLOW_IPS` defaulting to `127.0.0.1`. Its `ProxyHeadersMiddleware` rewrites `scope["scheme"]` (and `scope["client"]`) from `X-Forwarded-Proto`/`X-Forwarded-For` for loopback peers before the app runs, so `_request_origin` (`openhands-agent-server/openhands/agent_server/canvas_extensions/bridge.py:274`, untrusted branch at line 290: `scheme = request.url.scheme`) already reads the client-supplied scheme, and `_require_ingress_host` (line 299) answers 421. The bridge cannot recover the raw scheme itself, because uvicorn mutates the scope in place. `trust_forwarded_headers` is documented in `openhands-agent-server/openhands/agent_server/config.py:374`. Launching with `FORWARDED_ALLOW_IPS=192.0.2.1` (no loopback peer trusted) makes the bootstrap succeed, which pins the cause on the uvicorn configuration. Possible 

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5929#issuecomment-6159246348)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5929#pullrequestreview-5512505178): _This review was posted by an AI agent (OpenHands)._ ## Summary Ties uvicorn's `proxy_headers` to `Config.trust_forwarded_headers` in the `python -m openhands.agent_server` launcher (`openhands-agent-server/openhands/agent_server/__main__.py`). With the setting off (the default), uvicorn's `ProxyHeadersMiddleware` no longer rewrites `scope["scheme"]`/`scope["client"]` from `X-Forwarded-Proto`/`X-Forwarded-For` for `FORWARDED_ALLOW_IPS` peers (loopback by default), so the Canvas bridge's `_request_origin` reads the request's own scheme. With the setting on, behaviour is unchanged. This matches what `config.py` (line 374) documents and what `bridge.py` `_request_origin` already implements in its untrusted branch. ## Verification - Scope: the change lives in the Agent Server entry point this repository owns; the canvas bridge proxy code is u

### #5909: fix(sdk): accept the documented github:owner/repo/<path> install source

- Commit: `98c97e6f18cecdaec96c011efb00f43db5b63bcc` (2026-10-14T20:28:41+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5909).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5909). Families: F25, F26, F28.

Stated intent:

> - `openhands.sdk.extensions.fetch`: the GitHub shorthand takes an optional path after `owner/repo`, and `fetch_with_resolution` uses that path as `repo_path`. The existing containment check still applies, so `github:o/r/../..` is rejected.
> - Install source descriptions: the skill description drops the tree URL example and shows a git URL with `repo_path` instead. Both descriptions now say the shorthand can name a path.
> - verify-agent-server map: the `# bug` markers are removed from `F25.install-documented-sources` and `F26.install-documented-source`. `F26.install-errors` now uses `github:qa` for its offline invalid-shorthand case, since `github:qa/f26/extra` would now attempt a clone.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`
- `.agents/skills/verify-agent-server/references/feature-map/F26-plugins.md`
- `openhands-agent-server/openhands/agent_server/plugins_router.py`
- `openhands-agent-server/openhands/agent_server/skills_router.py`
- `openhands-agent-server/openhands/agent_server/skills_service.py`
- `openhands-sdk/openhands/sdk/extensions/fetch.py`
- `tests/sdk/extensions/test_fetch.py`

Linked issue acceptance context:

- [#5895](https://github.com/OpenHands/software-agent-sdk/issues/5895): [Bug]: Skill and plugin install reject the source forms their OpenAPI descriptions document (400) — current state closed, closed 2026-10-14T18:28:43Z.

> - [ ] Every source form documented in `InstallSkillRequest.source` and `InstallPluginRequest.source` installs (or is no longer documented). - [ ] An invalid GitHub shorthand (e.g. `github:owner` without a repo) is still a 400 without a network call. - [ ] Unit tests in `tests/sdk/extensions/test_fetch.py` cover the accepted forms. - [ ] The map bullets `F25.install-documented-sources` and `F26.install-documented-source` pass with their `# bug` markers removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `parse_extension_source` (`openhands-sdk/openhands/sdk/extensions/fetch.py:60-69`) accepts exactly one `/` after `github:` and raises `ExtensionFetchError` otherwise; the install routes map that to 400. - A `https://github.com/<owner>/<repo>/tree/<ref>/<path>` URL is treated as a plain git URL and `normalize_git_url` appends `.git` (`openhands-sdk/openhands/sdk/git/utils.py:584-587`). - The descriptions: `openhands-agent-server/openhands/agent_server/skills_router.py:150-158`, `openhands-agent-server/openhands/agent_server/plugins_router.py:97-103`, and the docstring of `service_install_skill` (`skills_service.py:502-506`). - `_fetch_catalog_entries` in `skills_service.py:749-756` also builds `github:owner/repo[@ref]/<path>` sources for marketplace entries, with a comment saying install can parse them; `tests/agent_server/test_skills_router.py` uses `github:owner/repo/skills/test-skill` sources too. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5909#issuecomment-6158916549)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5909#pullrequestreview-5512487380): _This review was posted by an AI agent (OpenHands)._ ## Scope This change belongs in this repository. The defect is in `openhands.sdk.extensions.fetch` (the SDK fetch path that backs skill, plugin, and Canvas-extension installs), which this repo owns, and the OpenAPI descriptions for `POST /api/skills/install` and `POST /api/plugins/install` are generated from the same source. Linked issue #5895 is open and carries `ready-for-dev`, so no product/architecture decision is outstanding. ## Current state re-verified - Head is `a696ae47aaf0c91c58be0648aaa9b073caa01a7f`, matching the requested SHA; PR is open, non-draft, no labels. - No prior review from this account and no review comments on this head; the only PR comment is the bot's REST-API breakage report, and there are no unresolved review threads. - All check runs for this head are `succe

### #5945: fix(agent-server): remove the per-server TMUX_TMPDIR on shutdown

- Commit: `d60b176ce32b45daec8127f134aa715f911d9637` (2026-10-14T20:30:40+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5945).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5945). Families: F01, F03, F05, F30, F31, F32.

Stated intent:

> - At shutdown, `api_lifespan` removes the per-server tmux directory, but only when this process created it. Removal is best-effort: a failure is logged as a warning and shutdown continues. A user-provided `TMUX_TMPDIR`, or a default directory that already existed, is never removed. Env restoration is unchanged.
> - Tests: the lifespan test asserts the directory is gone after exit. New tests cover these cases:
>   - a user-provided `TMUX_TMPDIR` is kept;
>   - a pre-existing default directory is kept;
>   - with a too-long `TMUX_TMPDIR`, the user's value is restored and kept and the substitute is removed;
>   - a failed removal is logged and does not abort shutdown.
> - Map: drops the `# bug` marker and the "known bug" label from `F31.tmux-dir-cleanup` and rewrites its prose as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`
- `openhands-agent-server/openhands/agent_server/api.py`
- `tests/agent_server/test_api.py`

Linked issue acceptance context:

- [#5932](https://github.com/OpenHands/software-agent-sdk/issues/5932): [Bug]: Agent server leaves its per-process TMUX_TMPDIR (openhands-agent-server-PID) behind after a graceful shutdown — current state closed, closed 2026-10-14T18:30:43Z.

> - [ ] When the server defaulted its own `TMUX_TMPDIR`, a graceful shutdown removes that directory (and restores the environment variable as today). - [ ] A user-provided `TMUX_TMPDIR` that the server kept is never deleted. - [ ] A unit test in `tests/agent_server/test_api.py` asserts the directory is removed after `api_lifespan` exits, and kept for a user-provided value. - [ ] The map bullet `F31.tmux-dir-cleanup` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/api.py`: - `_ensure_server_tmux_tmpdir` (line 143) creates the default directory with `mkdir(parents=True, exist_ok=True)` (line 149) and sets `TMUX_TMPDIR`. - The final block of `api_lifespan` (lines 351-357) only pops or restores `TMUX_TMPDIR` in `os.environ`; nothing deletes the directory. - `test_lifespan_defaults_and_restores_tmux_tmpdir` checks only the environment variable. Related: the per-server directory was introduced in https://github.com/OpenHands/software-agent-sdk/pull/2970; the too-long fallback in https://github.com/OpenHands/software-agent-sdk/pull/5559. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5945#issuecomment-6159493648)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5945#pullrequestreview-5520265372): _This review was posted by an AI agent (OpenHands)._ **Scope:** This change belongs in this repository. It touches agent-server lifecycle code (`_ensure_server_tmux_tmpdir` / `api_lifespan` in `openhands-agent-server/openhands/agent_server/api.py`), its unit tests, and the `verify-agent-server` feature map. It should not move repositories or be closed. **Why this stops for a decision:** The PR body asks for it directly ("Decision needed ... A maintainer should confirm which gate they prefer"), and the two candidate gates are not behaviorally equivalent. Gating removal on "this process created the directory" leaves a pre-existing, same-named default directory behind, whereas the issue-triage suggestion (the env-restore guard) would remove it. That difference is real in the shared-`/tmp`-across-PID-namespaces case the linked issue (#5932) c
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5945#pullrequestreview-5520674668): _This review was posted by an AI agent (OpenHands) on behalf of the requesting user._ **Verdict: APPROVE.** **Scope.** Agent Server lifecycle/cleanup — belongs in this repo. `shutil` is stdlib, so no packaging change; `_ensure_server_tmux_tmpdir` is private with `api_lifespan` as its only caller, and no public API, default, or wire contract changes. **Decision gate resolved.** The author's "Decision needed" (gate removal on a new `created` flag vs the env-restore guard) was answered by the maintainer (enyst, 2026-10-14 14:30): *"Let's go with created though."* The head implements that choice, so the open question is closed. **Verified at `4cecf3a4`** - `uv run pytest -q tests/agent_server/test_api.py` -> **51 passed** (reproduced locally). - CI: all 53 check-runs on the head are success (`agent-server-tests`, `smoke-test`, `cross-tests`, 

### #5918: fix(agent-server): keep the client's body framing in proxied requests

- Commit: `1045bdffdb8dfc30f575645c7d2dfccf61f8e644` (2026-10-14T20:45:37+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5918).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5918). Families: F12, F29.

Stated intent:

> - `proxy_http` (`docker_runtime/proxy.py`) now sends no body when the client sent neither `Content-Length` nor `Transfer-Encoding`, re-adds the client's `Content-Length` while still streaming the body (httpx then does not chunk), and keeps a chunked client body chunked. A `body=` replacement is passed as bytes, so httpx sets its `Content-Length`.
> - New parametrized unit test in `tests/agent_server/docker_runtime/test_proxy.py` checks the upstream framing and body for the GET, fixed-length POST and chunked POST cases.
> - The map bullet `F29.proxy-request-framing` drops its `# bug` markers and "known bug" label, its prose and the matching Gotchas line now describe the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`
- `openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py`
- `tests/agent_server/docker_runtime/test_proxy.py`

Linked issue acceptance context:

- [#5913](https://github.com/OpenHands/software-agent-sdk/issues/5913): [Bug]: Proxied Canvas app-backend requests always reach the backend chunked, without Content-Length (GET included) — current state closed, closed 2026-10-14T18:45:39Z.

> - [ ] A proxied request without a body (no `Content-Length`, no `Transfer-Encoding`) reaches the upstream with no body and no `Transfer-Encoding`. - [ ] A proxied request with `Content-Length: N` reaches the upstream with `Content-Length: N` and no `Transfer-Encoding`, still streamed (not buffered). - [ ] A chunked client request is still forwarded, and the `body=` replacement used by the Docker runtime proxy still works. - [ ] `tests/agent_server/docker_runtime/test_proxy.py` covers the upstream framing for these cases. - [ ] The map bullet `F29.proxy-request-framing` passes with its `# bug` markers removed (and the matching Gotchas line in `F29-app-backend-bridge.md` is updated). ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py`: - `_HOP_BY_HOP_HEADERS` (lines 46-49) includes `content-length` (it is not hop-by-hop in RFC 7230), so the client's `Content-Length` is dropped by `_filter_headers`. - `proxy_http` always passes the async generator `_request_body()` (lines 125-131) as `content=` to `client.stream(...)` (line 142). For an async iterable without a `Content-Length` header, httpx frames the body as `Transfer-Encoding: chunked`, for every method. - The Canvas bridge calls it from `proxy_app_backend_http` (`openhands-agent-server/openhands/agent_server/canvas_extensions/bridge.py:467`); the Docker runtime proxy (`docker_runtime/routers.py:102`) shares it. Suggested fix: keep streaming, but send no content when the client sent nei

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5918#issuecomment-6159091165)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5918#pullrequestreview-5512667954): _This review was posted by an AI agent (OpenHands)._ Reviewed head `53a93bcc` against `main` (base `6e7a853`). ## Scope The change belongs in this repository: `proxy_http` in `openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py` is agent-server code shared by the Canvas app-backend bridge (`canvas_extensions/bridge.py:467`) and the Docker conversation proxy (`docker_runtime/routers.py:102`). No product/architecture decision required. ## What I verified (socket level, raw TCP capture of the upstream request) - Base `6e7a853`: `GET` reaches upstream with `transfer-encoding: chunked` and an empty body; `POST` with `Content-Length: 13` arrives chunked with no `Content-Length`. Both match the reported bug (#5913). - Head `53a93bcc`: `GET` reaches upstream with no body and no framing header; `POST` with `Content-Length: 13` ar

### #5907: fix(agent-server): don't advertise a VS Code URL while VS Code is not running

- Commit: `4e32a3adb05250477e936481acc77b403c48cba7` (2026-10-14T20:48:49+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5907).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5907). Families: F16.

Stated intent:

> - `VSCodeService.get_vscode_url` returns `None` unless the server is running. `get_vscode_service()` seeds the token from `session_api_keys[0]` before `start()` runs, so the token alone never meant a server was there (and `start()` also mints a token before its port check).
> - Unit tests: the not-running case with a token, and a failed `start()` (port taken) leaving no URL; the existing URL-shape tests now mark the service as running.
> - Map (F16): `F16.vscode-url-not-running` drops its `# bug` markers and gains the missing `Requires: F16.vscode-enabled-status` (without it `--only` hit 503 "VS Code disabled"). `F16.vscode-url-shape` and `F16.vscode-folder-encoding` are labelled `blocked` (see Decision needed), and the shape recipe starts VS Code on a free port and puts the held port back. Prose and Gotchas updated.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F16-workspace-serving-and-vscode.md`
- `openhands-agent-server/openhands/agent_server/vscode_service.py`
- `tests/agent_server/test_vscode_service.py`

Linked issue acceptance context:

- [#5900](https://github.com/OpenHands/software-agent-sdk/issues/5900): [Bug]: /api/vscode/url returns a URL carrying the session key while VS Code is not running — current state closed, closed 2026-10-14T18:48:52Z.

> - [ ] `VSCodeService.get_vscode_url` returns `None` when the server is not running, even when a connection token is set. - [ ] `GET /api/vscode/url` and `GET /api/conversations/{conversation_id}/vscode/url` answer `{"url": null}` on a keyed server whose VS Code did not start, matching the keyless and `POST /api/init` cases. - [ ] A running server still returns the same URL as today (shape, `base_url`, `workspace_dir`, `vscode_base_path`). - [ ] A unit test in `tests/agent_server/test_vscode_service.py` covers the not-running case with a token set. - [ ] The map bullet `F16.vscode-url-not-running` passes with its `# bug` markers removed, and gains `Requires: F16.vscode-enabled-status`. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: `get_vscode_service()` seeds the connection token from `session_api_keys[0]` when it builds the service (`openhands-agent-server/openhands/agent_server/vscode_service.py:287-289`), before `start()` runs and fails, and `get_vscode_url` returns `None` only when the token is `None` (`vscode_service.py:125-126`); it never checks `is_running()`. A deferred-init server gets the key through `set_connection_token`, which skips a server that is not running, hence the different answers. On a machine with the OpenVSCode binary, `start()` also mints a random token before its port check, so a keyless server whose port is taken advertises a URL for a server that is not running; the same fix covers that case. Related: https://github.com/OpenHands/software-agent-sdk/issues/5845 (folder encoding

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5907#issuecomment-6158892290)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5907#pullrequestreview-5512564786): _This review was posted by an AI agent (OpenHands)._ ## Scope In-repo: `openhands-agent-server` owns the agent-server REST API, and `VSCodeService` / `vscode_router` live here. No cross-repo move needed. ## Assessment The root cause matches issue #5900: `get_vscode_service()` seeds `connection_token` from `session_api_keys[0]` before `start()` runs, so `get_vscode_url`'s token-only guard advertised a URL (carrying the session key) for a VS Code that never started. The change makes `get_vscode_url` return `None` unless `self.is_running()` — a minimal, correct gate. Verified against the current head: - Baseline `origin/main` (6e7a853) reproduces the bug: `VSCodeService(port=8001, connection_token='session-key')` → `is_running()=False`, `get_vscode_url()='http://localhost:8001/?tkn=session-key&folder=workspace'`. - Head returns `None` for th

### #5787: fix(agent-server): treat IPv6 loopback Host as a secure context

- Commit: `f67cba804501fb95af486ccb9f8157b81a283dae` (2026-10-14T20:56:25+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5787).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5787). Families: F02.

Stated intent:

> - `_request_is_secure_context` strips a `:port` suffix only from a bracketed host or a host with exactly one colon, so a bare IPv6 hostname is kept whole.
> - Tests: `Host: [::1]` and `Host: [::1]:8000` mints get `Secure; Partitioned`; a bracketed `X-Forwarded-Host: [::1]:8000` keeps working.
> - Map: `F02.workspace-session-ipv6-loopback` loses its `# bug` marker and "known bug" label; its prose now states the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F02-auth-and-sessions.md`
- `openhands-agent-server/openhands/agent_server/auth_router.py`
- `tests/agent_server/test_workspace_cookie_auth.py`

Linked issue acceptance context:

- [#5785](https://github.com/OpenHands/software-agent-sdk/issues/5785): [Bug]: workspace-session cookie minted via Host [::1]:PORT is not Secure (IPv6 loopback not treated as a secure context) — current state closed, closed 2026-10-14T18:56:27Z.

> - [ ] A plain-HTTP mint with `Host: [::1]` or `Host: [::1]:PORT` gets `Secure; Partitioned`. - [ ] `localhost`, `127.0.0.1` (with or without port) and bracketed `X-Forwarded-Host: [::1]:PORT` still get `Secure`; a non-loopback plain-HTTP host still does not. - [ ] `tests/agent_server/test_workspace_cookie_auth.py` covers the IPv6 loopback `Host` header. - [ ] The map bullet `F02.workspace-session-ipv6-loopback` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6a298a4 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/auth_router.py`, `_request_is_secure_context` (line 42): - Without `X-Forwarded-Host`, the host comes from `request.url.hostname` (line 58), which Starlette returns **unbracketed** (`::1`). - The bracket handling (line 60) only matches the bracketed `X-Forwarded-Host` form; otherwise the code strips a port with `host.split(":")[0]` (line 63), which turns `::1` into `''`, so the loopback check fails. Suggested fix: only strip a `:port` suffix when the host has exactly one colon (an unbracketed host with several colons is a bare IPv6 address). Related: https://github.com/OpenHands/software-agent-sdk/issues/4562 (private-LAN HTTP cookies; a different host class, not this bug). Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5787#issuecomment-6157103305)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5787#pullrequestreview-5511285919): _This review was posted by an AI agent (OpenHands)._ ## Summary This PR fixes a real bug: `_request_is_secure_context` split `request.url.hostname` on `:`, but `request.url.hostname` returns an IPv6 address unbracketed (`::1`), so a `Host: [::1]:PORT` mint was reduced to an empty string and failed the loopback check. The result was a `SameSite=None` cookie without `Secure`, which browsers reject, breaking cookie-authenticated workspace embeds on the IPv6 loopback. ## Assessment I verified the change against the workspace and the current head: - **Correctness.** The new `elif host.count(":") == 1` guard strips a `:port` suffix only for a single-colon host. A bare IPv6 hostname (`::1`) now keeps all colons and matches `_LOOPBACK_HOSTS`, while a genuine `host:port` still resolves to its host. I replayed base vs head logic: `[::1]`/`[::1]:800

### #5774: fix(agent-server): reap Canvas backends orphaned by a crashed server

- Commit: `91b89bdc6f33d185a1da85e9da54c521e53fcdec` (2026-10-14T20:59:17+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5774).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5774). Families: F01, F03, F05, F28, F30, F31, F32.

Stated intent:

> - `CanvasExtensionBackendManager` now writes a small record per started backend under `backends/run/<pgid>.json`: the process group, the leader's start time (`/proc/<pid>/stat`), and the server's own pid and start time. `_terminate_runtime` removes the record once the group is stopped.
> - New `reap_orphans()`, called from the API lifespan before routes are served. For each record whose server is no longer running, it stops the group with the existing SIGTERM-then-SIGKILL logic. It signals only when the recorded leader still matches (same start time, command line under `artifacts/<name>/`), so recycled pids and unrelated processes are left alone. Then it drops the record. A record whose server is still alive is skipped, so a second server on the same state dir does not kill another server's backend.
> - REST contract, TypeScript client and Docker behaviour are unchanged. The map bullet `F28.hard-restart-orphan` drops its `known bug` marker and now describes the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`
- `openhands-agent-server/openhands/agent_server/api.py`
- `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`

Linked issue acceptance context:

- [#5637](https://github.com/OpenHands/software-agent-sdk/issues/5637): [Bug]: Canvas backend outlives a SIGKILLed agent-server; restart reports it stopped and starts a second one — current state closed, closed 2026-10-14T18:59:19Z.

> - [ ] After a SIGKILL and restart on the same `OH_PERSISTENCE_DIR`, no backend of the dead server is alive once routes are served. - [ ] `start` never leaves two processes for one app, and `DELETE .../backend/data` never succeeds while its backend is alive. - [ ] A test with a real subprocess covers the crash-and-restart path. - [ ] The map bullet `F28.hard-restart-orphan` passes with its known-bug marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context In `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`: - Lines 642-649 spawn the backend with `start_new_session=True`. Nothing ties it to the server's lifetime. - The in-memory `self._runtimes` (127) is the only record of a running backend. `_status_unlocked` (409-442) reports `stopped`/`pid: null` when it has no entry. - `shutdown()` (786-790) runs only from the lifespan's `finally` (`api.py:344-346`), and a SIGKILL skips it. - `start` (589) and `delete_data` (768-777) check only the in-memory runtime. Suggested fix: on start, record the pgid and the leader's start time (`/proc/<pid>/stat`) in `backends/run/<name>.json`, and delete the record on stop. At startup, before serving, kill each leftover group that still matches its record (same start time, cmdline under `artifacts/<name>/`) with the existing SIGTERM-then-SIGKILL logic. `PR_SET_PDEATHSIG` alone is not enough, because it fires when the forking thread exits. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5774#issuecomment-6156527226)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5774#pullrequestreview-5511721198): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope for this repository: the Agent Server owns the Canvas App backend lifecycle in `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`. The change matches the linked issue's bounded scope (host-process crash/restart; no REST contract, TypeScript, or Docker changes). ## What I verified - **Head/state re-read:** head is still `c0b007e8dd8fbd6d3110d2af1ef1576826bb7074`, all GitHub Actions checks green on that exact head, no prior reviews/comments. Linked issue #5637 is open and carries `bug`, `priority:low`, `ready-for-dev`. - **Behavior (independent reproduction, real subprocesses):** started a real `CanvasExtensionBackendManager` backend (leader + forked `sleep` child) with `start_new_session=True`, `kill -9`'d the server, then ran `reap_orphans()` 

### #5891: fix(sdk): keep edge whitespace on the original side of a git diff

- Commit: `dc897208faae41296f9bee3ac6cc147a25a5f11a` (2026-10-14T22:01:26+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5891).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5891). Families: F14, F15.

Stated intent:

> - `run_git_command` gains a keyword-only `strip: bool = True`. All existing callers keep stripped output.
> - `get_git_diff` reads the `git show` blob with `strip=False` and normalizes it the way it already normalizes the modified side, so an unchanged file has identical sides. CRLF files behave as before.
> - Map: the `F15.diff-whitespace` bullet drops its `# bug` marker and "known bug" label, and the prose and Gotchas now describe the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`
- `openhands-sdk/openhands/sdk/git/git_diff.py`
- `openhands-sdk/openhands/sdk/git/utils.py`
- `tests/sdk/git/test_git_diff.py`

Linked issue acceptance context:

- [#5888](https://github.com/OpenHands/software-agent-sdk/issues/5888): [Bug]: git diff shows phantom changes for unchanged files that start with indentation or end with blank lines — current state closed, closed 2026-10-14T20:01:29Z.

> - [ ] The working-tree diff of an unchanged file returns identical `original` and `modified`, including files with leading indentation, leading blank lines, trailing blank lines and CRLF line endings. - [ ] Both sides keep the current normalization (`"\n".join(text.splitlines())`), so CRLF files and the existing diff tests are unchanged. - [ ] `run_git_command`'s output stays stripped for its other callers (refs, SHAs, name-status lists). - [ ] `tests/sdk/git/test_git_diff.py` covers an unchanged file with each of these shapes. - [ ] The map bullet `F15.diff-whitespace` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-sdk/openhands/sdk/git/git_diff.py`: - `original` comes from `run_git_command(["git", "show", f"{current_rev}:{path}"], ...)` (lines 111-113), and `run_git_command` returns `result.stdout.strip()` (`openhands-sdk/openhands/sdk/git/utils.py:115`), which drops the leading indentation of the first line and every trailing blank line. - `modified` is `"\n".join(f.read().splitlines())` (line 121), which keeps both and drops only the final line terminator. The commit-mode diff (`get_commit_file_diff`, `openhands-sdk/openhands/sdk/git/git_commits.py:173`) strips both sides alike, so it shows no phantom change, although it also loses the first line's indentation and trailing blank lines. Related: #5857 (working-tree diff of a deleted file) changes the same function but leaves how both sides are read untouched. Found by the first full agentic run of v

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5891#issuecomment-6158624498)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5891#pullrequestreview-5518414873): _This review was posted by an AI agent (OpenHands)._ **Scope.** In-repo: the change fixes `get_git_diff` in `openhands-sdk/openhands/sdk/git/git_diff.py`, which is owned here, and the linked issue #5888 is `bug` / `priority:low` / `ready-for-dev`. No product or architecture decision is needed. **What I verified on head `0ac9d3c`.** - Root cause is real and matches the issue: the base side came from `run_git_command`, which `.strip()`s git's stdout, while the disk side is `"\n".join(text.splitlines())`. I reproduced it through the real `get_git_diff` entry point with an actual git repo. On `origin/main`, `lead.txt` gives `original="indented line\nbody"` vs `modified=" indented line\nbody"`, and `trail.txt` gives `"body"` vs `"body\n\n"`. On this head both files return identical sides (`" indented line\nbody"` and `"body\n\n"`), matching th

### #5903: fix(agent-server): clear the skills marketplace cache on sync

- Commit: `923ff2de5fd670941da3b8c0c6b8d6d0d4028e8e` (2026-10-14T22:14:36+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5903).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5903). Families: F25.

Stated intent:

> - `sync_public_skills` clears the marketplace catalog cache on success (new `_invalidate_catalog_cache` helper); a failed sync keeps it.
> - `service_get_marketplace_catalog` caches only non-empty results, the same rule `plugins_service` already uses.
> - Unit tests for both; the `F25.marketplace-stale-cache` map bullet loses its known-bug marker and its prose and Gotchas line now describe the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`
- `openhands-agent-server/openhands/agent_server/skills_service.py`
- `tests/agent_server/test_skills_service.py`

Linked issue acceptance context:

- [#4665](https://github.com/OpenHands/software-agent-sdk/issues/4665): [Bug]: Skills marketplace returns empty against the current extensions manifest layout — current state open, closed —.

> - [ ] Skills load from the real `.plugin/marketplace.json` layout when `marketplaces/default.json` is absent. - [ ] Custom explicit marketplace paths remain supported as fallback. - [ ] Skills and plugins within the TTL use the same fetched/parsed snapshot. - [ ] Transient fetch/parse failures do not cache an empty catalog. - [ ] Existing plugin-over-skill name precedence, plugin filtering, installed-state freshness, and REST schemas remain unchanged. ### Version Current `main` at `6d3881035`. Repository layout was independently verified through the GitHub API on 2026-08-27; existing issue search found no duplicate.

- [#5893](https://github.com/OpenHands/software-agent-sdk/issues/5893): [Bug]: GET /api/skills/marketplace keeps serving its cached catalog (including an empty one) after POST /api/skills/sync — current state closed, closed 2026-10-14T20:14:38Z.

> - [ ] A successful `POST /api/skills/sync` clears the marketplace catalog cache, so the next `GET /api/skills/marketplace` lists entries the sync brought in. - [ ] An empty catalog result is not cached (same rule as the plugin catalog), so a transient failure does not blank the tab for the TTL. - [ ] A unit test covers both. - [ ] The map bullet `F25.marketplace-stale-cache` passes with its `# bug` marker removed. ### Installation Method `uv sync` in a checkout of main. ### Version main at 6e7a8532ef4426da08b5c06dd57cf4795303be01 ### Additional Context Root cause: - `openhands-agent-server/openhands/agent_server/skills_service.py:462-484` (`sync_public_skills`) calls only `_invalidate_public_skills_cache()` on success; the catalog's `_catalog_cache` (`skills_service.py:659`) is left alone. - `skills_service.py:688-692` (`service_get_marketplace_catalog`) stores whatever `_fetch_catalog_entries` returned, including `[]`; compare `plugins_service.py:282-285`, which caches only non-empty results. Related: https://github.com/OpenHands/software-agent-sdk/issues/4665 (empty marketplace against the current manifest layout). This is the separate cache-invalidation case noted there (https://github.com/OpenHands/software-agent-sdk/issues/4665#issuecomment-6112142654); fixing the manifest layout alone does not make newly synced entries visible. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5903#issuecomment-6158830354)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5903#pullrequestreview-5512443918): _This review was posted by an AI agent (OpenHands)._ ## Review: PR #5903 — clear the skills marketplace cache on sync **Scope:** In scope. The change is confined to the Agent Server package this repository owns (`openhands-agent-server/openhands/agent_server/skills_service.py`), its unit tests, and the `verify-agent-server` feature map. No cross-repo move is needed. **Assessment:** The fix matches the linked issue's acceptance criteria (#5893 — `bug`, `priority:low`, `ready-for-dev`) and mirrors the existing `plugins_service` precedent. - `sync_public_skills` now calls `_invalidate_catalog_cache()` on success (line 479), so the next `GET /api/skills/marketplace` re-reads the refreshed clone instead of serving the stale 5-minute entry. A failed sync leaves the cache intact, consistent with the existing `_invalidate_public_skills_cache` han

### #5905: fix(sdk): name a manifest-less git repository plugin after the repository

- Commit: `7bb4be18ca42bf0e44748183baa6a56eb329621a` (2026-10-14T22:17:13+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5905).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5905). Families: F25, F26, F28.

Stated intent:

> - `InstallationManager.install` now loads a remote source fetched without `repo_path` from a temporary copy (without `.git`) named after the repository (`extract_repo_name`, the helper that names the cache directory), so a loader that infers the name from the directory sees `<repo>`, as for a local directory. Local sources and `repo_path` installs load in place as before; the installed files are still copied from the fetched path. `update` loads the root under the installed name (see Decision needed).
> - Tests: a manifest-less `file://` git repository installs as `qa-src` (fails on main with `qa-src-<hash>`); a root installed under another name keeps it on update; the network test `test_install_document_skills_plugin` asserted the old cache-directory name for `github:anthropics/skills` and now expects `skills`.
> - Map: `F26.install-repo-root-name` loses its `# bug` marker and "known bug" label; the prose and the Claude Code layout Gotcha state the expected name.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F26-plugins.md`
- `openhands-sdk/openhands/sdk/extensions/installation/manager.py`
- `tests/sdk/plugin/test_installed_plugins.py`

Linked issue acceptance context:

- [#5896](https://github.com/OpenHands/software-agent-sdk/issues/5896): [Bug]: A git plugin without a manifest, installed without repo_path, is named after its cache directory (repo-HASH) instead of the repository — current state closed, closed 2026-10-14T20:17:15Z.

> - [ ] A git source installed without `repo_path` and without a root manifest is named after the repository (`extract_repo_name` of the URL), and installed under that directory. - [ ] Sources with a manifest, sources with `repo_path`, and local paths keep their current names. - [ ] A unit test in `tests/sdk/extensions/` (or `tests/sdk/plugin/`) covers installing a manifest-less git repository root. - [ ] The map bullet `F26.install-repo-root-name` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `InstallationManager.install` (`openhands-sdk/openhands/sdk/extensions/installation/manager.py:89-97`) loads the extension straight from the path `fetch_with_resolution` returns. For a remote source without `repo_path`, that is the clone root `cache/extensions/<repo>-<sha256[:16]>` built by `get_cache_path` (`openhands-sdk/openhands/sdk/extensions/fetch.py:209-228`, used at line 257). - With no manifest, the Claude Code format falls back to the directory name (`openhands-sdk/openhands/sdk/plugin/format/claude_code.py:106-115`), so the cache directory name becomes the plugin name. `Plugin.load` resolves the path, so a symlink with the right name would not help. Related: #4520 (GitHub shorthand parsing), #5496 (concurrent `.installed.json` writes). Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5905#issuecomment-6158821345)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5905#pullrequestreview-5512583491): _This review was posted by an AI agent (OpenHands)._ ## Assessment The change fixes the reported bug correctly and stays within this repository's ownership (SDK plugin/skill/extension installation). Verified against the current head `53bd765abebf7982ae1b56ed721bd61bfedadfaa`; the head has not moved and no prior review exists on it. What I checked: - **Root cause and fix.** `InstallationManager._load_fetched` now stages a remote source fetched without `repo_path` into a temp copy named after the repository (`extract_repo_name(url)`, or the installed name on `update`) and loads from there, so a manifest-less plugin sees `<repo>` instead of the `<repo>-<hash>` clone-cache directory. `repo_path` installs and `SourceType.LOCAL` sources still load in place, matching the issue's stated unchanged cases. The name source (`extract_repo_name`) is th

### #5921: fix(agent-server): leave web-researcher out of sub-agents when the browser is disabled

- Commit: `4804aa0be06f3a90e88037a3a580e7c069a2195b` (2026-10-14T22:19:54+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5921).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5921). Families: F27.

Stated intent:

> - `get_sub_agents` passes `enable_browser=configured_browser_available(config) is not False` to `discover_builtin_agents`. The function already filtered `web-researcher`; it was just never told to.
> - Unit test (parametrized on `enable_browser`) in `tests/agent_server/test_sub_agents_router.py`.
> - Map: `F27.subagents-browser-disabled` now restarts with `enable_browser: false` itself. Before, it relied on an earlier bullet's restart, so `--only` ran it with the browser enabled. Its `# bug` marker is removed and the prose describes the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`
- `openhands-agent-server/openhands/agent_server/sub_agents_router.py`
- `tests/agent_server/test_sub_agents_router.py`

Linked issue acceptance context:

- [#5911](https://github.com/OpenHands/software-agent-sdk/issues/5911): [Bug]: POST /api/sub-agents lists the browser-only web-researcher when enable_browser is false — current state closed, closed 2026-10-14T20:19:56Z.

> - [ ] `POST /api/sub-agents` leaves out `web-researcher` when the server's config disables the browser (`enable_browser: false`; for a Docker conversation runtime, whenever the catalog's configured browser availability is false). - [ ] With the browser enabled the built-in list is unchanged (still includes `web-researcher`); project/user agents are unaffected. - [ ] `tests/agent_server/test_sub_agents_router.py` covers both configurations. - [ ] The map bullet `F27.subagents-browser-disabled` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `get_sub_agents` (`openhands-agent-server/openhands/agent_server/sub_agents_router.py:129`) calls `discover_builtin_agents()` with no argument. - `discover_builtin_agents(enable_browser: bool = True)` (`openhands-tools/openhands/tools/preset/default.py:111`) already filters `web-researcher` when `enable_browser` is False (line 128), but the default `True` is always used. - The tool catalog route already derives browser availability from config with `configured_browser_available(config)` (`openhands-agent-server/openhands/agent_server/launch.py:103`, used in `tool_router.py:57`); the sub-agents route never reads `request.app.state.config`. Related (separate items, not in scope): `/server_info` `usable_tools` ignoring `enable_browser` (`F27.server-info-browser-disabled`), and the import-time `register_builtins_agents(enable_browser=True)` in `tool_router.py` that keeps `web-researcher` in the delegation registry. Found by the first

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5921#issuecomment-6159129204)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5921#pullrequestreview-5512470254): _This review was posted by an AI agent (OpenHands)._ ## Review summary Scope is correct: the fix lands in the Agent Server REST route that this repository owns, matching the linked issue #5911 (open, `bug` / `priority:low` / `ready-for-dev`) and its acceptance criteria. ### What I verified on head `c5dc63d` - **Root cause and fix.** `get_sub_agents` previously called `discover_builtin_agents()` with its default `enable_browser=True`, so a config with `enable_browser: false` still listed the browser-only `web-researcher`. The route now reads `http_request.app.state.config` (already set in `create_app`) and passes `enable_browser=configured_browser_available(config) is not False`, the same helper the tool catalog route uses (`tool_router.py`). `configured_browser_available` returns `False` when `enable_browser` is off, `container_browser_en

### #5931: fix(agent-server): answer uploads onto a directory or below a file with 400

- Commit: `75105bd40936f1962d8fa91506776a0c02ba957f` (2026-10-14T22:24:23+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5931).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5931). Families: F14.

Stated intent:

> - `_upload_file` now maps `IsADirectoryError` to 400 `Path is a directory`, and `FileExistsError` / `NotADirectoryError` (which `mkdir(parents=True)` raises when a parent is a file) to 400 `A parent of the path is not a directory`. Every other failure still returns 500. The conversation-scoped route `POST /api/conversations/{id}/file/upload` mounts the same handler.
> - Tests: a parametrised test on the global route covers a directory target, a direct parent that is a file, and an ancestor further up that is a file. A conversation-scoped test covers both collisions. Every test also asserts that nothing on disk changed.
> - Feature map: `F14.upload-onto-directory` no longer has its `# bug` markers and now asserts the exact 400 details.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`
- `openhands-agent-server/openhands/agent_server/file_router.py`
- `tests/agent_server/test_file_router.py`
- `tests/agent_server/test_runtime_router.py`

Linked issue acceptance context:

- [#5930](https://github.com/OpenHands/software-agent-sdk/issues/5930): [Bug]: POST /api/file/upload onto an existing directory, or below a file, returns a masked 500 instead of 400 — current state closed, closed 2026-10-14T20:24:25Z.

> - [ ] Uploading onto an existing directory returns 400 with a clear `detail`, and the directory is unchanged. - [ ] Uploading to a path whose parent (direct or further up) is a regular file returns 400 with a clear `detail`, and the file is unchanged. - [ ] Other upload failures (I/O errors, etc.) still return 500. - [ ] `tests/agent_server/test_file_router.py` covers both cases. - [ ] The map bullet `F14.upload-onto-directory` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/file_router.py`: - `_upload_file` (line 85) runs `target_path.parent.mkdir(parents=True, exist_ok=True)` (line 98) and `open(target_path, "wb")` (line 104); `mkdir` raises `FileExistsError` / `NotADirectoryError` when a parent is a file, and `open` raises `IsADirectoryError` when the target is a directory. - The handler's `except Exception` (lines 113-118) turns every non-`HTTPException` into a 500. - `create_directory` (lines 731-740) already maps `FileExistsError` / `NotADirectoryError` to 400 `Path exists and is not a directory`. Related: the same `_upload_file` is touched by the open upload fixes for the response `file_size` (F14.upload-size) and atomic overwrites (F14.upload-atomic). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5931#issuecomment-6159343821)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5931#pullrequestreview-5512691922): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope. The change belongs in `openhands-agent-server`: it edits the canonical REST handler `_upload_file` in `openhands-agent-server/openhands/agent_server/file_router.py`, which this repository owns per the cross-repo boundaries in `AGENTS.md`. No product/architecture decision is needed — the linked issue #5930 (labels `bug`, `priority:low`, `ready-for-dev`) already fixed the intended status code as 400, consistent with `create_directory` in the same file. ## Findings No material correctness, security, or compatibility defects found. Verified against current head `0f38cdba`: - The two new `except` branches are disjoint and correctly typed. `IsADirectoryError` maps to 400 `Path is a directory`; `(FileExistsError, NotADirectoryError)` maps to 400 `A parent of the path is not 

### #5924: fix(agent-server): apply enable_browser to /server_info usable_tools

- Commit: `8efff8d3cf1a5a3fcf7ce5e3b97c7f00684d9547` (2026-10-15T03:24:31+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5924).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5924). Families: F01, F29.

Stated intent:

> - `build_server_info` takes `browser_available` and `get_server_info` passes `configured_browser_available(config)`, the same decision the catalog route applies; `None` (local runtime, browser enabled) keeps the Chromium probe.
> - Unit tests for the route with `enable_browser=False` and for `build_server_info` with `None`/`True`/`False`.
> - Map: `F27.server-info-browser-disabled` drops its `# bug` marker, declares `Requires: F27.browser-probe, F27.browser-disabled` (its setup, so `--only` works alone), and `F27.catalog-docker-runtime` now asserts `usable_tools` lists the browser for the stock image.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`
- `openhands-agent-server/openhands/agent_server/server_details_router.py`
- `tests/agent_server/test_server_details_router.py`

Linked issue acceptance context:

- [#5915](https://github.com/OpenHands/software-agent-sdk/issues/5915): [Bug]: /server_info usable_tools lists browser_tool_set while enable_browser is false — current state closed, closed 2026-10-15T01:24:33Z.

> - [ ] With `enable_browser: false`, `GET /server_info` `usable_tools` does not contain `browser_tool_set`, matching `GET /api/tools/catalog`. - [ ] With `enable_browser: true` (local runtime), `usable_tools` is unchanged (still decided by the Chromium probe). - [ ] A unit test in `tests/agent_server/test_server_details_router.py` covers the disabled case. - [ ] The map bullet `F27.server-info-browser-disabled` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `ServerInfo.usable_tools` (`openhands-agent-server/openhands/agent_server/server_details_router.py:58`) defaults to `list_usable_tools()` (`openhands-sdk/openhands/sdk/tool/registry.py:230`), which runs only each tool's usability check (for the browser, the Chromium probe) and never sees `Config.enable_browser`. `get_server_info` (`server_details_router.py:152`) does not pass the config's browser decision either. - The catalog route applies `configured_browser_available(config)` (`openhands-agent-server/openhands/agent_server/launch.py:103`, used at `tool_router.py:57`), and launches drop the browser, so only `/server_info` disagrees. - The same helper also fixes the browser for `conversation_runtime: docker` from the conversation image; `/server_info` ignores that too (noted in the map's `F27.catalog-docker-runtime` bullet). Related: https://github.com/OpenHands/software-agent-sdk/issues/5899 (Chromium probe ignores `PLAYWRIGHT_BROWSERS_PATH`; a separate probe problem). Found by the first full agentic run of ve

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5924#issuecomment-6159141443)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5924#pullrequestreview-5519991487): _This review was posted by an AI agent (OpenHands)._ Reviewed head `fc6ebe3` (matches the requested SHA). Scope is correct: the Agent Server REST surface is owned by this repository, and the linked issue #5915 is `ready-for-dev` (`bug`, `priority:low`). What I verified on the current head: - The diff does what the description says: `build_server_info` takes `browser_available`, and `get_server_info` passes `configured_browser_available(config)` — the same helper the tool-catalog route uses. `None` (local runtime, browser enabled) keeps the process Chromium probe in charge, and non-browser tools are untouched. - Focused tests: `uv run pytest tests/agent_server/test_server_details_router.py` → 14 passed, including the new disabled-browser test and the `None`/`True`/`False` coverage of `build_server_info`. - Live endpoint check with a Chromi
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5924#issuecomment-6178130283): I'm an AI agent (Claude Code) helping Engel Nyst (@enyst) with project maintenance. Merged `main` into this branch (c3f2a8e). The only conflict was in the F27 map, where main's sub-agents fix (`F27.subagents-browser-disabled`, no longer a known bug) and this PR's `/server_info` fix touched neighbouring lines; both are kept. The code merged cleanly, the router tests pass, and `map check` and pre-commit are clean. `agent-server-stress-tests` is red on the merge commit, but it isn't caused by this PR: `test_concurrent_conversations_isolated_and_fast` fails the same way on `main`'s head (75105bd, 0.59s against a 0.54s budget), and this PR doesn't touch conversation concurrency. I haven't found an existing fix for it. I've re-run the failed job once. --- _Generated by [Claude Code](https://claude.ai/code)_

### #5983: refactor(agent-profiles): remove inline profile launches and keep a profile active

- Commit: `818c161b534477849716d445b2927b433f9dd9ab` (2026-10-15T10:51:09+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5983).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5983). Families: F04, F05, F06, F10, F12, F16, F18, F19, F20, F21, F22, F27, F30, F31, F32, F33, F34.

Stated intent:

> - **The inline launch source is removed.** `StartConversationRequest.agent_profile` and `LaunchedAgentProfile.inline` are gone, along with their handling in launch, Docker forwarding and stored requests. A request whose only agent source is `agent_profile` now fails validation with "One of `agent`, `agent_settings`, or `agent_profile_id` must be provided". Next to another source, `agent_profile` is ignored like any unknown field. `resolve()` still accepts a profile object, because materialize previews of unsaved drafts use it.
> - **A profile is always active.** A new locked helper, `_ensure_active_profile`, moves a missing or dangling pointer to `default`. When `default` doesn't exist, it seeds it from the current settings. Listing profiles, resolving the active profile and deleting the active profile all call it.
>   - Deleting the active profile now activates `default` instead of clearing the pointer. Deleting an active `default` reseeds it.
>   - When `default` exists but can't be loaded, or the store is already at the 50-profile limit without one, the first other profile is activated.

Changed paths:

- `openhands-agent-server/openhands/agent_server/agent_profiles_router.py`
- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `openhands-agent-server/openhands/agent_server/docker_runtime/routers.py`
- `openhands-agent-server/openhands/agent_server/launch.py`
- `openhands-agent-server/openhands/agent_server/openai/service.py`
- `openhands-sdk/openhands/sdk/conversation/request.py`
- `openhands-sdk/openhands/sdk/launch/resolve.py`
- `openhands-sdk/openhands/sdk/profiles/agent_profile.py`
- `openhands-sdk/openhands/sdk/settings/model.py`
- `tests/agent_server/test_agent_profile_conv_start.py`
- `tests/agent_server/test_agent_profiles_router.py`
- `tests/agent_server/test_launch_architecture.py`
- `tests/agent_server/test_launch_parity.py`
- `tests/agent_server/test_openai_gateway_launch.py`
- `tests/sdk/launch/test_resolve.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5983#issuecomment-6167607191)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5983#pullrequestreview-5519317231): Approved — safe to merge. **Why this is safe:** - Removes `StartConversationRequest.agent_profile` and `LaunchedAgentProfile.inline`, both of which only appeared after v1.54.0, so no released client sends or reads them. - Makes a stored Agent Profile always active: a missing/dangling pointer moves to `default` (seeded from current settings when absent), and deleting the active profile activates `default`. `_ensure_active_profile` runs under the store lock, so concurrent first requests seed once. - The gateway now launches a stored profile and returns 409 when none can be made active, instead of falling back to an inline unsaved profile. - All CI is green, including the OpenAPI breakage check (PASSED), sdk/agent-server/workspace/cross tests, ACP live tests, and agent-server stress tests. No blocking issues found. One non-blocking note for 
- [reviews; CHANGES_REQUESTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5983#pullrequestreview-5520703887): **REQUEST_CHANGES** I traced the changed paths on the current head (`c67bfe4`) and found one material, reproducible regression. Everything else in the PR checks out. --- ### Blocking: a corrupt/unreadable `default` file bricks the profile API `agent_profiles_router.py:266` (`_profile_to_activate`) only catches `FileNotFoundError` from `store.load(SEED_PROFILE_NAME)`: ```python try: profile = store.load(SEED_PROFILE_NAME) except FileNotFoundError: ...seed... ``` `AgentProfileStore.load()` raises `ValueError` (not `FileNotFoundError`) when the file exists but is corrupt or fails validation (`agent_profile_store.py:203-207`). The exception then escapes `_profile_to_activate` → `_ensure_active_profile` → `store_errors()`, which maps `ValueError` to **400**. Reproduced on the head with a single corrupted `default.json` and no active pointer: `
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5983#pullrequestreview-5521449970): **APPROVE** Re-reviewed at `213f756`. The blocking issue from my last review is fixed, and I verified it against the running code rather than just the diff. ### The corrupt-`default` fix (was blocking, now resolved) `_profile_to_activate` now handles a `ValueError` from `store.load("default")` and falls through to activating the first other profile, leaving the unreadable file untouched. I reproduced all four cases on the head: \| State \| Result \| \|---\|---\| \| corrupt `default` + another profile \| `200`, corrupt file byte-for-byte untouched, `mine` active \| \| corrupt `default` alone \| `200`, no active profile (same as `main`) \| \| delete corrupt `default` \| `200`, fresh `default` seeded and activated \| \| missing / dangling pointer \| unchanged from the previous head \| The `GET /api/agent-profiles` 400 is gone, so a single ba
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/5983#issuecomment-6172646583): Addressed the review in 213f75650. - **Corrupt `default`:** `_profile_to_activate` now treats a `default` that fails to load like a missing one, except that it is never reseeded over. The file stays untouched and the first other profile is activated. With no other profile, nothing is active and `GET /api/agent-profiles` returns 200 with an empty list, as on `main`. Two tests cover both cases, and both fail on c67bfe4d4. - **Description:** it now covers the corrupt `default` case, and says that `agent_profile` is rejected only when it is the request's sole agent source. Next to another source it is ignored like any unknown field. `StartConversationRequest` doesn't forbid extra fields, and I left that as is.

### #5589: fix(agent-server): stop startup tmux cleanup killing live sibling sessions

- Commit: `f0206428306db687ac9c8d833ece4fb82b015eba` (2026-10-15T06:32:29-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5589).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-5589). Families: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34.

Stated intent:

> - Record the creating process as each socket's owner via a tmux user option (`@openhands_socket_owner_pid`) from `TmuxTerminal` / `TmuxPanePool` initialization.
> - `_cleanup_stale_tmux_sessions()` now reclaims an `openhands-<32 hex>` socket only when its recorded owner process is gone (reusing `_is_pid_alive` from `conversation_lease`). The legacy `openhands` socket is still always reclaimed, and sockets with no recorded owner are still reclaimed.
> - Replace the #5485 test that asserted the buggy behavior with a regression test that exercises a real live sibling pool plus an orphaned out-of-process socket in one `TMUX_TMPDIR`.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `c1dfd9c52cd3` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -1076,0 +1077 @@
> +schema AgentLaunchAdditions property llm_profile_ref optional schema=anyOf=[type="string" minLength=1,type="null"]
> @@ -2090,0 +2092,2 @@
> +schema LaunchedAgentProfile property inline optional schema=type="boolean" default=false
> +schema LaunchedAgentProfile property llm_profile_ref optio

Changed paths:

- `openhands-agent-server/openhands/agent_server/api.py`
- `openhands-tools/openhands/tools/terminal/constants.py`
- `openhands-tools/openhands/tools/terminal/terminal/tmux_pane_pool.py`
- `openhands-tools/openhands/tools/terminal/terminal/tmux_terminal.py`
- `openhands-tools/pyproject.toml`
- `tests/agent_server/test_api.py`
- `uv.lock`

Linked issue acceptance context:

- [#5581](https://github.com/OpenHands/software-agent-sdk/issues/5581): [Bug]: Agent-server startup kills live terminal sessions of other conversations sharing the same `TMUX_TMPDIR` — current state closed, closed 2026-10-15T10:32:31Z.

> - [ ] With a shared, externally supplied `TMUX_TMPDIR` and two concurrently live agent-server processes, running `_cleanup_stale_tmux_sessions()` (the call made by `api_lifespan()` at startup) leaves the other live process's tmux sessions running and its terminals still usable; a command started before the cleanup still yields its output afterward. - [ ] A socket whose owning agent-server process is no longer running is still reclaimed by the startup cleanup, so orphaned tmux servers/sockets do not accumulate. - [ ] Behavior for an unset/defaulted `TMUX_TMPDIR` is unchanged: the per-process directory is created and used, and legacy/stale sockets in it are still cleaned up on startup. The non-POSIX early-return path is preserved. - [ ] The #5485 test `test_cleanup_stale_tmux_sessions_includes_isolated_sockets` no longer asserts that an `openhands-<32 hex>` socket belonging to a live conversation is killed, and a new regression test covers both outcomes (live sibling preserved; owner-gone socket reaped) using real `libtmux.Server` handles within a single `TMUX_TMPDIR`. - [ ] No public Python API, REST/WebSocket contract, persisted settings, event shape, or environment-variable contract changes; no new required configuration. - [ ] The agent-server test suite passes, including the new regression test, and the change passes the repository lint/format checks.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5589#issuecomment-6050660253)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5589#pullrequestreview-5451441816): _This review was posted by an AI agent (OpenHands)._ ## Scope This change belongs in this repository: it fixes agent-server startup behavior (`openhands-agent-server/openhands/agent_server/api.py`) and the terminal tool that records socket ownership (`openhands-tools/.../tmux_terminal.py`, `tmux_pane_pool.py`, `constants.py`). No cross-repository move or product decision is needed, and there are no public Python/REST/WebSocket contract changes (older sockets without a recorded owner are still reclaimed, so the stale-cleanup intent is preserved). Issue #5581 carries `bug`, `tools`, `agent-execution`, `priority:medium`, `ready-for-dev`. ## Verification performed on head `7a102dd` - Re-read the current PR body, labels, reviews, and review threads: no prior reviews or open threads, head matches, PR open and non-draft. - Current-head CI is gre
- [reviews; COMMENTED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5589#pullrequestreview-5470294699): # PR #5589 — follow-up review 🟡 **Taste rating: Acceptable** — small, appropriately scoped ownership check, but publication of ownership is not synchronized with destructive cleanup. **Verified head:** `7a102dd4c30d78bca6ca1f08fb505cb125f281f5` (rechecked via GitHub API; open, non-draft). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5589). Reviewed all five changed files, full affected implementations and callers, issue #5581 and its acceptance criteria, root/package AGENTS.md, and the custom review guide. No repository code or remote state was changed. ## Material finding ### P2 — Concurrent startup can still kill a live pool before its owner is recorded **Evidence:** [`tmux_pane_pool.py:129–138`](https://github.com/OpenHands/software-agent-sdk/blob/7a102dd4c30d78bca6ca1f08fb505cb125f281f5/openhands-tools/openhands/tools/ter
- [reviews; COMMENTED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5589#pullrequestreview-5481595809): 🟡 **Taste: Acceptable — follow-up on the existing startup-race finding.** Thank you for the focused fix and real tmux reproduction. The normal initialized-sibling and orphan-cleanup evidence is useful and sufficient for the issue’s scoped reproduction; I am not asking for a full Canvas deployment as an additional requirement. The head remains unchanged from [the October 9 review](https://github.com/OpenHands/software-agent-sdk/pull/5589#pullrequestreview-5470294699), so its demonstrated **P2 concurrent-startup gap** still needs addressing. [Pool initialization](https://github.com/OpenHands/software-agent-sdk/blob/7a102dd4c30d78bca6ca1f08fb505cb125f281f5/openhands-tools/openhands/tools/terminal/terminal/tmux_pane_pool.py#L129-L138) publishes a real socket before recording its owner. During that interval, [cleanup](https://github.com/OpenHa
- [reviews; COMMENTED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5589#pullrequestreview-5503503917): Please re-request my review when the previous comments are reflected. _This review comment was created by an AI agent (OpenHands) on behalf of Graham Neubig._
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5589#pullrequestreview-5508799566): _This review was posted by an AI agent (OpenHands)._ ## Scope This change belongs in this repository: it fixes agent-server startup behavior (`openhands-agent-server/openhands/agent_server/api.py`) and the terminal tool that creates/owns tmux sockets (`openhands-tools/.../tmux_terminal.py`, `tmux_pane_pool.py`, `constants.py`). No cross-repository move or product decision is needed. The linked issue #5581 is open with `bug`, `tools`, `agent-execution`, `priority:medium`, `ready-for-dev`; the PR references it via `Fixes #5581`. No public Python/REST/WebSocket contract change (older ownerless sockets are still reclaimed). ## What changed since the previous head The head moved from `7a102dd` to `c4b41b0`. This pass reviewed the delta: a merge of `main` plus the fix for the concurrent-startup gap that reviewer @neubig demonstrated on the prev
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5589#issuecomment-6081321442): @all-hands-bot Please take another look at this PR in light of the [follow-up review and deterministic reproduction](https://github.com/OpenHands/software-agent-sdk/pull/5589#pullrequestreview-5470294699). The previously speculative new_session → mark_socket_owner window was reproduced on head `7a102dd4c30d78bca6ca1f08fb505cb125f281f5` using real tmux and SDK operations: cleanup kills the unmarked socket while its owner process is still alive, and initialization fails. Please assess this remaining concurrent-startup gap and the proposed synchronization/regression coverage. This demonstrates an incomplete fix, not a new regression; ordinary scheduling frequency was not measured. _This comment was generated and posted by an AI agent (OpenHands) on behalf of Graham Neubig (@neubig)._
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5589#issuecomment-6146086092): @all-hands-bot please fix merge conflicts, review comments, and any failing CI.
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5589#issuecomment-6147735099): Done — merge conflicts resolved, the review's concurrent-startup finding fixed, and CI is green. ## Merge conflicts Merged `main` into the branch (merge commit `c4b41b0`). The only conflict was in `tests/agent_server/test_api.py`, where both `main` and this branch added a test next to the same `skipif` decorator. Resolution keeps both: - `main`'s `test_ensure_server_tmux_tmpdir_replaces_too_deep_env` and its `short_tmp_path`/`deep_tmp_path` fixtures. - This branch's `test_cleanup_stale_tmux_sessions_spares_live_sibling_socket`. The PR is now `MERGEABLE` (was `CONFLICTING`). ## Concurrent-startup gap (the P2 from the follow-up review) The review is right that the earlier fix was incomplete: `new_session()` makes the socket discoverable, and the owner option was recorded afterward. In that window another process's `_cleanup_stale_tmux_sessi

### #6003: fix(task_tracker): accept common task status names

- Commit: `11d82b2c267ab180e94ebcb668dc3c1cbce28f85` (2026-10-15T06:33:49-05:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/6003).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-6003). Families: F27, F33.

Stated intent:

> - Task statuses are normalized before validation: case, spaces and hyphens are ignored, `pending` and `to do` mean `todo`, and `completed` and `complete` mean `done`.
> - Other values, such as `blocked` or `cancelled`, are still rejected with the same error.
> - The schema shown to the model and the saved task list still use only `todo`, `in_progress` and `done`.

Changed paths:

- `openhands-tools/openhands/tools/task_tracker/definition.py`
- `tests/tools/task_tracker/__init__.py`
- `tests/tools/task_tracker/test_task_tracker_status.py`

Linked issue acceptance context:

- [#6005](https://github.com/OpenHands/software-agent-sdk/issues/6005): task_tracker rejects task plans that use common status names like pending — current state closed, closed 2026-10-15T11:33:51Z.

> - [ ] `pending`, `to do`, `completed`, `complete`, and case, space or hyphen variants of the canonical statuses validate and are stored as `todo`, `in_progress` or `done`. - [ ] Other values, such as `blocked`, are still rejected with the same error. - [ ] The tool schema shown to the model is unchanged. ### Installation Method OpenHands Enterprise (agent-server image) ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/6003#issuecomment-6190148937)

Review / discussion decisions (chronological evidence retained in raw records):

- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6003#issuecomment-6190155012): @openhands /codereview-roasted Review exactly commit 9f72397b8d4565d55323dceac690a5ad000f5d12. If the PR head changes, report that and do not approve another commit. Review only: do not push code, merge, deploy, or change attribution. Check correctness, regression coverage, and whether the PR validation claims match the evidence. Keep code comments short. Post the substantive review and link it in your completion message. End the substantive review with these plain lines (no code fence), choosing one verdict: REVIEWED_HEAD: 9f72397b8d4565d55323dceac690a5ad000f5d12 REVIEW_REQUEST: efa980169efa46308a0dda2b7c7bbaf3 VERDICT: APPROVED Use VERDICT: CHANGES_REQUESTED instead when findings require changes. If unable to review, explain the blocker without an approval verdict.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6003#issuecomment-6190296517): @openhands /codereview-roasted Review exactly commit 9f72397b8d4565d55323dceac690a5ad000f5d12. If the PR head changes, report that and do not approve another commit. Review only: do not push code, merge, deploy, or change attribution. Check correctness, regression coverage, and whether the PR validation claims match the evidence. Keep code comments short. Post the substantive review and link it in your completion message. End the substantive review with these plain lines (no code fence), choosing one verdict: REVIEWED_HEAD: 9f72397b8d4565d55323dceac690a5ad000f5d12 REVIEW_REQUEST: cc68040232ef4ea0953332d25a5079c7 VERDICT: APPROVED Use VERDICT: CHANGES_REQUESTED instead when findings require changes. If unable to review, explain the blocker without an approval verdict.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6003#issuecomment-6190579552): ## Review of commit 9f72397b8d4565d55323dceac690a5ad000f5d12 **Taste: good.** Small, focused, correct change. ### What was verified - Head `9f72397b8d4565d55323dceac690a5ad000f5d12` matches the requested review target; base is `f020642` (main). Changed files: `openhands-tools/openhands/tools/task_tracker/definition.py` (+20/-2), new `tests/tools/task_tracker/test_task_tracker_status.py`, and an empty `tests/tools/task_tracker/__init__.py`. - The `mode="before"` validator normalizes (strip -> lowercase -> `-`/space -> `_`), matches the canonical Literal values first, then the alias map (`pending`/`to_do` -> `todo`; `completed`/`complete` -> `done`). Unknown strings pass through so Pydantic still raises the same ValidationError. Verified locally: `To Do`, `TODO`, `to-do`, `Pending`, `In-Progress`, ` in progress `, `DONE`, `complete` all nor
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6003#issuecomment-6190618574): Reviewed PR #6003 "fix(task_tracker): accept common task status names" at the requested head 9f72397b8d4565d55323dceac690a5ad000f5d12 and posted the substantive review. **Review posted:** https://github.com/OpenHands/software-agent-sdk/pull/6003#issuecomment-6190579552 **Verdict: APPROVED** Findings summary: - Confirmed the PR head matches the review request exactly (9f72397..., base f020642); if head changes, a re-review would be required. - Correctness: the `mode="before"` validator normalizes strip/lowercase/`-`/space→`_`, matches canonical Literal values first, then aliases (`pending`/`to_do`→`todo`, `completed`/`complete`→`done`); unknown values pass through so Pydantic still rejects `blocked`, `cancelled`, `""` with the same error. Verified all edge cases locally. No schema change reaches the model or persisted/emitted tasks (canoni

### #5990: fix(plugin): load plugins whose root plugin.json has no $schema with the Claude Code layout

- Commit: `8ad5845e46fc8984fb90b1b44e583c1b5c29cd0a` (2026-10-15T07:52:02-05:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5990).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5990). Families: F09, F26.

Stated intent:

> - A root `plugin.json` selects the Agent Plugins format only when it declares `$schema`, or when it is unreadable or not a JSON object. A broken or unsupported Agent Plugins manifest is still claimed and rejected, so it is never silently loaded under the directory name.
> - A root `plugin.json` without `$schema` falls through to the Claude Code layout, which loads the plugin's `skills/` directory.

Changed paths:

- `openhands-sdk/openhands/sdk/plugin/format/agent_plugins.py`
- `tests/sdk/plugin/test_agent_plugins_format.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5990#issuecomment-6177762963)

Review / discussion decisions (chronological evidence retained in raw records):

- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/5990#issuecomment-6177714852): @openhands /codereview-roasted Review exactly commit 9804021b4b4fb8e1d4a5c1d72751531ed7e58a20. If the PR head changes, report that and do not approve another commit. Review only: do not push code, merge, deploy, or change attribution. Check correctness, regression coverage, and whether the PR validation claims match the evidence. Keep code comments short. Post the substantive review and link it in your completion message. End the substantive review with these plain lines (no code fence), choosing one verdict: REVIEWED_HEAD: 9804021b4b4fb8e1d4a5c1d72751531ed7e58a20 REVIEW_REQUEST: e1bde915c72a45de9919e9dc0804d93e VERDICT: APPROVED Use VERDICT: CHANGES_REQUESTED instead when findings require changes. If unable to review, explain the blocker without an approval verdict.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/5990#issuecomment-6177943169): **Review target**: `9804021b4b4fb8e1d4a5c1d72751531ed7e58a20` (head unchanged, PR open, mergeable). Base `75105bd4` (main). No prior human reviews or unresolved threads. **What the change does**: `AgentPluginsFormat.detect()` now reads the root `plugin.json` instead of treating mere presence as a claim. A manifest is claimed (and later rejected by `load_manifest()`) when it is unreadable, invalid JSON, a non-object, or an object containing a `$schema` key; a valid JSON object **without** `$schema` falls through to the Claude Code layout. **Correctness** (traced through `detect_format` → `PluginFormat.load` → `Plugin.load`): - The vendored Agent Plugins schema is closed and *requires* `$schema` (`test_schema_is_closed` asserts `set(schema["required"]) == {"$schema", "name"}`). A root `plugin.json` without `$schema` therefore cannot be a le
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/5990#issuecomment-6177977214): The review is complete. I reviewed exactly commit `9804021b4b4fb8e1d4a5c1d72751531ed7e58a20` (head unchanged), and posted the substantive review as a comment on PR #5990 (no code pushed, no merge, per the review-only instruction). ## Result: APPROVED **Posted review**: https://github.com/OpenHands/software-agent-sdk/pull/5990#issuecomment-6177943169 ### What I verified **Target/context**: PR `OpenHands/software-agent-sdk#5990` — head `9804021b…` matches the requested review commit, base `75105bd4` (main), open and mergeable. No prior human reviews or unresolved threads. Fixes a `.NET skills` marketplace where 16 GitHub-Copilot-style plugins (root `plugin.json` without `$schema`) all failed to load under the Agent Plugins detector. **Correctness** (traced `detect_format` → `PluginFormat.load` → `Claude Code fallback`): - The vendored Agent

### #5984: refactor(agent-profiles): deprecate the legacy profile resolver

- Commit: `b187e88eb018ef9f5d10af2a66043d566873d412` (2026-10-15T14:59:40+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5984).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5984). Families: F21.

Stated intent:

> - Both functions are `@deprecated(deprecated_in="1.55.0", removed_in="1.60.0")`. That is the 5-minor runway `check_deprecations.py` requires.
> - Each deprecation names its replacement: `openhands.sdk.launch.resolve` for the first, and `openhands.sdk.launch.preview_launch` for the dry-run.
> - No behavior changes. The module docstring now says what the module is for and points at the replacements.

Changed paths:

- `openhands-sdk/openhands/sdk/profiles/resolver.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5984#issuecomment-6167608992)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5984#pullrequestreview-5519317991): Approved — safe to merge. **Why this is safe:** - Purely additive annotations: `resolve_agent_profile()` and `resolve_agent_profile_dry_run()` get `@deprecated(deprecated_in="1.55.0", removed_in="1.60.0")` plus a docstring update. No behavior changes at all. - The 5-minor-release runway satisfies `check_deprecations.py`, and the deprecation-check workflow is green. - Each deprecation names its replacement (`launch.resolve` / `launch.preview_launch`); warnings only start once the package reaches 1.55.0, matching existing deprecations. - Only one file changed, and all CI is green. No blocking issues found. _This review was generated by an AI agent (OpenHands) on behalf of the user._

### #6007: Release v1.55.0

- Commit: `fd387b58dc7b707b73c159845c0396a17000be24` (2026-10-15T15:43:14+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/6007).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-6007). Families: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34.

Stated intent:

> ## Release v1.55.0
> 
> This PR prepares the release for version **1.55.0**.
> 
> **Started by:** @simonrosenberg
> 
> ### Release Checklist
> - [x] Version set to 1.55.0
> - [ ] Fix any deprecation deadlines if they exist
> - [ ] Integration tests pass (tagged with `integration-test`)
> - [ ] Behavior tests pass (tagged with `behavior-test`)
> - [ ] Example tests pass (tagged with `test-examples`)
> - [ ] Security scan passes (tagged with `security-scan`)
> - [ ] Confirm any `release-note-required` PRs are accurately called out in the final release notes
> 
> ### What happens on merge
> When this PR is merged, the `create-release.yml` workflow will automatically:
> 1. Create a GitHub release with tag `v1.55.0` and auto-generated notes, plus an explicit preamble for merged `release-note-required` PRs
> 2. Explicitly dispatch PyPI, npm, GitHub Packages, agent-server image, and binary publishers against tag `v1.55.0`
> 3. Trigger `version-bump-prs.yml` after successful PyPI publication
> 
> 
> ---
> <details>
> <summary><b>🐳 Agent Server images for this PR</b> — GHCR package, pull/run commands, and all pushed tags (click to expand)<

Changed paths:

- `clients/typescript/package-lock.json`
- `clients/typescript/package.json`
- `clients/typescript/src/generated/agent-server-schema.ts`
- `openhands-agent-server/pyproject.toml`
- `openhands-sdk/pyproject.toml`
- `openhands-tools/pyproject.toml`
- `openhands-workspace/pyproject.toml`
- `uv.lock`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/6007#issuecomment-6192706254)

Review / discussion decisions (chronological evidence retained in raw records):

- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6007#issuecomment-6192706163): ## 🔒 Release Security Scan ### 🔒 Approval drift (time-of-check vs time-of-use) **❌ 8 finding(s)** Baseline: `v1.54.0` — 54 merged PR(s) in range. > ⚠️ 1 first-parent commit(s) in range did not match the `(#N)` squash convention and were **not** audited (a merge/rebase merge, or a lost `(#N)` suffix). These are a blind spot — inspect them: > - Release v1.55.0 \| PR \| finding \| detail \| \| -- \| ------- \| ------ \| \| #5924 \| `changed-after-approval` \| last approval by @all-hands-bot was on fc6ebe323, but merged head was [c3f2a8e96](https://github.com/OpenHands/software-agent-sdk/compare/fc6ebe32334bfe312921dbeb275fa92ab058ea7e...c3f2a8e96b7e3f9522436dab6c940d09e91f1c8c) — commits landed after review \| \| #5986 \| `changed-after-approval` \| last approval by @hieptl was on f79cf5050, but merged head was [9aae234e0](https://github.com
- [discussion; comment; simonrosenberg](https://github.com/OpenHands/software-agent-sdk/pull/6007#issuecomment-6192778926): **Approval-drift review for the security scan.** All 8 findings are benign. I checked what landed after each last approval. \| PR \| After approval \| Verdict \| \|---\|---\|---\| \| #5747, #4960, #5744, #5520, #5621 \| Only the automated "Remove PR-only artifacts" commit; every changed file is under `.pr/` \| benign \| \| #5657 \| One merge of `main`; `git show --remerge-diff` is empty, so no manual resolution \| benign \| \| #5924 \| One merge of `main`; its only conflict was in `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`, resolved by keeping both sides' updated bullets \| benign, docs only \| \| #5986 \| One commit editing the `normalize_git_url` docstring, +6/-2 \| benign, docstring only \| The one unaudited first-parent commit is this PR's own "Release v1.55.0" version bump. Other checks o
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6007#issuecomment-6192793834): # 🧪 Integration Tests Results **Overall Success Rate**: 97.9% **Total Cost**: $2.09 **Models Tested**: 5 **Timestamp**: 2026-10-15 13:07:00 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38739279281) - **litellm_proxy_deepseek_deepseek_v4.1_flash**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38739279281) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38739279281) - **litellm_proxy_anth
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6007#issuecomment-6193134479): # 🧪 Behavior Tests Results **Overall Success Rate**: 96.0% **Total Cost**: $17.42 **Models Tested**: 5 **Timestamp**: 2026-10-15 13:23:47 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38739279518) - **litellm_proxy_deepseek_deepseek_v4.1_flash**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38739279518) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38739279518) - **litellm_proxy_anthro

### #5991: fix(agent-server): drop pip's vendored pkg_resources

- Commit: `f5f849f5ab51502cdfff97870f29de95307882a4` (2026-10-15T09:15:51-05:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5991).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5991). Families: F01, F12, F16, F31.

Stated intent:

> - The `pip-revendored` stage deletes `setuptools==70.3.0` from pip's `vendor.txt` and removes pip's `pkg_resources.patch` before `vendoring sync`. The rebuilt pip and its `bom.cdx.json` then contain no setuptools.
> - The retirement guard also checks whether upstream pip still bundles `pkg_resources`. It asks for the stage to be removed only once upstream has also fixed urllib3 and msgpack.

Changed paths:

- `openhands-agent-server/openhands/agent_server/docker/Dockerfile`
- `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5991#issuecomment-6178654295)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5991#pullrequestreview-5525511555): _This review was posted by an AI agent (OpenHands)._ ## Assessment Approved. The change is correctly scoped to this repository (the agent-server Docker image, which this repo owns) and matches the linked issue #5988 (now closed) exactly. **What the head does** - `pip-revendored` deletes `setuptools==70.3.0` from pip's `_vendor/vendor.txt`, asserts no `setuptools==` line survives, and removes `tools/vendoring/patches/pkg_resources.patch` before `vendoring sync`. The rebuilt wheel and its `bom.cdx.json` contain no setuptools, so CVE-2025-47273 and CVE-2026-59890 disappear from the shipped `-python`/`-python-slim` images. - The retirement guard now also probes `pkg_resources` upstream, and documents the intended retirement condition (upstream drops `pkg_resources` **and** fixes urllib3/msgpack). **Verification performed on c35c32cd** - Pulle

### #5753: fix(sdk): list changes of a repository below a non-repository path

- Commit: `d5995133d9425e6a37756b28ae19543b5d85306d` (2026-10-15T21:47:40+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5753).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5753). Families: F15.

Stated intent:

> - `get_git_changes` (`openhands-sdk/openhands/sdk/git/git_changes.py`): when the requested directory is not a repository but repositories were found one level below it, start from no base changes and list theirs under their directory name (sorted by path, as before). If neither the directory nor any directory below it is a valid repository, the original `GitRepositoryError` is still raised, so the agent server still answers `200 []` and `LocalWorkspace.git_changes` still raises. A path that is itself a repository is unchanged.
> - Tests: an SDK test for the layout (prefixed paths and statuses) and for a plain directory whose only `.git` below is broken (still raises); an agent-server router test with real git for the global route.
> - verify-agent-server map: `F15.changes-repo-below-path` loses its `# bug` marker and "known bug" label; the prose and two Gotchas now describe the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`
- `openhands-sdk/openhands/sdk/git/git_changes.py`
- `tests/agent_server/test_git_router.py`
- `tests/sdk/git/test_git_changes.py`

Linked issue acceptance context:

- [#5641](https://github.com/OpenHands/software-agent-sdk/issues/5641): [Bug]: GET /api/git/changes returns [] for a non-repo directory holding a repository, not its changes — current state closed, closed 2026-10-15T19:47:42Z.

> - [ ] `GET /api/git/changes` (global and conversation-scoped) on a non-repository directory that holds a repository lists that repository's changes prefixed with its directory name, sorted by path. - [ ] A directory with no repository at or below it still returns `200 []`; a `path` that is itself a repository is unchanged. - [ ] `tests/sdk/git/test_git_changes.py` covers `get_git_changes` on this layout. - [ ] The map bullet `F15.changes-repo-below-path` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context Root cause, in `openhands-sdk/openhands/sdk/git/git_changes.py`: - `get_git_changes` (line 218) finds nested repositories with `glob("./*/.git")` (lines 219-222), then calls `get_changes_in_repo(cwd, ref=ref)` on the requested directory first (line 225). - `get_changes_in_repo` starts with `validate_git_repository` (line 171), which raises `GitRepositoryError` for a non-repository directory (`openhands-sdk/openhands/sdk/git/utils.py:518`), so `get_git_changes` exits before the nested-repository loop (line 239). - `_get_git_changes` (`openhands-agent-server/openhands/agent_server/git_router.py:65-69`) maps that to `[]`. `LocalWorkspace.git_changes` (`openhands-sdk/openhands/sdk/workspace/local.py:185`) raises it instead. `_resolve_git_repo_root` (`openhands-agent-server/openhands/agent_server/file_router.py:285`) already handles this layout for `GET /api/file/archive`. Suggested fix (import `GitRepositoryError`): if the base is not a repository but nested repositories were found, st

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5753#issuecomment-6154867283)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5753#pullrequestreview-5510053219): _This review was posted by an AI agent (OpenHands)._ ## Review of PR #5753 (head `758f9ea`) **Scope.** The change belongs in this repository: it fixes backend behavior in the SDK (`openhands-sdk/openhands/sdk/git/git_changes.py`) that backs the canonical agent-server REST API `GET /api/git/changes` (global and conversation-scoped). It is the correct owner per `AGENTS.md` cross-repository boundaries. Linked issue #5641 is `ready-for-dev` (`bug`, `priority:low`). **Change.** `get_git_changes` now tolerates a requested directory that is not itself a repository when one or more repositories are found one level below it: it starts from an empty base change list, lists each nested repository's changes under its directory name, and sorts by path as before. The original `GitRepositoryError` is re-raised when neither the directory nor any director
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5753#pullrequestreview-5510069892): _This review was posted by an AI agent (OpenHands)._ ## Review: fix(sdk): list changes of a repository below a non-repository path Scope: this repository owns the SDK/Agent Server git endpoints, and the change stays within the SDK git listing, its tests, and the verify-agent-server feature map. No product or architecture decision is needed. Verified on head `758f9ea6`: - The `{base}/{repo}` layout now lists the repository below a non-repository base as `proj/README.md` (UPDATED) + `proj/notes.txt` (ADDED), sorted by path. Reproduced against real git in the workspace, for both the default base detection and an explicit `ref=HEAD`. - The `GitRepositoryError` contract is preserved for the no-repository case: a plain directory raises, and a directory whose only `.git` below is broken (a `.git` file pointing nowhere) also raises, so the router

### #5696: fix(agent-server): resolve trajectory download dir from request config

- Commit: `cb266703c5c797f10f17eea8fea61a25ad2eac30` (2026-10-15T23:13:23Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5696).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5696). Families: F14.

Stated intent:

> - `download_trajectory` now resolves the conversations directory from `request.app.state.config`, the request-time config pattern already used by `dependencies.py`, `tool_router.py`, and `server_details_router.py`.
> - The existing trajectory tests install that request-time config instead of patching the module-level lookup.
> - Regression test drives a real deferred-init app end to end: boot with `deferred_init=True`, `POST /api/init` with a non-default `conversations_path`, create a conversation, then download its trajectory through both `/api/file/download-trajectory/{id}` and `/api/conversations/{id}/file/download-trajectory/{id}` (equivalent zips, both 404 with `Conversation not found` when the directory is missing under the resolved config, and no `.zip` residue after the downloads).

Changed paths:

- `openhands-agent-server/openhands/agent_server/file_router.py`
- `tests/agent_server/test_file_router.py`

Linked issue acceptance context:

- [#5640](https://github.com/OpenHands/software-agent-sdk/issues/5640): [Bug]: download-trajectory returns 404 after POST /api/init sets conversations_path (expected 200 zip) — current state closed, closed 2026-10-15T23:13:25Z.

> - [ ] After `POST /api/init` sets `conversations_path`, the global and scoped trajectory routes return 200 with the zip of the conversation stored there. - [ ] A regression test covers both routes on a deferred-init app initialized with a non-default `conversations_path`. - [ ] The `F14.trajectory-init-path` map bullet passes and its known-bug marker (`# bug`) is removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context Root cause: `download_trajectory` reads the launch-time default config, not the app's current config. Paths are under `openhands-agent-server/openhands/agent_server/`. - `file_router.py:922`: `config = get_default_config()`; lines 923-930 build `<conversations_path>/<hex>` from it and raise the 404. - `config.py:540-546`: `get_default_config()` returns the module singleton loaded from env/file at launch. - `init_router.py:175-176` merges the init-time `conversations_path` into a new `Config`, and `init_router.py:264` installs it only as `app.state.config`; the singleton keeps the launch-time value (default `workspace/conversations`). - `runtime_router.py:85` mounts `file_router` under `/conversations/{runtime_conversation_id}`, so the scoped route runs the same endpoint and fails the same way. - Other routers read the live config from the request: `dependencies.py:35`, `tool_router.py:53`, `server_details_router.py:152`. Suggested fix: give `download_trajectory` a `request: Request` parameter and read `request.app.state.config` instead of calling `get_default_config()`. ---

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5696#pullrequestreview-5540778904): _AI-assisted review by Codex for @enyst._ Reviewed `0b509ab4`: no material findings. Both trajectory routes now use the initialized app config, with the REST/OpenAPI shape preserved. Verified through isolated CLI-launched servers on macOS/Python 3.13.11, without model calls. On exact base `1cfba211`, the deferred-init workflow reproduced twice: conversation GET returned 200, but both trajectory routes returned 404. On this head, the same workflow passed twice: both routes returned 200 with matching ZIP contents, persisted state and event files were included, LLM/condenser keys were redacted, and repeated downloads left no temporary ZIP behind. Missing conversations still returned 404. All 123 file-router, init-router and OpenAPI tests passed. Upstream checks on this head are metadata checks; the runtime comparison and focused tests above 
- [reviews; APPROVED; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5696#pullrequestreview-5540981245): Thank you!
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5696#pullrequestreview-5544090606): _This review was posted by an AI agent (OpenHands)._ Reviewed exact head `bdda5799` (still the PR head; the change matches the linked issue's suggested fix). Scope gate passes: this is the repository that owns the Agent Server file/trajectory routes. **What checks out** `openhands-agent-server/openhands/agent_server/file_router.py:938` now reads `config: Config = request.app.state.config`, which is the config `POST /api/init` installs (`init_router.py:281`). Both the flat route `/api/file/download-trajectory/{id}` and the conversation-scoped alias mounted in `runtime_router.py` run the same endpoint, so both are fixed. This matches the request-time config pattern already used by `dependencies.py`, `tool_router.py`, and `server_details_router.py`; the REST/OpenAPI shape is unchanged (`Request` is not a serialized parameter), and the migrat
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5696#discussion_r4291752534): This `POST /api/init` does not send an `X-Init-API-Key` header, and the `Config` created just above it has no `secret_key`. `Config.secret_key` defaults through `_default_secret_key()`, which reads `SESSION_API_KEY` / `OH_SESSION_API_KEYS_0` from the ambient environment. When either variable is exported, the init gate (`check_init_api_key`) requires the matching `X-Init-API-Key`, so this request returns 401 and the test fails at the next `assert response.status_code == 200`. Reproduced on this head: with `OH_SESSION_API_KEYS_0` set, this test fails (401 at the `/api/init` assert); with `env -u OH_SESSION_API_KEYS_0` it passes (11 passed). `tests/agent_server/test_init_router.py` already isolates this with its autouse `_clean_env` fixture that deletes `SESSION_API_KEY`, `OH_SESSION_API_KEYS_0`, and `OH_SECRET_KEY`; this module has no such 

### #5987: feat(plugin): add strict plugin fetch preflight

- Commit: `db9c54b71c8c9ed46a2454fc65eee804d3c1e8aa` (2026-10-15T21:25:01-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5987).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5987). Families: F09, F21, F26, F30.

Stated intent:

> - Added `preflight_plugin(...)`, `PluginSourceError`, and `CheckedPlugin` to `openhands.sdk.plugin` with strict secret expansion, structured error codes, temporary checkout cleanup, and raw manifest/skills/commands metadata.
> - Added `POST /api/plugins/fetch` via the existing `/plugins` router to fetch and pin a plugin into an immutable commit-SHA cache path without installing or enabling it.
> - Added pinned-cache reuse for `fetch_plugin_with_resolution(...)` when a commit SHA was already materialized by the strict resolver, and fixed plugin error wording so URL text such as `extensions-private` is not rewritten.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `1a0fd91ad03d` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -149,0 +150 @@
> +operation POST /api/plugins/fetch operationId=fetch_plugin_endpoint_api_plugins_fetch_post
> @@ -408,0 +410 @@
> +requestBody POST /api/plugins/fetch application/json required=true schema=FetchPluginRequest
> @@ -756,0 +759,4 @@
> +response POST /api/plugins/fetch 200 application/json schema=FetchPluginRespon

Changed paths:

- `openhands-agent-server/openhands/agent_server/agent_profiles_router.py`
- `openhands-agent-server/openhands/agent_server/plugins_router.py`
- `openhands-agent-server/openhands/agent_server/plugins_service.py`
- `openhands-sdk/openhands/sdk/plugin/__init__.py`
- `openhands-sdk/openhands/sdk/plugin/fetch.py`
- `openhands-sdk/openhands/sdk/plugin/paths.py`
- `openhands-sdk/openhands/sdk/plugin/strict_fetch.py`
- `openhands-sdk/openhands/sdk/profiles/__init__.py`
- `openhands-sdk/openhands/sdk/profiles/agent_profile.py`
- `tests/agent_server/test_plugins_router.py`
- `tests/sdk/plugin/test_plugin_strict_fetch.py`
- `tests/sdk/profiles/test_agent_profile.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5987#issuecomment-6171132400)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5987#pullrequestreview-5527194299): _This review was posted by an AI agent (OpenHands)._ Reviewed head `10e032d86363a5ced83270dc0a7aa81564e31959` (`main` <- `feat/strict-plugin-fetch`, +882/-1). Focused tests pass locally (`uv run pytest tests/sdk/plugin/test_plugin_strict_fetch.py tests/agent_server/test_plugins_router.py -q` -> 16 passed). The linked issue #5748 is open with only the `enhancement` label; the `Validate PR description` check is failing because #5748 carries neither `ready-for-dev` nor a pre-rollout creation date. The strict resolver is a good addition and the pinned-cache path is sound, but the new `POST /api/plugins/fetch` endpoint and `preflight_plugin()` do not satisfy the error-contract and secret-handling requirements the issue states for the same inputs. Two demonstrated findings are attached inline: 1. An unparseable `source` (after `${VAR}` expansio
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5987#pullrequestreview-5527490418): _This review was posted by an AI agent (OpenHands)._ Re-reviewed head `97845e0d628a514252fc08765d97f301949fc3a7` (`fix(plugin): sanitize strict fetch failures`), which moved since the previous review. Both findings from that review are addressed and verified on this head: 1. `parse_extension_source` is now wrapped and maps `ExtensionFetchError` to `PluginSourceError("invalid_source", ...)` with `from None`. I reproduced the previous failures: `POST /plugins/fetch` with `not-a-real-source`, `github:oops`, or `ftp://...` now returns `422` with `{"code":"invalid_source","message":"Invalid plugin source."}` instead of `500`, and a `source="${TOKEN}"` with a `StaticSecret` returns the structured error with the expanded value absent from the body. `preflight_plugin` likewise raises `PluginSourceError(code="invalid_source")`. 2. `_store_pinned_c
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5987#discussion_r4278290353): `parse_extension_source` raises `ExtensionFetchError` for a source it cannot classify (e.g. `github:oops`, a bare relative path, or an `ftp://` URL). Nothing here converts that into `PluginSourceError`, so two failures follow: - The router only catches `PluginSourceError`, so `POST /api/plugins/fetch` returns `500 Internal Server Error` for these inputs instead of the documented `422` with a structured `{code, message}` body the issue requires. - The agent-server's unhandled-exception handler returns `str(exc)` in the response `exception` field. When the source is an unexpanded-to-unparseable `${VAR}` (e.g. `source="${PLUGIN_TOKEN}"` with a `StaticSecret`), the expanded secret value is echoed back in the HTTP body. Reproduced on this head: `status 500`, body `{"detail":"Internal Server Error","exception":"Unable to parse extension source:
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5987#discussion_r4278290359): `_store_pinned_checkout` copies the whole fetched checkout, including `.git`. `_run_git(["remote", "add", "origin", url], ...)` has already written the credential-bearing URL into `.git/config`, so the immutable pinned cache at `~/.openhands/cache/plugins/<repo>-<hash>/resolved-refs/<sha>` retains the expanded token in plaintext for as long as the cache lives. Verified on this head with `fetch_plugin_strict(source="file://x-access-token:SUPERSECRET@<repo>", ...)`: the cached `.git/config` contains `url = file://x-access-token:SUPERSECRET@/...`. The pre-flight path avoids this because its temporary checkout is deleted, but the endpoint path (which is the one the issue says runs inside the sandbox for automation bootstrap) persists the credential indefinitely. Scrub the remote URL after fetch (e.g. `git remote set-url origin <redacted>` or 
- [review_comments; comment; malhotra5](https://github.com/OpenHands/software-agent-sdk/pull/5987#discussion_r4278385138): Fixed in 97845e0: strict fetch now catches ExtensionFetchError from parse_extension_source after secret expansion and maps it to PluginSourceError(code="invalid_source") with a generic message and no exception cause, so /plugins/fetch returns the structured 422 body without echoing expanded secret values. Added SDK and endpoint regression tests for the secret-expansion case. This reply was created by an AI agent (OpenHands) on behalf of the user.
- [review_comments; comment; malhotra5](https://github.com/OpenHands/software-agent-sdk/pull/5987#discussion_r4278385269): Fixed in 97845e0: pinned plugin cache copies now exclude .git metadata, preventing credential-bearing origin URLs from being persisted in cached checkouts. Added a regression test that fetches with a credential-bearing file URL, reuses the cache, and asserts the cached artifact has no .git metadata or expanded secret value. This reply was created by an AI agent (OpenHands) on behalf of the user.
- [review_comments; comment; malhotra5](https://github.com/OpenHands/software-agent-sdk/pull/5987#discussion_r4278795381): Note: previous tried using pydantic built in type `JsonValue` but that generated an empty-object schema for the OpenAPI check
- [review_comments; comment; malhotra5](https://github.com/OpenHands/software-agent-sdk/pull/5987#discussion_r4289656187): Acknowledged. I kept the plugin JSON payload schema explicit rather than switching to Pydantic's JsonValue, so the OpenAPI output avoids the empty-object schema behavior noted here. This reply was created by an AI agent (OpenHands) on behalf of the user.

### #6021: Release v1.55.1

- Commit: `5fa4dea24c52fe3ac53fa4e3e67a1376b72189e2` (2026-10-15T21:58:41-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/6021).
- Intent: documented. Runtime: **family-sweep-with-capability-limits**; [feature evidence and limits](intent-runtime-overlay.md#pr-6021). Families: F01, F02, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24, F25, F26, F27, F28, F29, F30, F31, F32, F33, F34.

Stated intent:

> ## Release v1.55.1
> 
> This PR prepares the release for version **1.55.1**.
> 
> **Started by:** @malhotra5
> 
> ### Release Checklist
> - [x] Version set to 1.55.1
> - [ ] Fix any deprecation deadlines if they exist
> - [ ] Integration tests pass (tagged with `integration-test`)
> - [ ] Behavior tests pass (tagged with `behavior-test`)
> - [ ] Example tests pass (tagged with `test-examples`)
> - [ ] Security scan passes (tagged with `security-scan`)
> - [ ] Confirm any `release-note-required` PRs are accurately called out in the final release notes
> 
> ### What happens on merge
> When this PR is merged, the `create-release.yml` workflow will automatically:
> 1. Create a GitHub release with tag `v1.55.1` and auto-generated notes, plus an explicit preamble for merged `release-note-required` PRs
> 2. Explicitly dispatch PyPI, npm, GitHub Packages, agent-server image, and binary publishers against tag `v1.55.1`
> 3. Trigger `version-bump-prs.yml` after successful PyPI publication
> 
> 
> ---
> <details>
> <summary><b>🐳 Agent Server images for this PR</b> — GHCR package, pull/run commands, and all pushed tags (click to expand)</summ

Changed paths:

- `clients/typescript/package-lock.json`
- `clients/typescript/package.json`
- `clients/typescript/src/generated/agent-server-schema.ts`
- `openhands-agent-server/pyproject.toml`
- `openhands-sdk/pyproject.toml`
- `openhands-tools/pyproject.toml`
- `openhands-workspace/pyproject.toml`
- `uv.lock`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/6021#issuecomment-6205928690)

Review / discussion decisions (chronological evidence retained in raw records):

- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6021#issuecomment-6205922881): ## 🔒 Release Security Scan ### 🔒 Approval drift (time-of-check vs time-of-use) **❌ 1 finding(s)** Baseline: `v1.55.0` — 4 merged PR(s) in range. > ⚠️ 1 first-parent commit(s) in range did not match the `(#N)` squash convention and were **not** audited (a merge/rebase merge, or a lost `(#N)` suffix). These are a blind spot — inspect them: > - Release v1.55.1 \| PR \| finding \| detail \| \| -- \| ------- \| ------ \| \| #5987 \| `changed-after-approval` \| last approval by @all-hands-bot was on 97845e0d6, but merged head was [906430917](https://github.com/OpenHands/software-agent-sdk/compare/97845e0d628a514252fc08765d97f301949fc3a7...906430917242d7f6cb015c6413c6595b61945943) — commits landed after review \| Audited 4 PR(s): 3 clean, 1 flagged, 0 un-auditable. ### 📦 Supply-chain dependency diff **✅ no findings** Baseline: `v1.55.0` Added: *
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6021#issuecomment-6206026216): # 🧪 Integration Tests Results **Overall Success Rate**: 97.9% **Total Cost**: $2.17 **Models Tested**: 5 **Timestamp**: 2026-10-16 01:35:55 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38835162050) - **litellm_proxy_deepseek_deepseek_v4.1_flash**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38835162050) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38835162050) - **litellm_proxy_anth
- [discussion; comment; github-actions[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6021#issuecomment-6206037294): # 🧪 Behavior Tests Results **Overall Success Rate**: 96.0% **Total Cost**: $10.25 **Models Tested**: 5 **Timestamp**: 2026-10-16 01:36:54 UTC <details> <summary>📁 Detailed Logs & Artifacts</summary> Click the links below to access detailed agent/LLM logs showing the complete reasoning process for each model. On the GitHub Actions page, scroll down to the 'Artifacts' section to download the logs. - **litellm_proxy_openai_gpt_5.5**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38835162222) - **litellm_proxy_deepseek_deepseek_v4.1_flash**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38835162222) - **litellm_proxy_gemini_3.1_pro_preview**: [📥 View & Download Logs](https://github.com/OpenHands/software-agent-sdk/actions/runs/38835162222) - **litellm_proxy_anthro

### #5998: ci: defer PR artifact cleanup until after merge

- Commit: `12e967664c2353f4cb85dd27da04ade035ff078c` (2026-10-16T09:13:44+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5998).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-5998). Families: no product runtime required.

Stated intent:

> - Remove approval cleanup and use the existing cleanup PR after merge to remove `.pr/` from `main` for both same-repository and fork PRs.
> - Update the artifact notice, contributor instructions, PR template, and design-doc guidance to describe automatic removal after merge to `main`.
> - Fail the existing pre-commit job only for `rel-` release PRs whose committed tree contains root `.pr` entries.

Changed paths:

- `.agents/skills/pr-design-doc/SKILL.md`
- `.agents/skills/pr-design-doc/references/html-craft.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/pr-artifacts.yml`
- `.github/workflows/precommit.yml`
- `AGENTS.md`
- `CONTRIBUTING.md`

Linked issue acceptance context:

- [#5997](https://github.com/OpenHands/software-agent-sdk/issues/5997): Defer temporary PR artifact cleanup until after merge to main — current state closed, closed 2026-10-16T07:13:46Z.

> - [ ] `pr-artifacts.yml` no longer defines a job that runs on `pull_request_review`, and approving a same-repository PR does not push a `.pr/`-removal commit to the PR head branch, so no CI run is triggered by artifact cleanup on approval. - [ ] Approving a same-repository PR leaves the `.pr/` directory intact on the PR branch. - [ ] After a same-repository or fork PR that carried `.pr/` merges into `main`, the post-merge job opens or updates a single cleanup PR against `main` on `automation/remove-pr-artifacts` whose diff deletes only files under `.pr/`. - [ ] When `main` has no `.pr/` directory at merge time, the post-merge job creates no commit and opens no cleanup PR (the existing no-op path is preserved). - [ ] When a cleanup PR for `automation/remove-pr-artifacts` already exists, the post-merge job updates that same PR instead of opening a second one. - [ ] The `` comment, for both same-repository and fork PRs, states that `.pr/` is removed automatically after the PR merges to `main` through the cleanup PR, and no longer states that it is removed on approval. - [ ] The cleanup PR continues to be subject to the normal review and required-check protections for `main`, and `.github/workflows/pr-description-check.yml` still exempts the `automation/remove-pr-artifacts` branch. - [ ] `CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and `.agents/skills/pr-design-doc/SKILL.md` describe removal after merge to `main` consistently and no longer say a same-repository `.pr/` directory is removed on approval. - [ ] `actionlint .github/workflows/pr-artifacts.yml` passes, and t

REST compatibility: no bot report returned for this PR.

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5998#pullrequestreview-5528201235): _This review was posted by an AI agent (OpenHands)._ ## Review of `c0be31e4dd1278a90f04e5bb43d0f65b6474547c` Scope check: this change belongs here. `.github/workflows/pr-artifacts.yml` is the repository-owned workflow that manages the `.pr/` reviewer-artifact convention, and the linked issue #5997 (`priority:low`, `ready-for-dev`) scopes the fix to that workflow plus the guidance describing its timing. No SDK/Agent Server API or ownership boundary is crossed. ### What I verified on the current head - **Triggers and jobs.** The `pull_request_review` trigger and the `cleanup-on-approval` job are gone; `check-pr-artifacts` and `cleanup-after-merge` remain. No dangling references to `cleanup-on-approval` remain anywhere in the tree. - **Post-merge path unchanged.** The `cleanup-after-merge` job body is byte-identical to `origin/main` (comment

### #6009: fix(marketplace): load skills from registered repositories without a manifest

- Commit: `6530325e9dd0ea1b6b4f1994d7dab52764cfc3f1` (2026-10-16T12:04:36-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/6009).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-6009). Families: F09, F25, F26.

Stated intent:

> - When a registered source has no marketplace manifest, the registry reads it as a plain skills folder with the new `Marketplace.from_skills_dir`. Each `<name>/SKILL.md` folder in `skills/` and `.skills/` becomes a standalone skill. If neither folder exists, the skill folders directly in the source path are used, so `repo_path` can point at the skills folder itself.
> - Loose markdown files are never read, so a repository's README and docs do not become skills. These skills auto-load like standalone skills from a manifest, including selection by name.
> - A source with neither a manifest nor skill folders still fails, now with a message saying both were missing. An invalid manifest is still an error.

Changed paths:

- `openhands-sdk/openhands/sdk/marketplace/registry.py`
- `openhands-sdk/openhands/sdk/marketplace/types.py`
- `tests/agent_server/test_skills_service.py`
- `tests/sdk/conversation/test_local_conversation_plugins.py`
- `tests/sdk/marketplace/test_marketplace_registry.py`

Linked issue acceptance context:

- [#6008](https://github.com/OpenHands/software-agent-sdk/issues/6008): [Bug]: A registered skills repository without a marketplace manifest loads no skills — current state closed, closed 2026-10-16T16:04:39Z.

> - [ ] A registered source without a manifest loads the `<name>/SKILL.md` folders in `skills/` and `.skills/`, and `auto_load` can select them by name. - [ ] A `repo_path` that points at the skills folder itself loads the skill folders directly inside it. - [ ] Loose markdown such as a README or docs is never loaded as a skill. - [ ] A source with neither a manifest nor skill folders still fails, and an invalid manifest is still an error. ### Installation Method Source checkout with uv ### SDK Version main at b187e88 ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version 3.13 ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/6009#issuecomment-6193905511)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; COMMENTED; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/6009#pullrequestreview-5550218642): Hello Alona, **Overall: Non-blocking.** No blockers from my side. The fallback is nicely contained. It only runs when the manifest is missing, an invalid manifest still fails, and discovery passes the repository root to `find_skill_md_directories`, so skill folders symlinked outside the repository are skipped. - **Non-blocking (question)** (`registry.py:84`): a single plugin repository registered as a marketplace now loads only its skills and drops its hooks and MCP servers silently. Main failed loudly here. - **Non-blocking (nit)** (`types.py:302`): a duplicate skill name across folders is dropped without a warning. On `647e863`, `tests/sdk/marketplace`, `tests/sdk/plugin`, `tests/sdk/skills`, the conversation plugin tests and the agent server skills and plugins tests pass (922). A plain repository with 430 skills auto-loads in about 0.1
- [reviews; APPROVED; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/6009#pullrequestreview-5550276736): Thank you! 🙏
- [review_comments; comment; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/6009#discussion_r4296534499): Hello Alona, **Non-blocking (question)** A repository that is a single plugin rather than a marketplace (for example `.plugin/plugin.json` with `skills/`, `hooks/` and `.mcp.json`) has no marketplace manifest either, so it now lands here. Its skills load as standalone skills, and its hooks, MCP servers and commands are dropped without a warning. On main the registration failed with "Marketplace manifest not found", so the mistake was at least visible. I checked this with a scratch repository: main loads nothing and logs the failure, while this branch loads its skill and only logs the "has no manifest" info line. Is that the behavior you want? If not, what do you think about keeping the error when the source has a `plugin.json` in one of the `PLUGIN_MANIFEST_DIRS`, or at least logging a warning that its hooks and MCP servers are ignored? H
- [review_comments; comment; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/6009#discussion_r4296534504): Hello Alona, **Non-blocking (nit)** When two folders declare the same skill name (say `skills/a` and `.skills/b`, both with `name: shared`), `setdefault` keeps the first and drops the second without a log line. A `logger.warning` naming both folders would make that easier to spot. Fine to skip if you'd rather keep it quiet. Thank you very much :pray:
- [review_comments; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6009#discussion_r4297131911): Thanks. Keeping the skills loading, but agreed it shouldn't be silent: #6031 logs a warning when the source has a `plugin.json`, saying only its skills are loaded and its hooks, MCP servers and commands are ignored.
- [review_comments; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6009#discussion_r4297132099): Added in #6031: a warning that names both folders when two skills share a name. The first one is still kept.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6009#issuecomment-6193896004): @openhands /codereview-roasted Review exactly commit 647e863a42cbb503b64068507f43184db0e7c260. If the PR head changes, report that and do not approve another commit. Review only: do not push code, merge, deploy, or change attribution. Check correctness, regression coverage, and whether the PR validation claims match the evidence. Keep code comments short. Post the substantive review and link it in your completion message. End the substantive review with these plain lines (no code fence), choosing one verdict: REVIEWED_HEAD: 647e863a42cbb503b64068507f43184db0e7c260 REVIEW_REQUEST: c3284f750d794df4b72038dc22e9c24f VERDICT: APPROVED Use VERDICT: CHANGES_REQUESTED instead when findings require changes. If unable to review, explain the blocker without an approval verdict.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6009#issuecomment-6194217377): ## Code Review: load skills from registered repositories without a manifest **Reviewed commit:** `647e863a42cbb503b64068507f43184db0e7c260` (PR head unchanged) **Base:** `main` @ `fd387b58dc7b707b73c159845c0396a17000be24` ### Scope verified 5 files match the PR manifest: `marketplace/registry.py` (+21), `marketplace/types.py` (+55, new `Marketplace.from_skills_dir`), and 3 test files. No REST/OpenAPI, persisted-settings, public-Python-API, or serialization surface changed. ### Correctness The fix is a single additive fallback: on a missing manifest, `_load_marketplace` reads the folder as plain Agent Skills directories. I traced the full data flow end-to-end: - Both consumers route through the same path: agent-server `load_registered_marketplace_skills` and `LocalConversation._ensure_plugins_loaded` -> `MarketplaceRegistry.get_marketplace
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6009#issuecomment-6194301153): @openhands /codereview-roasted Review exactly commit 647e863a42cbb503b64068507f43184db0e7c260. If the PR head changes, report that and do not approve another commit. Review only: do not push code, merge, deploy, or change attribution. Check correctness, regression coverage, and whether the PR validation claims match the evidence. Keep code comments short. Post the substantive review and link it in your completion message. End the substantive review with these plain lines (no code fence), choosing one verdict: REVIEWED_HEAD: 647e863a42cbb503b64068507f43184db0e7c260 REVIEW_REQUEST: a0cb0409946244658ca7ab7f1c7faa44 VERDICT: APPROVED Use VERDICT: CHANGES_REQUESTED instead when findings require changes. If unable to review, explain the blocker without an approval verdict.
- [discussion; comment; ak684](https://github.com/OpenHands/software-agent-sdk/pull/6009#issuecomment-6195083833): ## Code Review: load skills from registered repositories without a manifest **Reviewed commit:** `647e863a42cbb503b64068507f43184db0e7c260` (PR head unchanged — verified via API) **Base:** merge-base `b187e88e` ### Scope verified 5 files match the PR manifest: `openhands-sdk/openhands/sdk/marketplace/registry.py` (+21, new `_load_marketplace` fallback), `openhands-sdk/openhands/sdk/marketplace/types.py` (+55, new `Marketplace.from_skills_dir`), and 3 test files. No REST/OpenAPI, persisted-settings, public-Python-API, or serialization surface changed. ### Correctness The fix is a single additive fallback: when `Marketplace.load` raises `FileNotFoundError` (no manifest), `_load_marketplace` calls `from_skills_dir` and reads the folder as plain Agent Skills directories; an invalid manifest still raises `ValueError` before the fallback; a sou
- [discussion; comment; openhands-ai[bot]](https://github.com/OpenHands/software-agent-sdk/pull/6009#issuecomment-6195124725): The substantive review has been posted to the PR and the working tree is clean at the exact reviewed commit. ## Summary **Post:** [PR #6009 code review](https://github.com/OpenHands/software-agent-sdk/pull/6009#issuecomment-6195083833) **Verdict:** APPROVED — `REVIEWED_HEAD: 647e863a42cbb503b64068507f43184db0e7c260`, `REVIEW_REQUEST: a0cb0409946244658ca7ab7f1c7faa44` ### What I verified 1. **Review target**: Confirmed via GitHub API that the PR head is exactly `647e863a42cbb503b64068507f43184db0e7c260` (unchanged, still open). The PR's 5-file manifest matches the actual commit diff against its merge-base (`b187e88e`); earlier apparent extra files were just main-only commits ahead of the PR's fork point. 2. **Correctness**: Traced the full data flow through both consumers (agent-server `load_registered_marketplace_skills` and `LocalConvers

### #5955: fix(agent-server): reserve the run slot before a gateway turn saves its message

- Commit: `4a5f5a09096aea331b91481a5ff94dbfed22940e` (2026-10-17T00:03:47+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5955).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5955). Families: F05, F06, F07, F08, F09, F10, F11, F12, F30, F31.

Stated intent:

> - `EventService.reserve_run_slot()` claims run capacity up front (or returns `None` when the conversation already holds its own run permit or still has a run task wrapping up, so turns on a running conversation are not refused). `send_message` takes an optional `run_slot` and passes it to `run()`.
> - `_run_agent` (gateway) reserves before `send_message` and releases its handle afterwards.
> - Map: `F30.chat-reuse-run-limit-429` now `Requires: F30.chat-reuse-header` (it searches for that bullet's `QA_F30_SECOND` turn, so `--only` failed on a missing prerequisite), and its known-bug marker, prose and Gotchas line are updated.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F30-openai-gateway.md`
- `openhands-agent-server/openhands/agent_server/event_service.py`
- `openhands-agent-server/openhands/agent_server/openai/service.py`
- `tests/agent_server/test_run_admission.py`

Linked issue acceptance context:

- [#5934](https://github.com/OpenHands/software-agent-sdk/issues/5934): [Bug]: A 429 (run limit) on a continued OpenAI-gateway conversation still saves the user message, so each retry appends a duplicate — current state closed, closed 2026-10-16T22:03:49Z.

> - [ ] With all run slots taken, `POST /v1/chat/completions` on a continued conversation answers 429 without appending the user message, with detail `Conversation run limit reached. Retry the request later.` - [ ] When the conversation is already running (it holds its own run permit), a gateway turn on it is still accepted as before (not a spurious 429). - [ ] The native `POST /api/conversations/{id}/events` contract (message saved, then 429 with a `/run` retry hint) is unchanged. - [ ] A unit test covers the gateway refusal leaving the conversation's events unchanged. - [ ] The map bullet `F30.chat-reuse-run-limit-429` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `_run_agent` (`openhands-agent-server/openhands/agent_server/openai/service.py:534`) calls `event_service.send_message(..., run=True)` on the continued path (line 552). - `EventService.send_message` (`openhands-agent-server/openhands/agent_server/event_service.py:842`) saves the message first (line 853) and only then calls `run()`, which acquires a run slot and raises `ConversationRunLimitExceeded`; `send_message` rewraps it with the "Message saved ... Retry POST /api/conversations/{id}/run" detail (line 908). That contract fits the native events endpoint, not the gateway. - New conversations reserve a slot before anything is created (`ConversationService.start_conversation`, `RunSlot.acquire`), which is why only the continued path leaks. Suggested fix: on the gateway path, reserve the run slot before s

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5955#issuecomment-6159717650)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5955#pullrequestreview-5512726609): _This review was posted by an AI agent (OpenHands)._ ## Review of #5955 — reserve the run slot before a gateway turn saves its message **Scope:** In-repo and correctly owned. The change stays inside `openhands-agent-server` (the canonical Agent Server) and reuses the existing `RunSlot` capacity mechanism rather than adding a parallel one, which matches the linked issue's stated non-goals and the ownership guidance in `AGENTS.md` / `.agents/skills/custom-codereview-guide.md`. No product/architecture decision is needed. **What I verified on head `5e4a57f`:** - The diff only adds an up-front reservation on the OpenAI gateway's continued-conversation path. `_run_agent` calls `reserve_run_slot()` before `send_message`, and releases the handle in a `finally`. The native `POST /api/conversations/{id}/events` path (`event_router.py`) and `send_me

### #5842: fix(agent-server): apply bash order__gt filter before paging

- Commit: `304ea67a7993c04c45a4d2caefa5a0ad228e9c61` (2026-10-17T00:04:40+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5842).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5842). Families: F13.

Stated intent:

> - `_search_bash_events_sync` (`bash_service.py`) now drops filtered events while it fills the page, so `limit` counts only events that are returned. `next_page_id` names the next event that passes the filter, so a page is never followed by an empty one.
> - Two unit tests: `order__gt` paging across two pages, and no cursor when every remaining file is filtered out.
> - The `F13.order-gt-paging` map bullet no longer carries its `# bug` marker, and its prose and the paging Gotcha now describe the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`
- `openhands-agent-server/openhands/agent_server/bash_service.py`
- `tests/agent_server/test_bash_service.py`

Linked issue acceptance context:

- [#4388](https://github.com/OpenHands/software-agent-sdk/issues/4388): agent-server: search_bash_events pagination wrong when order__gt filter is active — pages undersized, clients loop on empty next-pages — current state closed, closed 2026-10-16T22:04:42Z.

> - [ ] On current `main`, `GET /api/bash/bash_events/search` with `order__gt` set and `limit=N` returns a page of up to `N` events whose `order` is greater than `order__gt`, rather than a page bounded by the number of files read. - [ ] When a page's items are all removed by `order__gt`, the response returns `items: []` with `next_page_id` null (or otherwise guarantees forward progress), so a client following the cursor cannot loop on empty pages. - [ ] `next_page_id` still resolves to a valid page start on the next request, and the final page of a filtered result set reports `next_page_id` null, so paginating a result set with `order__gt` active terminates and yields every matching event exactly once. - [ ] `RemoteWorkspace.execute_command` (and its async mirror) reassembles output past 100 chunks: a command producing more than 100 `BashOutput` chunks returns the complete output and the real exit code, not `exit_code` -1 with `timeout_occurred` true. - [ ] Existing `search_bash_events` behavior without `order__gt` is unchanged: `kind__eq`, `command_id__eq`, `timestamp__gte`/`timestamp__lt` and `sort_order` still filter, sort, and paginate as before, and `tests/agent_server/test_bash_service.py` continues to pass. - [ ] Regression coverage in `tests/agent_server/test_bash_service.py` asserts the observable page contents: an `order__gt` page of size `limit` returns `limit` filtered events, and an all-filtered request returns no items and no cursor. The added test fails on `main` and passes with the fix. - [ ] The `F13.order-gt-paging` bullet in `.agents/skills/verify-agent-ser

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5842#issuecomment-6157898237)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5842#pullrequestreview-5511870390): _This review was posted by an AI agent (OpenHands)._ ## Review: PR #5842 — apply bash `order__gt` filter before paging **Scope.** This belongs in `OpenHands/software-agent-sdk`: the change fixes `BashEventService._search_bash_events_sync` in the agent server, which owns the `/api/bash/bash_events/search` REST contract. No cross-repository move needed. **What I verified on head `929319f`** - The fix is correct and minimal. The old code sliced `matched[start_index : start_index + limit]` and set the cursor positionally at `matched[start_index + limit]` before applying the `order__gt` filter inside the slice, so a page could hold zero items while `next_page_id` was still set. The new loop iterates `matched[start_index:]`, skips unreadable files and events failing `order__gt`, and only sets `next_page_id = name` once `len(page_events) >= limi
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5842#pullrequestreview-5524169112): Approving this over #4396 for the same issue (#4388). Both take the right filter-before-paging approach (and this PR credits #4396 for it), but this one is stronger on three counts: (1) current-head CI is fully green (51/56 checks passing, rest skipped/neutral) versus #4396's stale 3-check head from Sep 25; (2) it has live before/after evidence against a real agent server (`F13.order-gt-paging`: xfail x2 on main with an empty page + cursor, xpass x2 here, pass after marker removal), where #4396 has unit tests only; (3) the cursor points at the next event that passes the filter, so a full page is never followed by an empty page — #4396 explicitly leaves that extra empty-page round trip. The production change is minimal (~15 lines, no new machinery) and the two new tests fail on main. Closing #4396 in favour of this PR. _This review was cre

### #5780: fix(agent-server): log failed runs without rendering a rich traceback on the loop

- Commit: `60c6f62133e0f649e2e71ec8c8aa51a06a1fb0f0` (2026-10-17T00:05:19+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5780).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5780). Families: F05, F06, F07, F08, F09, F10, F11, F12, F31.

Stated intent:

> - `event_service.py`: log the run failure at ERROR with the plain-text traceback in the message, not via `exc_info`, so no handler renders a rich traceback on the loop.
> - Tests: a new unit test checks that the record is ERROR, has no `exc_info` and carries the whole chain; the existing logging test is updated.
> - Map: `F05.run-error-responsive` loses its known-bug marker; the bullet prose and the Gotchas line now describe the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F05-run-pause-interrupt.md`
- `openhands-agent-server/openhands/agent_server/event_service.py`
- `tests/agent_server/test_event_service.py`

Linked issue acceptance context:

- [#5373](https://github.com/OpenHands/software-agent-sdk/issues/5373): agent-server: a logging call can wedge the event loop and amplify memory (rich traceback render) — current state closed, closed 2026-10-21T13:51:40Z.

> - [ ] With the default server logging configuration (`LOG_RICH_TRACEBACKS` unset or true), logging an exception with a cyclic `__context__`/`__cause__` chain returns in bounded time and bounded memory: a deterministic test constructs such an exception, invokes the logging path, and the call completes rather than hanging, with no unbounded RSS growth. - [ ] The same bound holds for a pathologically deep exception chain and for a chain with repeated/duplicated causes — not only for the cyclic shape. - [ ] The events-websocket error path (`openhands/agent_server/sockets.py`, `events_socket`) never performs an unbounded synchronous traceback/stack render; a failing `websocket.send_json` or client disconnect is still handled as benign (`Event websocket disconnected`) without a rich-rendered `stack_info=True` line. - [ ] The error path still sends the `ServerErrorEvent` to the client when the socket is open; hardening the render must not drop client-visible error reporting. - [ ] While the pathological logging path is exercised, the server keeps serving: `/alive`, `/server_info`, and the conversation REST endpoints respond within their normal latency, and no wedge occurs (an automated test in the repository's existing server/stress structure). - [ ] With the pathological path exercised, the process terminates on SIGTERM within the configured stop timeout instead of requiring SIGKILL. - [ ] Existing logging behavior is preserved: ordinary (non-pathological) exceptions still render rich tracebacks when `LOG_RICH_TRACEBACKS=true`; `LOG_RICH_TRACEBACKS=false` and the JSON/CI console 

- [#5633](https://github.com/OpenHands/software-agent-sdk/issues/5633): [Bug]: Failed run blocks the event loop while logging a rich traceback: /alive takes ~5 s, not ~100 ms — current state closed, closed 2026-10-16T22:05:21Z.

> - [ ] With default console logging, no `/alive` probe takes over 1.5 s while a run fails, and socket error frames are not delayed. - [ ] The run failure is still logged with enough detail to diagnose it. - [ ] A regression test fails a run on a live server and bounds the latency of a concurrent request. - [ ] The map bullet `F05.run-error-responsive` passes; its known-bug marker is removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context - `openhands-agent-server/openhands/agent_server/event_service.py:1546`: `logger.exception("Error during conversation run")` in `_run_and_publish`, a task on the server loop. Here the exception is a `ConversationRunError` chained through litellm, openai, httpx and httpcore errors. - `openhands-sdk/openhands/sdk/logger/logger.py:45` defaults `LOG_RICH_TRACEBACKS` to true; lines 134-137 install `RichHandler(rich_tracebacks=...)` as the root console handler unless `LOG_JSON` or CI is set. `RichHandler.emit` renders the whole chain with source context synchronously in the caller, here the loop. - `LocalConversation.arun` has already stored the `ConversationErrorEvent` and set `ERROR` before raising (`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:2582-2608`), so only the log line and everything else on the loop are delayed. Suggested fix: keep console rendering off the loop, either with a `QueueHandler`/`QueueListener` around the console handler in `setup_logging` (covers every `logger.exception` on the loop) or, here, a one-line error (the event already has `code` an

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5780#issuecomment-6156831024)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5780#pullrequestreview-5524170122): Approving this over #5709 for the same issue (#5633). Both stop the Rich traceback render from stalling the event loop. I prefer this PR's logging trade-off: the full traceback stays at ERROR level as plain text in the message, so production logs (INFO / LOG_JSON) keep the diagnostic detail, whereas #5709 demotes the traceback to DEBUG where production deployments would lose it. Evidence is also the strongest of the pair: live `/alive` probes of 4540/4791 ms on main versus 142/93 ms here (repeat x2, xfail -> xpass -> pass), with the new tests failing on main. The change itself is ~13 production lines using only the stdlib, with no new machinery, and current-head CI is fully green; #5709 has a failing PR-description check. Closing #5709 in favour of this PR. _This review was created by an AI agent (OpenHands) on behalf of Graham Neubig._

### #5779: fix(sdk): install the posted LLM when switch_llm targets a registered usage_id

- Commit: `a6c7bf5591f64ca62c653565d7498d72e155e789` (2026-10-17T00:08:24+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5779).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5779). Families: F05, F08, F09, F10.

Stated intent:

> - `LocalConversation.switch_llm` reuses a registered LLM only when its config (`model_dump(mode="json")` with secrets exposed) equals the posted one; otherwise it replaces the registry entry with the posted LLM and hands it the metrics object already recorded under that `usage_id`, so `stats.usage_to_metrics[usage_id]` keeps its accrued usage. Identical re-switches (`switch_profile`, `classify_and_switch_llm`'s `profile:<name>`) still reuse the cached object.
> - The route, its `200 {"success": true}` contract and `clients/typescript` are unchanged, as the triage scoped.
> - Regression tests: `tests/sdk/conversation/test_switch_model.py` (initialized conversation switched with an omitted `usage_id`, metrics carried over; identical config reused) and `tests/agent_server/test_switch_llm_survives_reload.py` (real `ConversationService`: `default` registered, switched model read back from the live conversation, `base_state.json` and `get_conversation`). The `F09.switch-llm-registered-usage-id` known-bug marker is removed and its recipe now requires the install.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`
- `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`
- `tests/agent_server/test_switch_llm_survives_reload.py`
- `tests/sdk/conversation/test_switch_model.py`

Linked issue acceptance context:

- [#5636](https://github.com/OpenHands/software-agent-sdk/issues/5636): [Bug]: switch_llm returns 200 but keeps the old LLM when the posted usage_id is already registered — current state closed, closed 2026-10-16T22:08:25Z.

> - [ ] On an initialized conversation, `switch_llm` with a registered `usage_id` and a different config installs the posted LLM or returns a 4xx, never 200 with the old LLM kept. - [ ] Re-switching to an identical config under a registered `usage_id` (e.g. `classify_and_switch_llm`'s `profile:<name>`) still works, and usage stays in `stats.usage_to_metrics`. - [ ] A regression test covers an initialized conversation switched with `usage_id: "default"`. - [ ] The `F09.switch-llm-registered-usage-id` map bullet passes and its known-bug marker is removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context - `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:1658-1662`: `switch_llm` calls `self.llm_registry.get(llm.usage_id)` and registers the posted LLM only on `KeyError`; on a hit the posted config is dropped (docstring 1650-1653: "first-write-wins"). - `local_conversation.py:1586-1596`: agent init registers the agent's LLMs, so `default` is taken after the first message. - `openhands-sdk/openhands/sdk/llm/llm.py:626-627`: `usage_id` defaults to `"default"`. - `openhands-agent-server/openhands/agent_server/conversation_router.py:616-650`: the route calls `conversation.switch_llm(llm)` and returns `Success()` unconditionally; its docstring offers it for forwarding profile configs. Suggested fix: in `switch_llm`, reuse the registered LLM only when its config equals the posted one (as `_condenser_for_switched_llm` compares `model_dump(mode="json", context={"expose_secrets": True})`); otherwise replace the ent

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5779#issuecomment-6156816074)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5779#pullrequestreview-5511218922): _This review was posted by an AI agent (OpenHands)._ ## Review of #5779 Scope check (per root `AGENTS.md` and `.agents/skills/custom-codereview-guide.md`): the change is entirely within this repository's ownership - `LocalConversation.switch_llm` behavior in the SDK plus its tests and the verify-agent-server feature map. No cross-repository product decision needed. ### What I verified on head `8a36139` - **Root-cause fix is correct.** `switch_llm` now compares the registered and posted LLM by full config (`model_dump(mode="json", context={"expose_secrets": True})`) and only reuses the cached object when they match, otherwise replacing the registry entry. I confirmed the `expose_secrets` context actually propagates to the secret field serializer, so a same-model/different-key config is correctly treated as different (`equal exposed: False`
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5779#pullrequestreview-5524169569): Approving this over #5699 for the same issue (#5636). Both correctly reuse a registered LLM only on config match, replace it otherwise, and preserve accrued metrics. This one wins on verification and integration: (1) current-head CI fully green (51/56) versus #5699's failing PR-description check and 5-check head; (2) live map evidence (`F09.switch-llm-registered-usage-id`: xfail x2 on main, xpass x2 here, pass after marker removal) plus a dedicated real-`ConversationService` reload test (`test_switch_llm_survives_reload.py`) and 2,515 passing tests in the affected areas; (3) metrics are carried over from the registered LLM object itself, keeping a single metrics object per usage_id. Closing #5699 in favour of this PR (its live curl reproduction was good work and corroborates the fix). _This review was created by an AI agent (OpenHands) on

### #5772: fix(agent-server): mark a goal loop left active at restart as interrupted

- Commit: `8ba8ccbeb1a5306835f44f5f0f8ab20516373ee9` (2026-10-17T00:14:07+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5772).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5772). Families: F05, F06, F07, F08, F09, F10, F11, F12, F31.

Stated intent:

> - `EventService.start()` now reconciles a stale goal status next to the existing stale-`RUNNING` execution-status reconcile: if the last persisted `goal` status is still `active`, it appends one `active: false, status: "interrupted"` status with the same `objective`, `iteration` and `max_iterations` (verdict cleared, like every other `interrupted` status). It is driven by the loaded state, so it covers graceful restarts and hard kills alike, and the goal stays resumable with `/goal/resume`. Terminal or already-inactive statuses are left alone. The check is best effort (an unreadable log logs a warning and does not block loading) and runs off the event loop.
> - `_last_goal_loop_status()` walks the log backward by index and stops at the first goal status, instead of materialising the whole event list.
> - `F11.goal-restart-active` in the verify-agent-server feature map drops its known-bug marker, and the prose and Gotchas that described the bug now describe the behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F11-goals.md`
- `openhands-agent-server/openhands/agent_server/event_service.py`
- `tests/agent_server/test_goal_loop.py`

Linked issue acceptance context:

- [#5627](https://github.com/OpenHands/software-agent-sdk/issues/5627): [Bug]: /goal loop active at a graceful restart stays `running` afterwards instead of `interrupted` — current state closed, closed 2026-10-16T22:14:09Z.

> - [ ] After a graceful restart with an active loop, the last goal status is `active: false, status: "interrupted"` with the objective and counts unchanged, and `/goal/resume` continues it. - [ ] A conversation loaded with a last goal status of `active: true` (e.g. after a hard kill, where `close()` never runs) is also reported `interrupted`, covered by a regression test. - [ ] The `F11.goal-restart-active` map bullet passes (remove its known-bug marker). ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/event_service.py`: - `EventService.close()` (:2061) sets `self._closing = True` (:2064) and then cancels `_goal_loop_task` (:2076-2079). - The `except asyncio.CancelledError` handler in `_run_goal_loop` (:1835-1842) emits `interrupted` only `if not self._closing` (:1839), so the shutdown cancellation records nothing. - `EventService.start()` reconciles a stale `RUNNING` execution status on load (:1328-1333), but nothing reconciles a stale `running` goal status. - `stop_goal_loop()` (:1858-1871) returns `False` when there is no task and records nothing, and the `/goal/stop` route (`conversation_router.py:477-494`) returns 200 either way. `resume_goal_loop()` (:1905) accepts the stale status because it is neither `complete` nor `capped`, which is the only way out. Suggested fix: in `start()`, next to the stale-`RUNNING` reconcile, read `_last_goal_loop_status()` (:1873) and, if it is `active: true`, append a `goal` `ConversationStateUpdateEvent` with the same pa

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5772#issuecomment-6156386934)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5772#pullrequestreview-5511025928): _This review was posted by an AI agent (OpenHands)._ ## Summary This change is in-scope for this repository: the agent-server owns conversation lifecycle and the `ConversationStateUpdateEvent` stream, and the fix lives in `EventService.start()` alongside the existing stale-`RUNNING` execution-status reconcile. The bug is real and the fix addresses it. On load, `EventService.start()` now calls `_reconcile_stale_goal_status()`, which reads the last persisted `goal` status and, if it is still `active`, appends one `active: false, status: "interrupted"` status carrying the same `objective`, `iteration`, and `max_iterations` with `verdict` cleared — matching the shape of every other `interrupted` status emitted by the goal loop. Because it is driven from loaded state, it covers graceful restarts and hard kills alike, and it leaves terminal/alr
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5772#pullrequestreview-5524169343): Approving this over #5719 for the same issue (#5627). The core reconcile-in-`start()` approach is identical, but this PR is the more complete package: (1) current-head CI fully green (51/56, rest skipped/neutral) versus #5719's failing PR-description check and thin 4-check head; (2) live map evidence (`F11.goal-restart-active`: xfail x2 on main, xpass x2 here, pass after marker removal) plus graceful-restart, hard-kill, and resume regression tests that fail on main; (3) it validates the persisted status through `GoalStatus`, clears `verdict` like every other interrupted status, is best-effort so an unreadable log cannot block conversation loading, improves `_last_goal_loop_status` to a lazy backward scan, and removes the now-fixed known-bug marker from the feature map. Closing #5719 in favour of this PR. _This review was created by an AI 
- [review_comments; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5772#discussion_r4299828915): Good agent. I used to hunt these down with a microscope manually 😅

### #5769: fix(typescript): use the global WebSocket in the callback socket clients

- Commit: `c87fddb623f0b7a43f697fa6bb3006e10203cd4c` (2026-10-17T00:21:57+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5769).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5769). Families: F13, F34.

Stated intent:

> - New internal helper `src/events/global-websocket.ts`: it returns `globalThis.WebSocket` when called and holds the shared error message. Both clients now call it when they connect. All module-load detection and `require('ws')` code is gone, and the built `dist/` has no `require` or `ws` import.
> - Tests: the callback-client test stubs `globalThis.WebSocket` instead of a fake `window`. New tests cover a constructor installed after import and the bash client on the global constructor. The missing-implementation tests now remove the global.
> - The verify-agent-server map: `F34.ws-callback-client` and `F34.bash-websocket` lose their `# bug` markers and "known bug" labels. Their prose, the WebSockets overview and the Node gotcha now describe the fixed behaviour. The README notes the Node 22 requirement.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `clients/typescript/README.md`
- `clients/typescript/src/__tests__/package-import.test.ts`
- `clients/typescript/src/__tests__/websocket-callback-client.test.ts`
- `clients/typescript/src/events/bash-websocket-client.ts`
- `clients/typescript/src/events/global-websocket.ts`
- `clients/typescript/src/events/websocket-client.ts`

Linked issue acceptance context:

- [#4731](https://github.com/OpenHands/software-agent-sdk/issues/4731): Fix WebSocket client require('ws') breaking bundlers — current state closed, closed 2026-10-16T22:22:00Z.

> ## Problem `src/events/websocket-client.ts` uses `require('ws')` in library code: ```typescript if (typeof window !== 'undefined' && window.WebSocket) { WebSocketImpl = window.WebSocket; } else { const ws = require('ws'); WebSocketImpl = ws; } ``` Problems: 1. `require()` breaks ES module bundlers (Webpack, Rollup, Vite) — they statically analyze imports 2. The detection runs at **module load time**, so the `require('ws')` executes immediately in Node.js even if the WebSocket client is never used 3. Contradicts the browser-compatibility require

- [#4846](https://github.com/OpenHands/software-agent-sdk/issues/4846): [Bug]: WebSocket clients ignore Node's global WebSocket in ESM builds — current state closed, closed 2026-10-16T22:21:59Z.

> - [ ] The TypeScript WebSocket callback clients use globalThis.WebSocket in Node ESM when available. - [ ] The fallback path still supports injected WebSocket implementations and browser window.WebSocket. - [ ] A TypeScript client test covers Node's global WebSocket constructor path. ### Installation Method uv sync --dev; npm ci in clients/typescript ### If you selected "Other", please specify _No response_ ### SDK Version Current main branch ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version _No response_ ### Model Name (if applicable) _No response_ ### Operating System Linux ### Logs and Error Messages WebSocket implementation not available ### Minimal Code Sample ```bash node --input-type=module -e "const {WebSocketCallbackClient}=await import('./dist/events/websocket-client.js'); const client=new WebSocketCallbackClient({host:'http://127.0.0.1:9',conversationId:'test',callback:()=>{},onError:console.error}); client.start(); client.stop()" ``` ### Screenshots and Additional Context this is the issue backing https://github.com/OpenHands/software-agent-sdk/pull/4799

- [#5651](https://github.com/OpenHands/software-agent-sdk/issues/5651): [Bug]: ConversationEventStream on Node 22 WebSocket stops after one refused reconnect instead of backing off — current state open, closed —.

> - [ ] On Node 22's global `WebSocket`, the stream keeps backing off after a refused attempt and reconnects once the server is back, also when the server is down at `start()`. - [ ] A handshake that never settles counts as a failed attempt after 10 s, with no double scheduling when the implementation also fires `close`. - [ ] A unit test with a fake `WebSocket` that fires `error` but never `close` covers both paths. - [ ] The `F34.event-stream-reconnect-native` map bullet passes with its known-bug marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context In `clients/typescript/src/client/conversation-event-stream.ts`: - `scheduleReconnect()` (176-195) is called only from `onclose` (145-165, call at 164) and from the `catch` for synchronous throws in `connect()` (166-173). - `onerror` (140-144) never reschedules. - The 10 s handshake abort (126-128) only calls `socket.close()`. On undici, closing a socket that already failed fires a second `error`, sets `readyState` to 2 and still fires no `close`, so the abort does not recover either. Suggested fix: send every terminal outcome of an attempt through one idempotent handler that returns unless `this.socket === socket`, clears the handshake timer, sets `this.socket = null` and calls `scheduleReconnect()`. Call it from `onclose`, from `onerror` while the attempt has not opened, and from the handshake timer; a later `close` from a compliant implementation then finds `this.socket !== socket` and schedules nothing. Until then, Node consumers can pass the `ws` constructor

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5769#issuecomment-6155874165)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5769#pullrequestreview-5510851704): _This review was posted by an AI agent (OpenHands)._ ## Scope This belongs in this repository: it fixes `clients/typescript/`, the browser-compatible TypeScript client this repo owns, and needs no cross-repo move. ## What I verified on head `891df04d` - The fix is correct. Both `WebSocketCallbackClient` and `BashWebSocketClient` now resolve `globalThis.WebSocket` at connect time through the new `src/events/global-websocket.ts`, and all module-load `require('ws')` / `window.WebSocket` detection is gone. The built `dist/events/` contains no `require` or `ws` reference, so the bundler and ESM-`require` problems in #4731 and #4846 are addressed. - Authentic before/after evidence (Node 24 ESM against a real `ws` server, built `dist/`): - base `dfd8fe8`: issue #4846 repro -> `ONERROR: WebSocket implementation not available. Install the ws packa
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5769#pullrequestreview-5524170717): Approving this over #4799 for the same issues (#4846, #4731). Both read the global WebSocket instead of `require('ws')`, but this PR resolves `globalThis.WebSocket` at connect time through one shared `global-websocket.ts` helper, so a constructor installed after import (e.g. `ws` assigned on an older Node) is picked up and the duplicated detection blocks in both clients are deleted; #4799 captures the global once at module load in each client. This PR also keeps `ws` in dependencies so the documented `createWebSocket` workaround for #5651 keeps working. Verification is stronger and current: live map proof for both clients (xfail x2 on main -> xpass x2 here -> pass), the issue's own Node 22 repro re-run against `dist/`, 385 TypeScript tests passing, and current-head CI fully green (52/58). Closing #4799 in favour of this PR. (Not closing #

### #5768: fix(agent-server): bound profile pre-flight and reject exhausted quotas

- Commit: `76055f697a5e9295252e0d54ab4ea822946e5c54` (2026-10-17T00:23:32+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5768).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5768). Families: F20.

Stated intent:

> - The ping uses a copy of the LLM with `num_retries: 0`, no `fallback_strategy`, and `timeout` = min(15 s, posted timeout). The posted config itself is not changed.
> - A rate-limit error that the SDK's existing `is_quota_exhaustion_error` classifies as an exhausted budget/quota answers `valid: false` with the redacted provider message; a plain 429 is still `valid: true`.
> - verify-agent-server map: `F20.validate-unreachable-bounded` and `F20.validate-hang-bounded` lose their `# bug` markers and "known bug" labels; the prose describes the bounded behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F20-llm-profiles.md`
- `openhands-agent-server/openhands/agent_server/profiles_router.py`
- `tests/agent_server/test_profiles_router.py`

Linked issue acceptance context:

- [#5100](https://github.com/OpenHands/software-agent-sdk/issues/5100): [Bug]: Profile pre-flight validation reports budget-exceeded keys as valid and blocks ~2 min on retries — current state closed, closed 2026-10-16T22:23:35Z.

> - [ ] A 429 whose body indicates exhausted budget/quota (e.g. `budget_exceeded`, `insufficient_quota`) makes `validate_profile` return `valid=false` with a redacted error message. - [ ] A plain, recoverable rate limit is still non-blocking (`valid=true`). - [ ] The pre-flight call does not use the profile's runtime retry policy; the repro above returns in a few seconds with at most 2 LLM calls. - [ ] Unit tests in `tests/agent_server/` cover the budget-exceeded 429, a recoverable 429, and the no-long-retry behaviour. ### Installation Method pip / uv (`openhands-agent-server` via uvx, launched by Agent Canvas) ### SDK Version 1.48.0 (also verified against `main`) ### Version Confirmation - [x] I have confirmed this bug exists on the LATEST version of OpenHands SDK ### Python Version 3.13 ### Model Name (if applicable) openhands/gpt-5.5 via LiteLLM proxy ### Operating System macOS

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5768#issuecomment-6155830844)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5768#pullrequestreview-5510676729): _This review was posted by an AI agent (OpenHands)._ Reviewed the current head `87799be1a4ff1dc2b24b6a88000ea72a452dbd64` (title/body/labels, review threads, check runs, and linked issues #5100/#5653/#5155 all re-read now). No prior review by this account on this head; no comments after it. **Scope.** The change lives in this repository's own Agent Server route (`openhands-agent-server/.../profiles_router.py`) plus its tests and the `verify-agent-server` feature-map prose. It belongs here; no cross-repo move is warranted. Issue #5100 is open with `ready-for-dev`. **Findings.** No material correctness, security, compatibility, or acceptance-criterion defects. - **Bounded pre-flight.** `_preflight_llm()` copies the posted LLM with `num_retries=0`, `timeout=min(llm.timeout, 15)`, and `fallback_strategy=None`. With `num_retries=0` tenacity's 
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5768#pullrequestreview-5524170404): Approving this over #5155 for the same issue (#5100). #5155 is still a draft with the HUMAN template placeholder unfilled and adds `.pr/` working files, so it is not in a reviewable state; this PR is complete. On substance both bound the pre-flight and reject exhausted quotas via the existing `is_quota_exhaustion_error`, but this PR additionally has: (1) current-head CI fully green (51/56) and pre-commit clean; (2) live map evidence for both the unreachable-endpoint case (valid:false in 0.3 s) and the hanging-provider case (bounded at 15.1 s with exactly one request at the stub), each xfail x2 on main -> xpass x2 here -> pass; (3) a cleaner diff (+217/-42, 3 files) that extracts a shared `_invalid()` helper instead of duplicating error handling, drops fallbacks during pre-flight so a working fallback cannot mask a broken primary, and does

### #5778: fix(agent-server): return the whole output from execute_bash_command

- Commit: `c73ec79e3fd1dfc1e5d6326fca4e0c14fa10029f` (2026-10-17T00:25:28+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5778).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5778). Families: F13, F14, F15, F16, F34.

Stated intent:

> - `bash_router.py`: the route reads every page of the command's `BashOutput` events (following `next_page_id`), sorts by `order`, joins stdout and stderr separately, and returns the final chunk with the joined text and its exit code. The response is still one `BashOutput`; small outputs are unchanged.
> - `clients/typescript`: `RemoteWorkspace.executeCommand` maps a missing exit code to `-1` instead of `0`.
> - verify-agent-server map: `F13.execute-large-output` drops its known-bug marker; the F13 and F34 prose and gotchas now describe the joined output.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`
- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `clients/typescript/src/__tests__/remote-workspace-execute.test.ts`
- `clients/typescript/src/workspace/remote-workspace.ts`
- `openhands-agent-server/openhands/agent_server/bash_router.py`
- `tests/agent_server/test_bash_router.py`

Linked issue acceptance context:

- [#5638](https://github.com/OpenHands/software-agent-sdk/issues/5638): [Bug]: execute_bash_command returns only the last chunk of output over 1 MiB; null exit_code past 100 chunks — current state closed, closed 2026-10-16T22:25:30Z.

> - [ ] `execute_bash_command` returns the complete stdout and stderr of a multi-chunk command, joined in `order`, with the final chunk's exit code. - [ ] With more than 100 chunks, the response carries the real exit code (7 for `exit 7`). The TS `RemoteWorkspace.executeCommand` no longer maps a missing exit code to 0. - [ ] A regression test covers multi-chunk output through the route. - [ ] The `F13.execute-large-output` bullet in the verify-agent-server feature map passes, with its known-bug marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context - `openhands-agent-server/openhands/agent_server/bash_router.py:122-123` returns `cast(BashOutput, page.items[-1])` from `search_bash_events(command_id__eq=command.id)`. That search uses the default `limit=100` (`bash_service.py:150`), and the route ignores `next_page_id`. - `bash_service.py:25` sets `MAX_CONTENT_CHAR_LENGTH = 1024 * 1024`. `read_stream` (`bash_service.py:336-351`) stores each full 1 MiB slice as its own `BashOutput` with no exit code. Only the final event (`bash_service.py:407-414`) carries `exit_code`. Small outputs work because their only event is the final one. - `clients/typescript/src/workspace/remote-workspace.ts:133` sets `exit_code: bashOutput.exit_code ?? 0`. - The Python SDK's `RemoteWorkspace.execute_command` does not use this route. It polls search and joins every chunk. Suggested fix: in `execute_bash_command`, page through the command's `BashOutput` events by following `next_page_id`. Sort them by `order`, concatenate `stdout` and `std

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5778#issuecomment-6156787589)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5778#pullrequestreview-5511327800): _This review was posted by an AI agent (OpenHands)._ ## Review: fix(agent-server): return the whole output from execute_bash_command Verified against head `e58ea59dee1afb53847c887bf1e5a520765d326d`. **What I checked** - Scope: the fix lives in the agent-server REST route and mirrors into `clients/typescript/`, matching the ownership flow in `AGENTS.md` (Agent Server -> OpenAPI -> TS client). It belongs in this repository; no cross-repo move needed. - `bash_router.py`: `_collect_bash_output` now follows `next_page_id` across every page, sorts the command's `BashOutput` events by `order`, joins `stdout`/`stderr` separately, and returns the final chunk's `exit_code`. Pagination is non-overlapping (`next_page_id` is the first item of the next page), so no chunk is dropped or duplicated. The response is still a single `BashOutput`, and small/e
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5778#pullrequestreview-5524169829): Approving this over #5697 for the same issue (#5638). Both page through all chunks, join in order, and map a missing TypeScript exit code to -1 instead of 0. This PR is stronger overall: (1) current-head CI fully green (51/56) versus #5697's failing PR-description check and 5-check head; (2) live map evidence (`F13.execute-large-output`: xfail x2 on main, xpass x2 here, pass after marker removal) in addition to route tests through a real `BashEventService` covering >100 chunks and exit code 7; (3) an empty result returns a `BashOutput` with exit_code -1 rather than raising a 500, matching the Python SDK's existing -1 convention. Closing #5697 in favour of this PR. _This review was created by an AI agent (OpenHands) on behalf of Graham Neubig._

### #6036: test: guard Linux-only Canvas backend cases on macOS

- Commit: `bf9b6057b00e1b2b532ecff508c0d92d818adced` (2026-10-16T18:35:29-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/6036).
- Intent: documented. Runtime: **no-product-runtime-needed**; [feature evidence and limits](intent-runtime-overlay.md#pr-6036). Families: no product runtime required.

Stated intent:

> - Skip only tests that require a supported Linux backend artifact.
> - Add a non-Linux regression test covering `unsupported` status, prepare, and start without extracting an artifact.

Changed paths:

- `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/6036#issuecomment-6223595991)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/6036#pullrequestreview-5553750739): _This review was posted by an AI agent (OpenHands)._ ## Scope Test-only change to `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`, which this repository owns (agent-server test suite). It belongs here; no cross-repository move or product decision is needed. ## What the change does Adds a `requires_linux_backend` skipif marker keyed on `CanvasExtensionBackendManager.current_platform() is None`, and applies it to the ten tests whose fixtures build `backend-linux-amd64`/`arm64` artifacts and execute them via a Linux script shebang and `/proc`-based liveness checks. A new `test_backend_is_unsupported_on_non_linux_platform` asserts the `unsupported` status, `prepare`, and `start` paths on non-Linux hosts, and that no artifact is extracted. I checked every test in the module against its fixtures: all tests that need a re

### #5951: fix(agent-server): let OH_*_IS_NONE=1 clear a config file value

- Commit: `6235a41b5b847296e9f8bece1479c060e9a14a5f` (2026-10-17T01:56:40+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5951).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5951). Families: F01, F02, F03, F29, F31, F32.

Stated intent:

> - `env_parser.merge` gains a keyword-only `none_overrides` flag (default `False`, so merging inside the environment parser is unchanged); with it set, an explicit `None` in the later value replaces the earlier one, while `MISSING` still keeps it.
> - `load_config` merges the environment over the file with `none_overrides=True`. Fields with no `OH_*` variable are absent from the env data, so file values are untouched.
> - Map bullet `F31.config-null-override`: `# bug` marker and "known bug" label removed, prose and the idle-TTL Gotcha/Configuration lines rewritten to the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`
- `openhands-agent-server/openhands/agent_server/config.py`
- `openhands-agent-server/openhands/agent_server/env_parser.py`
- `tests/agent_server/test_config.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5935](https://github.com/OpenHands/software-agent-sdk/issues/5935): [Bug]: OH_{FIELD}_IS_NONE=1 cannot null a value set in the agent-server config file — current state closed, closed 2026-10-16T23:56:42Z.

> - [ ] `OH_<FIELD>_IS_NONE=1` sets a nullable field to `None` even when the config file sets it (an environment null overrides a file value, like any other `OH_*` value). - [ ] Without any `OH_*` variable for a field, the file value is kept; a non-null `OH_<FIELD>` still replaces it. - [ ] Merging inside the environment parser (e.g. a union of `str \| None` parsers) is unchanged. - [ ] A unit test in `tests/agent_server/test_config.py` covers the file-value-plus-`_IS_NONE` case. - [ ] The map bullet `F31.config-null-override` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `load_config` (`openhands-agent-server/openhands/agent_server/config.py:564-585`) combines the two sources with `merge(file_data, env_data)` (line 581). - `env_parser.merge` (`openhands-agent-server/openhands/agent_server/env_parser.py:360-383`) returns the earlier value whenever the later one is `None` ("Favor present values over missing ones", lines 380-381). That rule suits merging the outputs of several environment parsers, but in `load_config` it drops the explicit `None` that `NoneEnvParser.from_env` (line 81) returns for `_IS_NONE=1`. Fields with no environment variable are absent from `env_data` (`MISSING`), so they are not affected. The F31 Gotchas also note the consequence for `OH_CONVERSATION_IDLE_TTL_SECONDS_IS_NONE=1`, which disables idle eviction only when the file does not set the TTL. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5951#issuecomment-6159557508)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5951#pullrequestreview-5512677813): _This review was posted by an AI agent (OpenHands)._ ## Verdict: approved Scope check: the change lives in `openhands-agent-server` (config loading + env parser), which this repository owns, and it fixes the linked bug #5935 (`ready-for-dev`, `priority:low`). No cross-repository or product-direction decision is needed. ## What I verified - **Head matches** `5353a33b858a08edeff53cfdbca5afe02ad43d84`; PR is open, non-draft, no labels, `all-hands-bot` requested. - **CI for this head is fully green**: `agent-server-tests`, `cross-tests`, `pre-commit`, `REST API (OpenAPI)`, `Persisted settings`, `windows-tests`, `integration-test`, `smoke-test`, and the rest all pass. - **Focused tests pass**: `uv run pytest tests/agent_server/test_config.py tests/agent_server/test_env_parser.py tests/agent_server/test_cors.py tests/agent_server/test_init_rout

### #5840: fix(agent-server): answer 422 for a malformed id on scoped bash routes

- Commit: `8869bcd914af1ef3fb588eeeab7c4b502b83a93e` (2026-10-17T02:06:05+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5840).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5840). Families: F02, F03, F06, F13, F16, F30.

Stated intent:

> - `get_bash_event_service`: when the runtime state is missing on a scoped request, raise the path parameter's own validation error (`RequestValidationError`, loc `["path", "runtime_conversation_id"]`), so the response is the same 422 body FastAPI returns for the other scoped routes. A valid id is unchanged; the global `/api/bash/...` routes are unchanged.
> - Unit test covering all 7 scoped bash routes with `not-a-uuid`.
> - Map bullet `F13.scoped-bad-id`: `# bug` markers and "known bug" removed, prose rewritten as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`
- `openhands-agent-server/openhands/agent_server/dependencies.py`
- `tests/agent_server/test_runtime_router.py`

Linked issue acceptance context:

- [#5833](https://github.com/OpenHands/software-agent-sdk/issues/5833): [Bug]: Conversation-scoped bash routes return 500 "'State' object has no attribute 'runtime_event_service'" for a non-UUID conversation id (expected 422) — current state closed, closed 2026-10-17T00:06:06Z.

> - [ ] All 7 conversation-scoped bash routes answer `422` for a non-UUID `runtime_conversation_id`, with the validation error located at `["path", "runtime_conversation_id"]`, and log no traceback. - [ ] Scoped bash routes with a valid conversation id, and the global `/api/bash/...` routes, are unchanged. - [ ] A unit test in `tests/agent_server/test_runtime_router.py` covers the malformed id on every scoped bash route. - [ ] The map bullet `F13.scoped-bad-id` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - The scoped router is built with the router dependency `bind_local_conversation_runtime` (`openhands-agent-server/openhands/agent_server/runtime_router.py:81`), which sets `request.state.runtime_event_service` (line 40) after its `runtime_conversation_id: UUID` parameter validates. - When that parameter fails validation, FastAPI records the error and skips that dependency, but keeps resolving the remaining dependencies (`fastapi/dependencies/utils.py` `solve_dependencies`, `errors.extend(...); continue`). - The bash endpoints' dependency `get_bash_event_service` (`openhands-agent-server/openhands/agent_server/dependencies.py:74-76`) then reads `request.state.runtime_event_service`, which was never set, so it raises `AttributeError` before FastAPI can return the collected 422. - The file, git and vscode scoped routes have no dependency that reads that state, so they already answer 422. Found by the first full agentic run of verify-agent-server; tracked in #5653. --

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5840#issuecomment-6157881748)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5840#pullrequestreview-5512682694): _This review was posted by an AI agent (OpenHands)._ Scope is correct for this repository: the fix is in the agent-server REST surface (`openhands-agent-server/.../dependencies.py`), which this repo owns, and it is a bounded malformed-input edge case, not a product/architecture decision. **What I verified on the current head** (`bf333281`, CI all green, 0 failing checks): - The root cause is real and matches the linked issue: the router dependency `bind_local_conversation_runtime` (`runtime_router.py:81`) validates `runtime_conversation_id: UUID` and only then sets `request.state.runtime_event_service` (`runtime_router.py:40`). When that path param fails, FastAPI records the error and `continue`s but keeps resolving the endpoint's remaining dependencies (`fastapi/dependencies/utils.py` `solve_dependencies`), so the old unconditional `requ

### #5901: fix(sdk): reject skill installs with unparsable frontmatter as invalid

- Commit: `cab7bb4b29476cf4a70150f5f94ba97f22766b9a` (2026-10-17T02:07:48+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5901).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5901). Families: F25.

Stated intent:

> - `_load_skill_from_dir` (`openhands-sdk/openhands/sdk/skills/installed.py`), the loader the skill install path uses, turns `yaml.YAMLError` into `SkillValidationError`, matching how directory loading in `skill.py` already treats YAML errors. The router needs no change: it already maps `SkillValidationError` to 422.
> - Unit test in `tests/sdk/skills/test_installed_skills.py`: `install_skill` raises `SkillValidationError` and installs nothing.
> - Map bullet `F25.install-bad-frontmatter`: `# bug` marker and "known bug" label removed, prose rewritten as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md`
- `openhands-sdk/openhands/sdk/skills/installed.py`
- `tests/sdk/skills/test_installed_skills.py`

Linked issue acceptance context:

- [#5894](https://github.com/OpenHands/software-agent-sdk/issues/5894): [Bug]: POST /api/skills/install of a SKILL.md with unparsable YAML frontmatter returns 500 instead of 422 — current state closed, closed 2026-10-17T00:07:49Z.

> - [ ] `POST /api/skills/install` of a SKILL.md with unparsable YAML frontmatter returns `422` with the existing invalid-skill detail, and nothing is installed. - [ ] SDK `install_skill(...)` raises `SkillValidationError` for that input (covered by a unit test in `tests/sdk/skills/test_installed_skills.py`). - [ ] The map bullet `F25.install-bad-frontmatter` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `install_skill_endpoint` (`openhands-agent-server/openhands/agent_server/skills_router.py:343-382`) maps `SkillFetchError`/`ExtensionFetchError` to 400 and only `SkillValidationError` to 422. - The install path loads the fetched skill through `SkillInstallationInterface.load_from_dir` -> `_load_skill_from_dir` (`openhands-sdk/openhands/sdk/skills/installed.py:46-51`) -> `Skill.load` -> `_load_agentskills_skill`, whose `frontmatter.load(...)` (`openhands-sdk/openhands/sdk/skills/skill.py:408`) lets `yaml.YAMLError` escape. - Project/directory loading already treats YAML errors as a bad skill (`skill.py:1128`, `:1143` catch `yaml.YAMLError`), so the install path is the inconsistent one. Suggested fix: in `_load_skill_from_dir`, convert `yaml.YAMLError` into `SkillValidationError` (install, then, maps it to 422 with no router change). Related: refresh of a skill whose source became unfetchable also answers 500, a separate item on the same umbrella (refresh endpoints have no error mapping at all). Found by the first full agentic run of verify-agent-server; tracked in #

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5901#issuecomment-6158726769)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5901#pullrequestreview-5518630810): _This review was posted by an AI agent (OpenHands)._ ## Scope This change belongs here. The repository owns the Python SDK and Agent Server, including skill behavior and the canonical REST API (`AGENTS.md` -> Cross-Repository Boundaries). It fixes a bug in `openhands-sdk/openhands/sdk/skills/installed.py` and its regression test, and the linked issue #5894 is open with `bug`, `priority:low`, and `ready-for-dev`. No maintainer/product decision is required. ## What I verified on head `8ba2cd2` - **Root cause and fix are correct.** `frontmatter.load` raises `yaml.parser.ParserError` for the reported input, which is a subclass of `yaml.YAMLError` (confirmed in the workspace venv). `_load_skill_from_dir` now converts that into `SkillValidationError`, which the install route already maps to `422 Invalid skill. Ensure the source contains a valid

### #5885: fix(sdk): drop Bedrock-only ids from the supported model list

- Commit: `acf55bcd26729d3f9212d5d5334047b6cd595d2a` (2026-10-17T02:09:09+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5885).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5885). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - `get_supported_llm_models` now drops every id that LiteLLM's model-cost map files under a Bedrock provider (`litellm_provider` `bedrock`, `bedrock_converse`, `bedrock_mantle`), besides `bedrock`-prefixed ids, instead of adding more string heuristics. Bedrock models listed from AWS when credentials are configured (`bedrock/<modelId>`) are untouched. This also applies to the SDK's `UNVERIFIED_MODELS_EXCLUDING_BEDROCK`.
> - Two unit tests in `tests/sdk/llm/test_model_list.py`.
> - The `F23.models-no-bedrock` map bullet loses its known-bug marker; its prose and a Gotchas line that mentioned region-prefixed Bedrock ids now describe the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F23-llm-catalog-and-connections.md`
- `openhands-sdk/openhands/sdk/llm/utils/unverified_models.py`
- `tests/sdk/llm/test_model_list.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5882](https://github.com/OpenHands/software-agent-sdk/issues/5882): [Bug]: GET /api/llm/models?provider=deepseek|anthropic lists AWS Bedrock-only ids (deepseek.r1-v1:0, anthropic.claude-...-v1:0) — current state closed, closed 2026-10-17T00:09:11Z.

> - [ ] `get_supported_llm_models()` without AWS credentials returns no id whose LiteLLM metadata (`litellm.model_cost[id]["litellm_provider"]`) names a Bedrock provider, in addition to the existing `bedrock`-prefix filter. - [ ] `GET /api/llm/models?provider=deepseek` and `?provider=anthropic` list no `deepseek.*` / `anthropic.*` Bedrock ids, and still list the provider's own ids. - [ ] Bedrock models listed from AWS when credentials are configured (`bedrock/<modelId>`) are unchanged. - [ ] A focused unit test in `tests/sdk/llm/test_model_list.py` covers this. - [ ] The map bullet `F23.models-no-bedrock` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `openhands-sdk/openhands/sdk/llm/utils/unverified_models.py:71-73`: `get_supported_llm_models` drops only ids that start with `bedrock`. LiteLLM's model-cost map lists most Bedrock models without that prefix (`deepseek.r1-v1:0`, `us.anthropic.claude-...`), with `litellm_provider: "bedrock"` or `"bedrock_converse"`. - `openhands-sdk/openhands/sdk/llm/utils/unverified_models.py:147`: `_extract_model_and_provider` treats the text before the first `.` as the provider when there is no `/`, so `deepseek.r1-v1:0` is filed under `deepseek` and `anthropic.claude-...` under `anthropic`. - `openhands-agent-server/openhands/agent_server/llm_router.py:135-143`: `list_models` filters that list by the extracted provider. Suggested fix: use LiteLLM's own provider metadata (`litellm.model_cost[id]["litellm_provider"]`) to drop Bedrock 

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5885#issuecomment-6158551626)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5885#pullrequestreview-5517050648): _This review was posted by an AI agent (OpenHands)._ ## Assessment The change is in scope for this repository (it fixes `get_supported_llm_models` in `openhands-sdk`, which backs `GET /api/llm/models`), and the linked issue #5882 is a bug marked `ready-for-dev`. No material correctness, security, compatibility, or acceptance defect found. ## Verification performed - Checked out the exact head `5bc90fb7186e349643789aafa754dfbd8c2c9821` and confirmed it matches the PR head. - All GitHub Actions checks for this head are green. - `uv run pytest tests/sdk/llm/test_model_list.py` — 14 passed. The two new tests fail when the fix is reverted to base `6e7a8532` and pass on the head, so they are real regression protection. - `uv run pytest tests/agent_server/test_llm_router.py tests/sdk/llm` — 1286 passed; the only 2 failures (`tests/sdk/llm/auth/t

### #5884: fix(sdk): expand ~ in OH_PERSISTENCE_DIR for the meta-profile store

- Commit: `3adf42f6377b570dfea53cc02bc669e1515f18f8` (2026-10-17T02:10:21+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5884).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5884). Families: F04, F06, F07, F09, F20, F22, F23, F30, F33, F34.

Stated intent:

> - `default_meta_profile_dir()` (`openhands-sdk/openhands/sdk/llm/meta_profile_store.py`) now returns `get_user_persistence_dir() / "meta-profiles"`, so `~` is expanded and a relative value is anchored exactly as for every other store. It is still evaluated at call time, so the router, `launch.py` and bare `MetaProfileStore()` pick it up unchanged.
> - New unit test for `OH_PERSISTENCE_DIR='~/state'`.
> - The `F22.persistence-dir` map bullet drops its `# bug` marker and "known bug" label; prose and Gotchas now describe the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F22-meta-profiles.md`
- `openhands-sdk/openhands/sdk/llm/meta_profile_store.py`
- `tests/sdk/llm/test_meta_profile_store.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5878](https://github.com/OpenHands/software-agent-sdk/issues/5878): [Bug]: With OH_PERSISTENCE_DIR='~/x', meta-profiles are written to '<server cwd>/~/x/meta-profiles' instead of the expanded directory — current state closed, closed 2026-10-17T00:10:23Z.

> - [ ] `default_meta_profile_dir()` resolves `OH_PERSISTENCE_DIR` the same way as the other stores (reusing `get_user_persistence_dir()`), so `~` is expanded and relative paths are anchored. - [ ] A unit test covers `OH_PERSISTENCE_DIR` with a leading `~`. - [ ] The `F22.persistence-dir` map bullet passes with its `# bug` marker removed. ### Installation Method `uv sync` in a checkout of main. ### Version main at 6e7a8532ef4426da08b5c06dd57cf4795303be01 ### Additional Context Root cause: `openhands-sdk/openhands/sdk/llm/meta_profile_store.py:41-43`, `default_meta_profile_dir()` returns `Path(os.environ["OH_PERSISTENCE_DIR"]) / "meta-profiles"` without `expanduser()` or anchoring, unlike `get_user_persistence_dir()` in `openhands-sdk/openhands/sdk/utils/path.py:32`, which the settings and LLM profile stores use. Callers: `openhands-agent-server/openhands/agent_server/meta_profiles_router.py:47`, `openhands-agent-server/openhands/agent_server/launch.py:71`, and bare `MetaProfileStore()`. Related: https://github.com/OpenHands/software-agent-sdk/issues/3835 / https://github.com/OpenHands/software-agent-sdk/pull/3836 (added the `OH_PERSISTENCE_DIR` support for meta-profiles), https://github.com/OpenHands/software-agent-sdk/issues/3815. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5884#issuecomment-6158518958)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5884#pullrequestreview-5516489839): _This review was posted by an AI agent (OpenHands)._ ## Review — PR #5884 **Scope:** In scope. `default_meta_profile_dir()` lives in the SDK, and its only consumers (the agent-server meta-profiles router, `launch.py`, and bare `MetaProfileStore()`) are in this repository. No cross-repo ownership question. **Change:** `default_meta_profile_dir()` now returns `get_user_persistence_dir() / "meta-profiles"` instead of carrying its own `Path(os.environ["OH_PERSISTENCE_DIR"])` copy. Call-time evaluation is preserved, so the router, `launch.py`, and bare `MetaProfileStore()` all pick up the same resolution as `settings.json`, LLM profiles, agent profiles, and the skills caches. **Verification I ran on b1f62cde:** - Live, through the real agent-server entry point (`python -m openhands.agent_server` with `HOME=<tmp>/home`, `OH_PERSISTENCE_DIR=~/qa

### #5867: fix(agent-server): reject MCP OAuth callback URLs with a bad port as 400

- Commit: `e5a1602e3bbc8b55571b7777bd6b6142101f11ad` (2026-10-17T02:12:02+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5867).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5867). Families: F19.

Stated intent:

> - `_validate_callback_url` turns the `ValueError` from `.port` into `400 Unexpected OAuth callback URL`, and a `ValueError` from `urlparse` itself (e.g. `http://[::1/callback`, unclosed IPv6 bracket) into `400 Invalid OAuth callback URL`. Valid URLs and the existing checks are unchanged; the job keeps waiting.
> - New parametrized test in `tests/agent_server/test_mcp_router.py` (port 99999, port `abc`, malformed IPv6) asserting 400, the detail, and that the job stays `authorizing`.
> - Map bullet `F19.oauth-callback-bad-port`: `# bug` markers and "known bug" removed, the two posts now check the 400 detail, prose rewritten as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F19-mcp.md`
- `openhands-agent-server/openhands/agent_server/mcp_router.py`
- `tests/agent_server/test_mcp_router.py`

Linked issue acceptance context:

- [#5863](https://github.com/OpenHands/software-agent-sdk/issues/5863): [Bug]: MCP OAuth callback URL with port 99999 or a non-numeric port answers 500 instead of 400 — current state closed, closed 2026-10-17T00:12:04Z.

> - [ ] A callback URL with an out-of-range or non-numeric port answers `400` with `detail: "Unexpected OAuth callback URL"`, and the job keeps waiting for consent. - [ ] In-range ports and the matching callback URL behave as today. - [ ] `tests/agent_server/test_mcp_router.py` covers both ports. - [ ] The map bullet `F19.oauth-callback-bad-port` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/mcp_router.py`: `_validate_callback_url` (line 746) compares `parsed.port != expected_parsed.port` (line 760). `urllib.parse.SplitResult.port` raises `ValueError` for a port outside 0-65535 or a non-numeric one, nothing catches it, and the generic handler turns it into a 500. The same applies to `urlparse` itself for a malformed IPv6 host such as `http://[::1/callback` (`ValueError: Invalid IPv6 URL`, line 747), which would fit `400 Invalid OAuth callback URL`. Related: the sibling item `F19.oauth-callback-replay` in the same route (transport errors when forwarding the callback) is tracked separately. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5867#issuecomment-6158327530)

### #5916: fix(agent-server): drop upstream Date and Server from proxied responses

- Commit: `7abcd3bab78f988b2f2ca2485f4626a5484ce313` (2026-10-17T02:13:25+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5916).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5916). Families: F12, F29.

Stated intent:

> - `proxy_http` (`docker_runtime/proxy.py`) drops the upstream `date` and `server` headers from the response it returns, so uvicorn's single pair is the only one. The change is response-side only: `_filter_headers` and request header forwarding are untouched.
> - `proxy_http` is shared with the Docker runtime conversation proxy, whose inner agent server also sends `date`/`server`, so the same duplication goes away there.
> - New unit test in `tests/agent_server/docker_runtime/test_proxy.py`; the `F29.proxy-response-headers` map bullet loses its known-bug marker.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md`
- `openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py`
- `tests/agent_server/docker_runtime/test_proxy.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5914](https://github.com/OpenHands/software-agent-sdk/issues/5914): [Bug]: Proxied Canvas app-backend responses carry two Date and two Server headers — current state closed, closed 2026-10-17T00:13:27Z.

> - [ ] A response proxied through the Canvas app-backend bridge carries one `Date` and one `Server` header (the agent server's own). - [ ] Request header forwarding to the upstream is unchanged. - [ ] A unit test in `tests/agent_server/docker_runtime/test_proxy.py` covers the response headers `proxy_http` returns. - [ ] The map bullet `F29.proxy-response-headers` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: `proxy_http` in `openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py` builds the response with `headers=_filter_headers(upstream.headers)` (line 175). `_filter_headers` (lines 58-59) drops only the hop-by-hop/credential set `_HOP_BY_HOP_HEADERS` (lines 32-50), so the upstream `date` and `server` pass through. uvicorn then prepends its own `date` and `server` default headers without de-duplicating (`uvicorn/protocols/http/h11_impl.py:471`, `httptools_impl.py:479`: `self.default_headers + list(message["headers"])`). `_filter_headers` is shared by the request and response directions, and `proxy_http` is shared by the Canvas bridge (`canvas_extensions/bridge.py:467`) and the Docker runtime conversation proxy (`docker_runtime/routers.py:102`), whose inner agent server also sends `date`/`server`, so the same duplication happens there. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5916#issuecomment-6159040303)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5916#pullrequestreview-5512474921): _This review was posted by an AI agent (OpenHands)._ ## Verdict No material correctness, security, or compatibility defect found on head `1cb0168`. ## Scope This change belongs in this repository: it fixes `proxy_http` in `openhands-agent-server/openhands/agent_server/docker_runtime/proxy.py`, the Agent Server's own proxy, and the linked issue #5914 (labels `bug`, `priority:low`, `ready-for-dev`, open) targets the same file. No ownership move is warranted. ## What I verified on the current head - **The fix and its blast radius.** `_RESPONSE_DROP_HEADERS = {"date", "server"}` is applied only on the response path (`headers={...}` at lines 179-183), and `_filter_headers` (line 120, request direction) is unchanged, so request forwarding is untouched as the issue's acceptance criteria require. `proxy_http` is shared by the Canvas app-backend b

### #5826: fix(agent-server): read agent_final_response from the active branch

- Commit: `079d410db4fe17ccfe28b80f58b12676cf854c7c` (2026-10-17T02:25:16+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5826).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5826). Families: F05, F06, F07, F08, F09, F10, F11, F12, F31.

Stated intent:

> - `EventService._get_agent_final_response_sync` now passes `state.active_branch()` to `get_agent_final_response`. This covers the REST route and the OpenAI-compatible gateway (`openai/service.py`), which both go through `EventService.get_agent_final_response`. The SDK's own callers (task manager, delegate, agent hooks) read fresh sub-conversations that are never navigated, so they are unchanged; the TypeScript client just calls the route.
> - New unit test on a real `LocalConversation`: answer is returned, navigate back gives `""`, navigate forward to the answer returns it again. The existing mocked tests now stub `active_branch()`.
> - Feature map: `F10.navigate-final-response` loses its `known bug` label and `# bug` marker; the prose now states the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md`
- `openhands-agent-server/openhands/agent_server/event_service.py`
- `tests/agent_server/test_conversation_response.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5822](https://github.com/OpenHands/software-agent-sdk/issues/5822): [Bug]: agent_final_response still returns the abandoned branch's answer after navigate moves HEAD back — current state closed, closed 2026-10-17T00:25:17Z.

> - [ ] `agent_final_response` (and the OpenAI-compatible gateway, which uses the same `EventService.get_agent_final_response`) only considers events on the active branch. - [ ] A focused unit test covers a navigate back before the agent's answer (returns `""`) and the normal linear case (unchanged). - [ ] The map bullet `F10.navigate-final-response` passes with its `# bug` marker removed. ### Installation Method `uv sync` in a checkout of main. ### Version main at 6a298a48fcdb06c3bf5835d648a7d8b4d3d31cf5 ### Additional Context Root cause: `EventService._get_agent_final_response_sync` (openhands-agent-server/openhands/agent_server/event_service.py:2203) calls `get_agent_final_response(self._conversation._state.events)`, i.e. the whole `EventLog`, instead of `self._conversation._state.active_branch()` as `LocalConversation` does for title generation, stuck detection and pending actions (openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py). The conversation tree / navigate API came in with #3922 and #3923. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5826#issuecomment-6157712499)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5826#pullrequestreview-5512658173): _This review was posted by an AI agent (OpenHands)._ ## Scope Belongs in this repository: the agent-server owns this REST contract, and the referenced issue #5822 (`bug`, `priority:low`, `ready-for-dev`) is the tracked bug for this repo. No product/architecture decision is needed. ## Verification (head `e35e8e8c`) - Head is still `e35e8e8c916f7b9fd37b48b17898f006ec9c9f02`; PR open, not draft, no labels. - All check runs on this head are green, including `agent-server-tests`, `cross-tests`, `sdk-tests`, `pre-commit`, `REST API (OpenAPI)`, `agent-server-api`, and `security`. - `uv run pytest tests/agent_server/test_conversation_response.py` -> 8 passed (including the new real-`LocalConversation` test). - `uv run pytest tests/agent_server -k openai` -> 17 passed. - Linked issues re-read: #5822 open with `bug`, `priority:low`, `ready-for-dev`

### #5812: docs(typescript): describe the pre-run 200-and-defer switchAcpModel behaviour

- Commit: `9ad70fedddc4e7e68df476a30f9c464b4ca1081c` (2026-10-17T02:34:06+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5812).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5812). Families: F34.

Stated intent:

> - Rewrite both `switchAcpModel` docstrings: live session switches in place via `session/set_model`; before the first run the server answers 200 and stores the model for the first session; non-ACP conversations get 400.
> - Drop the `# bug` marker from the `F09.ts-client-acp-docs` map bullet, add a positive check for the new wording, and update the F09 Gotchas line.
> - No code change; the Python docstrings (`conversation_router.py`, `LocalConversation.switch_acp_model`) already describe the deferred behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`
- `clients/typescript/src/client/conversation-client.ts`
- `clients/typescript/src/conversation/remote-conversation.ts`

Linked issue acceptance context:

- [#3763](https://github.com/OpenHands/software-agent-sdk/issues/3763): ACP: persist pre-session model switch instead of raising (switch_acp_model loses the model before first run()) — current state closed, closed 2026-06-17T09:15:28Z.

> ## Summary A pre-session ACP model switch (changing the model on an ACP conversation **before the first `run()`**) is silently lost: `LocalConversation.switch_acp_model` raises before it persists the new value, so the model the first run actually uses stays the construction-time `acp_model`. Downstream this manifests as a misleading "success" in OpenHands and agent-canvas (the chip shows the new model; the run uses the old one) or a hard 409 error, depending on deployment. This issue proposes fixing it **at the root in the SDK** — persist the s

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5808](https://github.com/OpenHands/software-agent-sdk/issues/5808): [Bug]: TS client switchAcpModel docstrings say the server returns 409 before the first message, but it answers 200 and defers the switch — current state closed, closed 2026-10-17T00:34:08Z.

> - [ ] `ConversationClient.switchAcpModel` and `RemoteConversation.switchAcpModel` docstrings describe the 200-and-defer behaviour before the first run and no longer mention a 409. - [ ] The `F09.ts-client-acp-docs` map bullet passes with its `# bug` marker removed, and the F09 Gotchas line about the stale docstrings is updated. ### Installation Method `uv sync` in a checkout of main. ### Version main at 6a298a48fcdb06c3bf5835d648a7d8b4d3d31cf5 ### Additional Context Root cause: docstrings written for the original 409 behaviour (#3390) were not updated when #3763 made the pre-session switch persist instead of raising. Locations: `clients/typescript/src/client/conversation-client.ts:413-419` and `clients/typescript/src/conversation/remote-conversation.ts:326-331`. Server behaviour: `openhands-agent-server/openhands/agent_server/conversation_router.py:697-710`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:1723-1748`. Python docstrings are already correct. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5812#issuecomment-6157501660)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5812#pullrequestreview-5511606327): _This review was posted by an AI agent (OpenHands)._ **Scope.** Documentation-only change inside `clients/typescript/`, which this repository owns. No code, endpoint, or type change. Correctly scoped here; nothing to move. **What I verified on head `f0956f6c`** - The two rewritten docstrings now match the server. `conversation_router.py` (`switch_conversation_acp_model`) declares only 400/404/504 and documents the pre-first-run case as persisted and applied when the first session starts, returning 200 either way; `LocalConversation.switch_acp_model` and `EventService.switch_acp_model` confirm the same. `RemoteConversation.switchAcpModel`'s linked `{@link switchLlm}` and `ConversationClient`'s linked `{@link switchLLM}` are both valid targets, so the doc links resolve. - No stale wording remains: `grep -c 'returns 409 before the first mess

### #5802: fix(agent-server): reject a whitespace-only conversation title on PATCH

- Commit: `f676088d7a46f7bf6ab15f4b2d6551f8106cc30e` (2026-10-17T02:37:16+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5802).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5802). Families: F04, F08, F10, F11, F12, F13.

Stated intent:

> - `UpdateConversationRequest` gets a `title` field validator that rejects a title that is blank after trimming, so the route answers `422` before the service runs and the stored title is kept. Non-blank titles are unchanged (the service still strips them; `max_length=200` still applies).
> - New router test `test_update_conversation_whitespace_only_title_rejected` (spaces, and tab/newline) asserts `422` and that the service is never called.
> - The `F04.patch-whitespace-title` map bullet loses its `# bug` marker and "known bug" label; its prose now states the expected behaviour.
> 
> The Python SDK `RemoteConversation` and `clients/typescript` only forward the title to this endpoint, so the server-side validator covers them.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md`
- `openhands-agent-server/openhands/agent_server/models.py`
- `tests/agent_server/test_conversation_router.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5799](https://github.com/OpenHands/software-agent-sdk/issues/5799): [Bug]: PATCH /api/conversations/{id} with a whitespace-only title returns 200 and erases the stored title — current state closed, closed 2026-10-17T00:37:18Z.

> - [ ] `PATCH /api/conversations/{id}` with a whitespace-only title returns `422` and leaves the stored title unchanged. - [ ] A normal title still works, and surrounding whitespace is still stripped before it is stored. - [ ] A focused unit test covers the whitespace-only title. - [ ] The map bullet `F04.patch-whitespace-title` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6a298a4 ### Additional Context Root cause: - `UpdateConversationRequest.title` (`openhands-agent-server/openhands/agent_server/models.py:514-519`) has `min_length=1`, which accepts `" "`. - `ConversationService.update_conversation` (`openhands-agent-server/openhands/agent_server/conversation_service.py:1807`) stores `request.title.strip()`, which is `""`. - `AutoTitleSubscriber` (`conversation_service.py:2494`) skips any conversation whose `stored.title is not None`, so the emptied title also disables autotitle. Related: the PATCH unknown-id status (`F04.patch-unknown-id`) is a separate bug. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5802#issuecomment-6157371915)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5802#pullrequestreview-5511512208): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope. The change lives in `openhands-agent-server` (this repository owns the Agent Server and its canonical REST API), and the linked issue #5799 carries `bug` / `priority:low` / `ready-for-dev`, so no product/architecture decision is outstanding. It is a focused backend validation fix; the OpenAPI surface for the PATCH title schema is unchanged, so the `clients/typescript` mirror needs no coordinated update. ## Review The reported bug is that `PATCH /api/conversations/{id}` with `{"title": " "}` returned `200 {"success": true}` and persisted the title as `""`, whereas `{"title": ""}` was correctly rejected with `422`. The proposed fix adds a `field_validator("title")` to `UpdateConversationRequest` that rejects any non-`None` title that is blank after `.strip()`. I verifie

### #5864: fix(agent-server): skip the workspaces save when a write changes nothing

- Commit: `478a6f9caebc42e3e2625f741ea73336a2551b90` (2026-10-17T02:41:31+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5864).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5864). Families: F03, F17, F18, F31.

Stated intent:

> - `FileWorkspacesStore.update` skips `save` when `update_fn` returns the document it was given; every route's `apply` already returns its input unchanged for a no-op, so no router change is needed. Real adds and deletes still write (and still answer 500 on read-only storage).
> - Two unit tests in `tests/agent_server/test_workspaces_router.py`: a refused delete on a fresh directory leaves no file; with `save` failing, re-adds stay 200, unknown deletes 404, a real add 500, and the file keeps its inode and mtime.
> - Drops the `# bug` markers of `F17.noop-delete-no-file`, `F17.noop-write-untouched` and `F17.noop-write-readonly` and rewrites their prose and the Gotchas line as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F17-workspaces-registry.md`
- `openhands-agent-server/openhands/agent_server/persistence/store.py`
- `tests/agent_server/test_workspaces_router.py`

Linked issue acceptance context:

- [#5849](https://github.com/OpenHands/software-agent-sdk/issues/5849): [Bug]: workspaces write routes rewrite workspaces.json when nothing changes; a 404 DELETE creates the file and on read-only storage no-op writes answer 500 — current state closed, closed 2026-10-17T00:41:33Z.

> - [ ] A `DELETE` (workspace or parent) of a path that is not saved answers 404 and creates or rewrites no `workspaces.json`. - [ ] Re-posting only already saved paths (workspaces or parents) answers 200 with the unchanged document and does not rewrite the file. - [ ] On storage the server cannot write, those no-op requests keep their 404/200 answers, while a real add still answers 500 `Failed to save workspaces`. - [ ] Real adds and deletes still persist, including stamping `schema_version` 1 on a hand-written document. - [ ] `tests/agent_server/test_workspaces_router.py` covers the fresh-directory delete and the no-op writes. - [ ] The map bullets `F17.noop-delete-no-file`, `F17.noop-write-untouched` and `F17.noop-write-readonly` pass with their `# bug` markers removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: `FileWorkspacesStore.update` (`openhands-agent-server/openhands/agent_server/persistence/store.py:772-788`) always calls `self.save(updated)` (line 787) after `update_fn`, even though every route's `apply` already returns its input unchanged when nothing matched (`openhands-agent-server/openhands/agent_server/workspaces_router.py:110, 145, 189, 226`). The delete routes raise 404 only after `store.update` returns (lines 162, 243), so a failed save turns the 404 into 500. Suggested fix: skip `save` when `update_fn` returns the very object it was given. The `.workspaces.lock` file created by the lock is not part of this bug. Found by the first full agentic run of verify-agent-server; tracked in 

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5864#issuecomment-6158210060)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5864#pullrequestreview-5511929674): _This review was posted by an AI agent (OpenHands)._ ## Summary The change fixes the reported no-op-write bug in `FileWorkspacesStore.update` (`openhands-agent-server/openhands/agent_server/persistence/store.py`) by skipping `save` when `update_fn` returns the identical document (`updated is not workspaces`). I verified that all four route `apply` closures in `workspaces_router.py` already return the same object on a no-op, so the identity check is correct for every caller of `WorkspacesStore.update`; no other caller exists in the tree. ## Verification - Head `35d3349a5788fc9e3f7a8f8fc08a0f382e2b9d04` matches the request; PR is open, not draft, no labels. - `uv run pytest tests/agent_server/test_workspaces_router.py` -> 14 passed. The two new tests exercise the fresh-directory 404 delete and the no-op paths with a failing `save` (re-add 2

### #5851: fix(agent-server): document 422 for an invalid secret name

- Commit: `07e6e059afd836817d8263d3edb67cb95d5c203e` (2026-10-17T02:44:59+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5851).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5851). Families: F18, F19, F32.

Stated intent:

> - Docstrings of `get_secret_value` and `delete_secret` now say `422 if name format is invalid`. The status code is unchanged. (`PUT /api/settings/secrets` already said 422 since https://github.com/OpenHands/software-agent-sdk/pull/5682.)
> - New parametrized test checks, for GET, DELETE and PUT, that an invalid name gets 422 and that the route's `/openapi.json` description names 422 and not 400.
> - Drops the `F18.secret-name-contract` known-bug marker in the verify-agent-server map and rewrites its prose and the Gotchas line as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F18-settings-and-secrets.md`
- `openhands-agent-server/openhands/agent_server/settings_router.py`
- `tests/agent_server/test_settings_router.py`

Linked issue acceptance context:

- [#5846](https://github.com/OpenHands/software-agent-sdk/issues/5846): [Bug]: GET/DELETE /api/settings/secrets/{name} document 400 for an invalid name, but answer 422 — current state closed, closed 2026-10-17T00:45:01Z.

> - [ ] The docstrings of `get_secret_value` and `delete_secret` (and so the OpenAPI descriptions) say 422 for an invalid name; the status code itself is unchanged. - [ ] A unit test checks that the published OpenAPI description of each secret route names the status an invalid name actually gets. - [ ] The map bullet `F18.secret-name-contract` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/settings_router.py`: - `_validate_secret_name` (line 92) raises `HTTP_422_UNPROCESSABLE_ENTITY` (line 104-105), and its own docstring (line 101) says 422. - `get_secret_value` docstring, line 501: `400 if name format is invalid, 404 if secret not found.` - `delete_secret` docstring, line 580: `400 if name format is invalid, 404 if secret not found, 500 if file is corrupted.` No OpenAPI document or TypeScript client text repeating the claim is checked in. Related: #5682 (fixed the `PUT` docstring), #5623. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5851#issuecomment-6158029851)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5851#pullrequestreview-5511894462): _This review was posted by an AI agent (OpenHands)._ ## Summary Reviewed PR #5851 at head `a90f6d132ef803329654bae6a2f52c065384a465` ("fix(agent-server): document 422 for an invalid secret name"), +36/-14 across 3 files. The change aligns the `GET`/`DELETE /api/settings/secrets/{name}` docstrings — and therefore the published OpenAPI descriptions — with the 422 that `_validate_secret_name` actually raises, adds a regression test, and updates the verify-agent-server F18 map bullet. ## Verification - Scope/ownership: belongs here. The routes live in `openhands-agent-server/openhands/agent_server/settings_router.py`, which this repository owns. No cross-repo/client regeneration is needed: no checked-in OpenAPI document repeats the claim, and the TypeScript client (`clients/typescript/src/client/settings-client.ts`) neither asserts nor docume

### #5874: fix(agent-server): describe the real materialize report in the seed docstring

- Commit: `fd96074269cee08e8d8da917512b26b8d49c98f6` (2026-10-17T02:50:59+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5874).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5874). Families: F21, F30.

Stated intent:

> - Docstring only: describe what materialize actually reports. No behaviour change.
> - `test_seed_skips_ghost_llm_profile_for_bare_defaults` now also pins the `errors` entry and the absence of `dangling_llm_profile_ref` in the materialize body.
> - verify-agent-server map: drop the known-bug marker on `F21.dangling-llm-report` and rewrite its prose as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F21-agent-profiles.md`
- `openhands-agent-server/openhands/agent_server/agent_profiles_router.py`
- `tests/agent_server/test_agent_profiles_router.py`

Linked issue acceptance context:

- [#5871](https://github.com/OpenHands/software-agent-sdk/issues/5871): [Bug]: _seed_default_llm_profile docstring says materialize reports dangling_llm_profile_ref, but the materialize response has no such field — current state closed, closed 2026-10-17T00:51:00Z.

> - [ ] The `_seed_default_llm_profile` docstring describes the materialize report as `valid` false, `llm_profile_resolved` false and an `errors` entry, and no longer promises `dangling_llm_profile_ref` from materialize. - [ ] No runtime behaviour changes (materialize and launch responses are identical). - [ ] `test_seed_skips_ghost_llm_profile_for_bare_defaults` pins the documented materialize report (the `errors` entry, and no `dangling_llm_profile_ref` key). - [ ] The map bullet `F21.dangling-llm-report` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `openhands-agent-server/openhands/agent_server/agent_profiles_router.py:181-183`: the docstring says "materialize/dry-run reports it as `dangling_llm_profile_ref` rather than raising". - `openhands-sdk/openhands/sdk/profiles/resolver.py:109` (`llm_profile_resolved`) and `:496` (the `"LLM profile ... not found"` error) are what materialize actually reports; the diagnostics model has `dangling_mcp_server_refs` and `dangling_meta_profile_ref` but no `dangling_llm_profile_ref`. - `openhands-sdk/openhands/sdk/launch/errors.py:53`: `dangling_llm_profile_ref` is only in the launch's 422 detail. The other direction (adding a `dangling_llm_profile_ref` field to the materialize diagnostics) would be an API addition; this issue proposes only correcting the docstring. Related: #4031 (the ghost-profile fix that introduced the docstring), #3933. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5874#issuecomment-6158452510)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5874#pullrequestreview-5515574008): _This review was posted by an AI agent (OpenHands)._ ## Review: docstring-only correction, verified against the code **Scope gate.** This change belongs in this repository. It corrects the docstring of `_seed_default_llm_profile` in the Agent Server package, its regression test, and the `verify-agent-server` feature map — all owned here per the root `AGENTS.md` cross-repository boundaries. No product/architecture decision is required; the linked issue (#5871, `bug`/`priority:low`/`ready-for-dev`) explicitly scopes out the alternative API addition. **Verification against the workspace code (head `f1feebd6`).** - `openhands-sdk/openhands/sdk/profiles/resolver.py:105-142` — `AgentProfileDiagnostics` has `llm_profile_ref`, `llm_profile_resolved`, `errors`, plus `dangling_mcp_server_refs` and `dangling_meta_profile_ref`, and **no** `dangling_l

### #5966: fix(sdk): keep streamed deltas out of RemoteConversation.state.events

- Commit: `8d2c996de5bf4bc558c03451618124c49df24397` (2026-10-17T03:01:34+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5966).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5966). Families: F33.

Stated intent:

> - `RemoteEventsList.create_default_callback()` skips `StreamingDeltaEvent`; user callbacks and the visualizer still receive every delta.
> - Unit test: the default callback keeps deltas out of the cached events.
> - Map: drop the `# bug` marker from `F33.conversation-streaming-state` and rewrite its prose and the Gotchas line as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`
- `openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`
- `tests/sdk/conversation/remote/test_remote_events_list.py`

Linked issue acceptance context:

- [#4671](https://github.com/OpenHands/software-agent-sdk/issues/4671): [Epic] Streaming: separate the wire format from the durable event record — current state open, closed —.

> - [ ] Step 0 baselines landed (#4679) — the byte budget is derived from a measured frame-size distribution, not a guess. - [ ] Step 1 independent fixes landed: #4674 · #4675 · #4676 · #4677 · #4678 · #4077. - [ ] Step 1b landed (#4696) — `StreamingDeltaEvent` is not an `Event`, deltas have their own fan-out, the frame on the wire is unchanged, and `receives_streaming_deltas` is gone. - [x] The runtime idle timer is reset during a long stream that produces no durable events (#4695), and stays so after step 1b. - [x] Step 2 landed (#4680) — `append_event` returns its sequence number and publish happens after persist. - [x] Steps 3+4 landed (#4681) — `/sockets/session/{id}` serves durable events in a non-`Event` envelope, with cursor, paged replay and byte-budget admission. - [x] Step 5 landed (#4682) — `StreamContext` mints the identity; every `ItemStarted` is retired by exactly one `Durable` or `ItemAborted`. - [ ] Step 6 landed (OpenHands/OpenHands#16965) — canvas keys slots by event id and `handle-event-for-ui.ts` contains no text comparison. - [ ] Step 7 landed (#4683) — `StreamingDeltaEvent` is deleted outright and the old endpoint is documented as deprecated. - [ ] `Event` is unchanged on disk throughout: same class, same `extra="forbid"`, same bytes.

- [#4683](https://github.com/OpenHands/software-agent-sdk/issues/4683): Streaming step 7: stop emitting StreamingDeltaEvent and freeze the legacy events endpoint — current state open, closed —.

> - [ ] `StreamingDeltaEvent` has no construction site. - [ ] A webhook receives the same event count for a streamed and non-streamed conversation, with no filter in the path. - [ ] The legacy endpoint is documented as deprecated with a removal target.

- [#5963](https://github.com/OpenHands/software-agent-sdk/issues/5963): [Bug]: RemoteConversation.state.events keeps every StreamingDeltaEvent frame with a streaming LLM — current state closed, closed 2026-10-17T01:01:36Z.

> - [ ] With a streaming LLM, `RemoteConversation.state.events` contains no `StreamingDeltaEvent`; its stored events match the server's. - [ ] User callbacks (and the visualizer) still receive every `StreamingDeltaEvent`. - [ ] A unit test in `tests/sdk/conversation/remote/` covers the default callback with a `StreamingDeltaEvent`. - [ ] The map bullet `F33.conversation-streaming-state` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: `RemoteConversation._initialize_connection` (`openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py:1091`) registers `self._state.events.create_default_callback()` as a socket callback, and that callback (`remote_conversation.py:495-499`) calls `RemoteEventsList.add_event` for every event, including the transient `StreamingDeltaEvent` frames the server publishes straight to PubSub (`openhands-agent-server/openhands/agent_server/event_service.py:1218-1226`) without persisting them. Reconciliation over REST never returns them, so they only enter the cache through the socket path. Related broader transport work (not the same report): https://github.com/OpenHands/software-agent-sdk/issues/4671 and https://github.com/OpenHands/software-agent-sdk/issues/4683; https://github.com/OpenHands/software-agent-sdk/pull/4700 gives deltas their own fan-out, which would also remove this. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5966#issuecomment-6159953472)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5966#pullrequestreview-5512811912): _This review was posted by an AI agent (OpenHands)._ Reviewed head `55c3ee3` (base `main`). The change makes `RemoteEventsList.create_default_callback()` drop `StreamingDeltaEvent` before caching, which is the correct minimal fix for #5963: the server publishes deltas directly to pub/sub and never persists them (`event_service._publish_stream_delta`; see the `StreamingDeltaEvent` docstring), so the client cache should match the stored event log. Deltas still reach user callbacks and the visualizer because `compose_callbacks` invokes every callback with the raw event and only the default callback returns early. `reconcile()`/`_add_event_unsafe()` fetch from the server, which never stores deltas, so they remain clean too. Verification performed in the workspace: - `uv run pytest tests/sdk/conversation/remote/test_remote_events_list.py` -> 1

### #5917: fix(agent-server): refuse a non-gzip canvas backend artifact with 409

- Commit: `57c71706e83c83b8f58a6b4efc959f5132cec94b` (2026-10-17T03:03:50+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5917).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5917). Families: F28.

Stated intent:

> - `CanvasExtensionBackendManager.prepare` translates archive-format errors from extraction (`tarfile.TarError`, `gzip.BadGzipFile`, `EOFError`, `zlib.error`) into `ValueError("backend artifact is not a valid gzip tar archive")`, so the route answers 409. The temporary extraction directory is still removed and no approval is written.
> - Unit tests for a plain-bytes, a gzip-but-not-tar and a truncated gzip artifact (manager level), plus the route's 409.
> - Map: `F28.prepare-corrupt-archive` loses its `# bug` marker and "known bug" label; the recipe now also checks the 409 detail.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md`
- `openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py`
- `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`

Linked issue acceptance context:

- [#5910](https://github.com/OpenHands/software-agent-sdk/issues/5910): [Bug]: Canvas backend prepare returns 500 'not a gzip file' for a non-gzip artifact instead of 409 — current state closed, closed 2026-10-17T01:03:52Z.

> - [ ] Prepare with a sha256-matching artifact that is not a gzip tar archive returns `409` with a reason, not `500`. - [ ] No approval file and no extracted artifact directory are written for that app. - [ ] A unit test in `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py` covers the non-gzip artifact. - [ ] The map bullet `F28.prepare-corrupt-archive` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `CanvasExtensionBackendManager._extract_archive` (`openhands-agent-server/openhands/agent_server/canvas_extensions/backend.py:302`) opens the artifact with `tarfile.open(archive, mode="r:gz")`, which raises `tarfile.ReadError` ("not a gzip file") for non-gzip bytes. All of its own refusals are `ValueError`. - `prepare_canvas_extension_backend_endpoint` (`openhands-agent-server/openhands/agent_server/canvas_extensions_router.py:359-362`) maps only `ValueError` to `409`, so `tarfile.ReadError` reaches the generic handler as `500`. Suggested fix: in `_extract_archive`, translate archive-format errors (`tarfile.TarError`, plus the gzip/zlib errors a truncated stream raises mid-read) into `ValueError("backend artifact is not a valid gzip tar archive")`. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5917#issuecomment-6159100199)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5917#pullrequestreview-5512511330): _This review was posted by an AI agent (OpenHands)._ ## Scope Agent Server Python fix in the owning repository (`openhands-agent-server/openhands/agent_server/canvas_extensions/`). No cross-repository or product/architecture decision required, so this proceeds as a normal technical review. ## What I verified on head `8fe2c264` - The reported symptom is real: the prepare route (`canvas_extensions_router.py`) maps only `ValueError` to 409, while `_extract_archive` uses `tarfile.open(..., mode="r:gz")`, which raises `tarfile.ReadError` / `gzip.BadGzipFile` / `EOFError` / `zlib.error` — none of which are `ValueError`. The fix wraps the extraction call in `prepare` and translates those into `ValueError("backend artifact is not a valid gzip tar archive")`, which the route turns into 409. - Side effects are preserved: the translation happens ins

### #5898: fix(agent-server): map profile-store errors in scoped secrets list

- Commit: `1396fa7ff6510a28fe9587b1708376f23507cd5b` (2026-10-17T03:04:29+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5898).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5898). Families: F18, F19, F32.

Stated intent:

> - `list_secrets` now reads the scoping profile inside `store_errors()`, so the route answers 400 / 503 like the agent-profile routes; unknown ids still answer 404 and valid ids still scope the list.
> - Two unit tests in `tests/agent_server/test_settings_router.py` (unloadable profile -> 400, lock timeout -> 503).
> - Feature map `F18.secret-scope-profile-errors`: `# bug` markers removed, prose and the Gotchas line rewritten as expected behaviour, and the first bullet now declares `Requires: F18.secret-put-list-get` (it reads the `QA_F18_TOKEN` secret that bullet creates, so `--only` failed in arrange instead of reaching the marker).

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F18-settings-and-secrets.md`
- `openhands-agent-server/openhands/agent_server/settings_router.py`
- `tests/agent_server/test_settings_router.py`

Linked issue acceptance context:

- [#5632](https://github.com/OpenHands/software-agent-sdk/issues/5632): [Bug]: Waiting on a locked profile or connection store freezes the whole server; /alive stalls up to 30 s — current state open, closed —.

> - [ ] While a request on any of the four stores waits for its lock, `GET /alive` and unrelated routes answer within 2 s. - [ ] The waiting request still returns 503 after the lock timeout and writes nothing. - [ ] A regression test holds a store lock and asserts `/alive` answers during the wait. - [ ] Map bullets `F20.store-busy-responsive`, `F21.busy-blocks-server`, `F22.store-busy-responsive`, `F23.store-busy-responsive` pass with their known-bug markers removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context Root cause: FastAPI runs `async def` endpoints on the event loop, and `FileLock.acquire(timeout=30)` polls with a blocking sleep, holding the loop for the whole wait. - Store locks (30 s), under `openhands-sdk/openhands/sdk/`: `llm/llm_profile_store.py:130-146`, `profiles/agent_profile_store.py:71-84`, `llm/meta_profile_store.py:123-140`, `llm/provider_connection_store.py:166-176`. The timeout becomes 503 in `store_errors()` (`_secrets_exposure.py:127-137`; `meta_profiles_router.py:77` for meta-profiles). - `async def` handlers calling stores inline (`openhands-agent-server/openhands/agent_server/`): `profiles_router.py:148,170,212,378,400,452`; `agent_profiles_router.py:289,318,367,406,461,496` and `store.load` at 568 in `materialize_agent_profile`; `meta_profiles_router.py:124,141,161,190,212`; `provider_connections_router.py:146,159,191,248`. - Contrast: `materialize_agent_profile` already runs `preview_launch` via `asyncio.to_thread` (`agent_profiles_router.py:578`), and conversation launch does the same f

- [#5883](https://github.com/OpenHands/software-agent-sdk/issues/5883): [Bug]: GET /api/settings/secrets?agent_profile_id= answers 500 for an unloadable agent profile or a busy profile store (agent-profile routes answer 400/503) — current state closed, closed 2026-10-17T01:04:31Z.

> - [ ] An unloadable agent profile in `?agent_profile_id=` answers 400 with the load error, not 500. - [ ] A lock timeout on the agent-profile store answers 503 `Profile store is busy. Please retry.`, not 500. - [ ] An unknown id still answers 404 `Agent profile not found`; a valid id still scopes the list. - [ ] A unit test covers both error paths. - [ ] The `F18.secret-scope-profile-errors` map bullets pass with their `# bug` markers removed. ### Installation Method `uv sync` in a checkout of main. ### Version main at 6e7a8532ef4426da08b5c06dd57cf4795303be01 ### Additional Context Root cause: `list_secrets` in `openhands-agent-server/openhands/agent_server/settings_router.py:466-472` calls `AgentProfileStore.name_for_id` and `AgentProfileStore.load` outside `store_errors()` (`openhands-agent-server/openhands/agent_server/_secrets_exposure.py:127`), the context manager the agent-profile routes use to map `ValueError` to 400 and `TimeoutError` to 503. Related: #5632 (a held store lock stalls the whole server for 30 s; separate concern, not addressed here). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5898#issuecomment-6158682432)

### #5875: fix(agent-server): honor UTC offsets in bash event timestamp filters

- Commit: `75dec1d3ef1cf1630502b31e83527e8e3301fb18` (2026-10-17T03:04:38+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5875).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5875). Families: F13.

Stated intent:

> - `BashEventService._timestamp_to_str` now converts aware datetimes to UTC before formatting, matching the UTC file-name prefixes (event timestamps come from `utc_now()`). Naive bounds are still read as UTC.
> - Focused unit test: `+02:00` bounds on both `timestamp__gte` and `timestamp__lt`, plus the naive case.
> - Feature map: the `F13.timestamp-offset` bullet loses its `known bug` label and `# bug` marker, its prose and the matching Gotchas line now describe the fixed behavior.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md`
- `openhands-agent-server/openhands/agent_server/bash_service.py`
- `tests/agent_server/test_bash_service.py`

Linked issue acceptance context:

- [#5870](https://github.com/OpenHands/software-agent-sdk/issues/5870): [Bug]: Bash event timestamp filters ignore the timezone offset (a +02:00 bound lands 2 h late) — current state closed, closed 2026-10-17T01:04:40Z.

> - [ ] `timestamp__gte` / `timestamp__lt` with a non-UTC offset select the same events as the same instant written with `Z`, on the global and conversation-scoped bash search routes. - [ ] A naive (offset-less) bound is still treated as UTC. - [ ] A focused unit test in `tests/agent_server/` covers an offset bound. - [ ] The map bullet `F13.timestamp-offset` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: `BashEventService._timestamp_to_str` (`openhands-agent-server/openhands/agent_server/bash_service.py:71-75`) formats the datetime with `strftime("%Y%m%d%H%M%S%f")` without converting it to UTC. Event file names are built from `event.timestamp` (`utc_now()`, UTC), and the search compares the bound string to those file-name prefixes (lines 192-193), so an aware bound in another offset is compared as local wall time. Fix: convert aware datetimes to UTC in `_timestamp_to_str` before formatting. Related: https://github.com/OpenHands/software-agent-sdk/pull/5842 changes `order__gt` paging in the same file (independent). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5875#issuecomment-6158488952)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5875#pullrequestreview-5515603414): _This review was posted by an AI agent (OpenHands)._ ## Scope This change belongs in this repository: it fixes `openhands-agent-server` bash event filtering behavior, which this repo owns, and updates the repo-owned `verify-agent-server` feature map. No cross-repository ownership issue. ## What I verified on the current head (`ac37f64d`) - **Fix correctness.** `BashEventService._timestamp_to_str` now calls `timestamp.astimezone(UTC)` for aware datetimes before formatting. Event file names are produced from `utc_now()` timestamps, so the UTC-normalized bound now compares correctly against the UTC `YYYYMMDDHHMMSSffffff` prefix. I confirmed base-vs-fixed behavior directly: for an event at `10:00Z` and a `+02:00` bound at `11:00+02:00` (09:00Z), the base implementation formats the bound as `...110000` and excludes the event, while the fixed o

### #5957: fix(typescript-client): type GitChange.status with the server's wire values

- Commit: `5f2185ed63334dca95a54839848d2518f9d7f513` (2026-10-17T03:07:44+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5957).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5957). Families: F34.

Stated intent:

> - `GitChange.status` (`clients/typescript/src/models/workspace.ts`) is now the generated contract's `GitChangeStatus`, so it cannot drift from the server again; `GitChangeStatus` is also exported from the main entry point.
> - Tests: a type-level check in `agent-server-contract-types.test.ts` (exported status equals the generated one, also through `RemoteWorkspace.gitChanges`) and a runtime test in `remote-workspace-git.test.ts` that `gitChanges` returns the wire statuses unchanged.
> - Map: `F34.workspace-git-types` loses its `# bug` marker and "known bug" label; prose and the Gotchas line now describe the fixed type.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `clients/typescript/src/__tests__/agent-server-contract-types.test.ts`
- `clients/typescript/src/__tests__/remote-workspace-git.test.ts`
- `clients/typescript/src/index.ts`
- `clients/typescript/src/models/workspace.ts`

Linked issue acceptance context:

- [#5947](https://github.com/OpenHands/software-agent-sdk/issues/5947): [Bug]: TypeScript GitChange.status is typed 'added|modified|deleted|renamed' but the server sends 'ADDED|UPDATED|DELETED|MOVED' — current state closed, closed 2026-10-17T01:07:46Z.

> - [ ] The exported `GitChange['status']` is `'MOVED' \| 'ADDED' \| 'DELETED' \| 'UPDATED'`, taken from the generated `GitChangeStatus`. - [ ] A unit test in `clients/typescript` checks the exported type against the generated contract and that `gitChanges` returns the wire statuses unchanged. - [ ] The map bullet `F34.workspace-git-types` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: `clients/typescript/src/models/workspace.ts:31` hand-declares lowercase statuses that never matched the server. The server's enum is `GitChangeStatus` in `openhands-sdk/openhands/sdk/git/models.py`, and the generated contract has it at `clients/typescript/src/generated/agent-server-schema.ts:5431`. `GitChange` is re-exported from `clients/typescript/src/index.ts:192` and returned by `RemoteWorkspace.gitChanges` (`src/workspace/remote-workspace.ts:214`). This changes a published type, so consumers that compare against the lowercase values will now get a compile error; those comparisons never matched at runtime. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5957#issuecomment-6159691140)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5957#pullrequestreview-5512715807): _This review was posted by an AI agent (OpenHands)._ ## Correct, minimal fix — approved Re-fetched head `1935c7f` and reviewed the diff against the repo guidance (`AGENTS.md`, `clients/typescript/AGENTS.md`, `.agents/skills/custom-codereview-guide.md`) and linked issue #5947 (open; `bug`, `priority:low`, `ready-for-dev`). **Verified on this head** - `clients/typescript/src/models/workspace.ts` types `GitChange['status']` as the generated `GitChangeStatus` (`'MOVED' \| 'ADDED' \| 'DELETED' \| 'UPDATED'`), matching the SDK enum (`openhands-sdk/openhands/sdk/git/models.py:9`) and the generated contract (`agent-server-schema.ts:5431`). This is the issue's primary acceptance criterion. - `GitChangeStatus` is re-exported from the root entry point (`src/index.ts:193`); no other export of that name exists, so no collision. - Issue reproducer: `co

### #6038: fix: use short tmux sockets for overlong paths

- Commit: `47c50f7e5c6862822990065b6eee23718f5e650e` (2026-10-16T21:11:35-04:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/6038).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-6038). Families: F13, F27, F31, F33.

Stated intent:

> - Use the Agent Server's canonical-byte-length approach to select an explicit short `/tmp` tmux socket path when the original would exceed the platform's Unix-socket limit, without modifying the process-wide `TMUX_TMPDIR`.
> - Keep socket creation locking alongside the selected socket path.
> - Exercise real standalone and pooled terminal sessions under an overlong directory; give shared-socket Agent Server tests short temporary namespaces so they continue covering locking and cleanup.

Changed paths:

- `openhands-tools/openhands/tools/terminal/terminal/tmux_terminal.py`
- `tests/agent_server/test_api.py`
- `tests/tools/terminal/test_tmux_pane_pool.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/6038#issuecomment-6224775724)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/6038#pullrequestreview-5554885018): _This review was posted by an AI agent (OpenHands)._ **Scope.** This belongs in this repository. It changes tmux socket-path selection in `openhands-tools` (the terminal tool this repo owns) and is the direct-terminal counterpart to the Agent Server logic already on `main` (`_ensure_server_tmux_tmpdir` in `openhands-agent-server/openhands/agent_server/api.py`). No cross-repository or product decision is involved. **Verification on head `0870a753` (Linux, tmux 3.5a, real tmux, no mocks).** - Before/after on the real entry point: with `TMUX_TMPDIR` set to a 100-byte directory, `origin/main` raises `libtmux.exc.LibTmuxException: ['error connecting to .../openhands-<32 hex> (File name too long)']` for both `TmuxTerminal.initialize()` and `TmuxPanePool.initialize()`. On this head the same script succeeds: both terminals create a real session, 

### #5973: fix(agent-server): answer 409 and keep state when a write loses ownership

- Commit: `f621dab57be854aaee3a982c83b6bf34b8ce5705` (2026-10-17T03:13:03+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5973).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5973). Families: F01, F03, F04, F05, F06, F07, F08, F09, F10, F11, F12, F30, F31, F32.

Stated intent:

> - `EventService.save_meta(stored=None)` now takes the new `StoredConversation`, writes it under the lease guard, and makes it `self.stored` only after the write succeeded (persist first, as the issue suggests). `update_conversation`, `AutoTitleSubscriber` and `update_secrets` build a `model_copy` and pass it in; `update_conversation` applies the `ConversationState.tags` update only after the save.
> - `ConversationOwnershipLostError` maps to **409 Conflict** with `detail` "Conversation is owned by another agent server instance; the write was refused" (next to the existing 409 for credential-binding activation). This also turns the stale owner's refused `POST .../events` from 500 into 409.
> - The `F31.fenced-write-no-trace` map bullet drops its `known bug` marker, now asserts the status is 409, and the F31 gotcha about stale owners answering 500 is updated.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md`
- `openhands-agent-server/openhands/agent_server/api.py`
- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `openhands-agent-server/openhands/agent_server/event_service.py`
- `tests/agent_server/test_auto_title_span_metadata.py`
- `tests/agent_server/test_conversation_service.py`
- `tests/agent_server/test_unhandled_exception_error_id.py`

Linked issue acceptance context:

- [#4800](https://github.com/OpenHands/software-agent-sdk/issues/4800): [Bug]: ConversationService: stored fields mutated in memory before save_meta() with no rollback on failure — current state closed, closed 2026-10-17T01:13:06Z.

> - [ ] `set_conversation_tag` leaves `stored.tags` unchanged when `save_meta()` raises - [ ] `delete_conversation_tag` leaves `stored.tags` unchanged when `save_meta()` raises - [ ] `update_conversation` leaves `stored.title` / `stored.tags` unchanged when `save_meta()` raises - [ ] `AutoTitleSubscriber` leaves `stored.title` unchanged when `save_meta()` raises - [ ] A write refused because conversation ownership was lost returns an appropriate 4xx rather than an unhandled 500. - [ ] After that rejected write, GET still reports the prior title/tags, and the persisted metadata is unchanged. The cleanest fix is reversing the order — write to disk first, then update in-memory state — rather than adding rollback logic after the fact. Not a blocker, but worth addressing consistently across all affected sites rather than piecemeal.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5973#issuecomment-6160438950)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5973#pullrequestreview-5522877638): _This review was posted by an AI agent (OpenHands)._ ## Summary The fix correctly reverses the mutate-then-persist ordering that let a refused write leak into in-memory metadata. `EventService.save_meta(stored=None)` now serializes a supplied `StoredConversation` under the lease guard and adopts it as `self.stored` only after the write succeeds; `update_conversation`, `AutoTitleSubscriber` and `apply_resume_secrets` build a `model_copy` and pass it in. `update_conversation` also moves the `ConversationState.tags` mutation after the save, so a lost-lease `PATCH` leaves both `meta.json` and the state untouched. `ConversationOwnershipLostError` now maps to 409 instead of falling through to the generic 500 handler. Verification against the current head: - Scope: this is an agent-server bug fix (conversation metadata persistence + REST error m

### #5956: fix(typescript): resolve updateConversation with the updated conversation

- Commit: `8cfff960ff3b9de94d9346eac9802405d6af0bed` (2026-10-17T03:21:58+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5956).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5956). Families: F34.

Stated intent:

> - `ConversationManager.updateConversation` and `ConversationClient.updateConversation` type the PATCH as `Success` and resolve with the conversation read back via `getConversation`.
> - New unit test for both methods; the existing `ConversationClient` wrapper test accounts for the extra `GET`.
> - Map: `F34.manager-update-result` loses its `# bug` marker and "known bug" label; the prose and the Gotchas line now describe the read-back.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `clients/typescript/src/__tests__/api-clients.test.ts`
- `clients/typescript/src/client/conversation-client.ts`
- `clients/typescript/src/conversation/conversation-manager.ts`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5946](https://github.com/OpenHands/software-agent-sdk/issues/5946): [Bug]: TypeScript updateConversation is typed to return ConversationInfo but resolves with {success: true} — current state closed, closed 2026-10-17T01:22:00Z.

> - [ ] `ConversationManager.updateConversation` and `ConversationClient.updateConversation` resolve with a value that matches their declared return type. - [ ] The title is still stored through `PATCH /api/conversations/{id}`; the server's response contract is unchanged. - [ ] A unit test in `clients/typescript/src/__tests__` covers the resolved value. - [ ] The map bullet `F34.manager-update-result` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `clients/typescript/src/conversation/conversation-manager.ts:384-393`: `updateConversation(...): Promise<ConversationInfo>` calls `this.client.patch<ConversationInfo>(...)` and returns `response.data`. - `clients/typescript/src/client/conversation-client.ts:430-439`: `updateConversation<TConversation = ConversationInfo>(...)` does the same. - The server answers `Success` (`openhands-agent-server/openhands/agent_server/conversation_router.py`, `PATCH /api/conversations/{conversation_id}`), and `Success` is the response type in `clients/typescript/src/generated/agent-server-schema.ts`. Two ways to make it consistent: keep the declared type and read the conversation back after the PATCH (one extra GET, no signature change), or change the declared type to `Success` (no extra request, but a type-level break for callers that pass a type argument or read the result as a conversation). Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5956#issuecomment-6159731776)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5956#pullrequestreview-5512668118): _This review was posted by an AI agent (OpenHands)._ ## Scope This is the right repository for the change. `clients/typescript/` mirrors the Agent Server API (root `AGENTS.md`, `clients/typescript/AGENTS.md`), and the fix stays inside the client library: it types the `PATCH /api/conversations/{id}` response as the `Success` the generated contract actually declares and resolves with the conversation read back via `getConversation`. No server contract change, no repository move, no product/architecture decision needed to proceed. ## Findings I found no material correctness, security, compatibility, or acceptance-criterion defect on this head. - Both `ConversationManager.updateConversation` and `ConversationClient.updateConversation` now send the `PATCH` typed `Success` and resolve with `getConversation(id)` (`conversation-manager.ts:390-391

### #5968: fix(agent-server): answer 409 when a continued gateway run waits for confirmation

- Commit: `804498a61126a90917efebb5c2c25defb53f2cf0` (2026-10-17T03:23:48+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5968).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5968). Families: F30.

Stated intent:

> - `_wait_for_reused_conversation_completion` now also returns on `paused` and `waiting_for_confirmation`. It keeps the same guard (the run was seen `running`, or new events arrived), so a status left over from the previous turn does not end the wait early. `_raise_for_terminal_error` then answers 409 `Agent run ended with status: <status>`, the same answer a new conversation gets.
> - New unit test `tests/agent_server/test_openai_gateway_reused_wait.py` covers both statuses answering 409 at once, and the stale-status guard.
> - Map bullet `F30.chat-reuse-confirmation`: removed the `# bug` marker and the "known bug" label, tightened `--expect 409,504` to `409`, and rewrote the prose as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F30-openai-gateway.md`
- `openhands-agent-server/openhands/agent_server/openai/service.py`
- `tests/agent_server/test_openai_gateway_reused_wait.py`

Linked issue acceptance context:

- [#5962](https://github.com/OpenHands/software-agent-sdk/issues/5962): [Bug]: A continued OpenAI-gateway conversation that stops in waiting_for_confirmation (or paused) answers 504 after 120 s instead of 409 — current state closed, closed 2026-10-17T01:23:50Z.

> - [ ] A continued gateway conversation whose run stops in `waiting_for_confirmation` answers 409 with detail `Agent run ended with status: waiting_for_confirmation` within seconds; same for `paused`. - [ ] A conversation that was already `waiting_for_confirmation` or `paused` before the new turn starts running does not end the wait early (the guard against a stale status from the previous turn still applies). - [ ] Finished, error and stuck runs keep their current answers (200, 500, 500), and runs longer than 120 s still answer 504. - [ ] A unit test covers the continued-conversation wait returning on `waiting_for_confirmation` and `paused`. - [ ] The map bullet `F30.chat-reuse-confirmation` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: on the continued path `_run_agent` waits in `_wait_for_reused_conversation_completion` (`openhands-agent-server/openhands/agent_server/openai/service.py:343`), which returns only when `last_status.is_terminal()` (line 359): finished, error, stuck. `paused` and `waiting_for_confirmation` are not terminal, so the loop polls until the 120 s deadline and raises 504 (line 363). The 409 branch of `_raise_for_terminal_error` (line 379) is therefore unreachable on this path. New conversations wait on the run task (`EventService.wait_for_run_completion`), which ends when the run stops for confirmation, so they reach the 409. Related: map bullets `F30.chat-reuse-concurrent` (https://github.com/OpenHands/software-agent-sdk/pull/5948) and `F30.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5968#issuecomment-6159965900)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5968#pullrequestreview-5512793603): _This review was posted by an AI agent (OpenHands)._ Scope: this change belongs in this repository. It modifies agent-server gateway behavior (`openhands-agent-server/openhands/agent_server/openai/service.py`), its feature map, and a test under `tests/agent_server/`, all within the Agent Server ownership described in the root and `openhands-agent-server` `AGENTS.md`. ## Assessment The root cause matches the linked issue: `_wait_for_reused_conversation_completion` returned only on `is_terminal()`, so `paused` and `waiting_for_confirmation` were never observed on the continued path, the `409` branch of `_raise_for_terminal_error` was unreachable, and the request fell through to the fixed 120 s deadline as a `504`. The change adds `_STOPPED_RUN_STATUSES` (`PAUSED`, `WAITING_FOR_CONFIRMATION`) and returns on those statuses under the existing 

### #5965: fix(sdk): deliver streamed deltas to RemoteConversation token_callbacks

- Commit: `b301ee008178ec4a357d3c2bc6370495c6ce1e3f` (2026-10-17T03:27:19+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5965).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5965). Families: F33.

Stated intent:

> - `RemoteConversation.__init__` accepts `token_callbacks` (added as the last named parameter, so positional callers are unaffected) and passes it to `_initialize_connection`.
> - When token callbacks are given, a small event callback converts each `StreamingDeltaEvent` from the socket into an `LLMStreamChunk` (`choices[0].delta.content` / `reasoning_content`), the same type a local conversation's token callbacks receive. Nothing changes when no token callbacks are passed; event callbacks and `state.events` are untouched.
> - Map: drops the `# bug` marker from `F33.conversation-token-callbacks` and rewrites its prose and the matching Gotchas line.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`
- `openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`
- `tests/sdk/conversation/remote/test_remote_conversation.py`

Linked issue acceptance context:

- [#4671](https://github.com/OpenHands/software-agent-sdk/issues/4671): [Epic] Streaming: separate the wire format from the durable event record — current state open, closed —.

> - [ ] Step 0 baselines landed (#4679) — the byte budget is derived from a measured frame-size distribution, not a guess. - [ ] Step 1 independent fixes landed: #4674 · #4675 · #4676 · #4677 · #4678 · #4077. - [ ] Step 1b landed (#4696) — `StreamingDeltaEvent` is not an `Event`, deltas have their own fan-out, the frame on the wire is unchanged, and `receives_streaming_deltas` is gone. - [x] The runtime idle timer is reset during a long stream that produces no durable events (#4695), and stays so after step 1b. - [x] Step 2 landed (#4680) — `append_event` returns its sequence number and publish happens after persist. - [x] Steps 3+4 landed (#4681) — `/sockets/session/{id}` serves durable events in a non-`Event` envelope, with cursor, paged replay and byte-budget admission. - [x] Step 5 landed (#4682) — `StreamContext` mints the identity; every `ItemStarted` is retired by exactly one `Durable` or `ItemAborted`. - [ ] Step 6 landed (OpenHands/OpenHands#16965) — canvas keys slots by event id and `handle-event-for-ui.ts` contains no text comparison. - [ ] Step 7 landed (#4683) — `StreamingDeltaEvent` is deleted outright and the old endpoint is documented as deprecated. - [ ] `Event` is unchanged on disk throughout: same class, same `extra="forbid"`, same bytes.

- [#4683](https://github.com/OpenHands/software-agent-sdk/issues/4683): Streaming step 7: stop emitting StreamingDeltaEvent and freeze the legacy events endpoint — current state open, closed —.

> - [ ] `StreamingDeltaEvent` has no construction site. - [ ] A webhook receives the same event count for a streamed and non-streamed conversation, with no filter in the path. - [ ] The legacy endpoint is documented as deprecated with a removal target.

- [#5959](https://github.com/OpenHands/software-agent-sdk/issues/5959): [Bug]: Conversation(token_callbacks=[...]) with a RemoteWorkspace silently ignores the callbacks — current state closed, closed 2026-10-17T01:27:21Z.

> - [ ] `RemoteConversation` accepts `token_callbacks` and calls them once per `StreamingDeltaEvent` received on the events socket, with an `LLMStreamChunk` carrying the delta's `content` and `reasoning_content`. - [ ] Without `token_callbacks`, behaviour is unchanged; event callbacks still receive the `StreamingDeltaEvent` frames. - [ ] A unit test under `tests/sdk/conversation/remote/` covers the forwarding. - [ ] The map bullet `F33.conversation-token-callbacks` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `openhands-sdk/openhands/sdk/conversation/conversation.py:200` passes `token_callbacks=token_callbacks` to `RemoteConversation`. - `RemoteConversation.__init__` (`openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py:724`) has no `token_callbacks` parameter; it lands in `**_: object` (line 749) and is never read. `_initialize_connection` (line 1053) wires only event callbacks to the `WebSocketCallbackClient`. - The server publishes the deltas from its own token callback (`openhands-agent-server/openhands/agent_server/event_service.py:1244`, `_token_streaming_callback`), so the data is already on the socket; the client only has to hand each `StreamingDeltaEvent` to the token callbacks. Related: the streaming epic https://github.com/OpenHands/software-agent-sdk/issues/4671 and its step 7 https://github.com/OpenHands/software-agent-sdk/issues/4683 (which will retire `StreamingDeltaEvent` on the legacy socket; the client-side forwarding should then

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5965#issuecomment-6159914119)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5965#pullrequestreview-5512798385): _This review was posted by an AI agent (OpenHands)._ Reviewed at head `bc379ab41c0254ad70e9a3582e2e7cf52bedd937` (unchanged from the review request). **Scope.** This belongs in this repository: it fixes the SDK's `RemoteConversation` (the factory path that `Conversation(agent=..., workspace=RemoteWorkspace(...))` uses), inside the Python SDK's conversation layer. No cross-repo move or product decision is needed. **What I verified on the current head.** - The bug and the fix are consistent with the code: `Conversation(...)` already forwards `token_callbacks` (factory line ~200), and `RemoteConversation.__init__` previously swallowed it in `**_`. The head now accepts `token_callbacks`, threads it through `_initialize_connection`, and appends one adapter event callback that converts each `StreamingDeltaEvent` from the events socket into an `

### #5877: fix(ts-client): match agent-profile payload types to the server models

- Commit: `4fedb7e2b524b5f6d2d5d0efa9be3c03e072da40` (2026-10-17T02:01:50Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5877).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5877). Families: F34.

Stated intent:

> - `clients/typescript/src/models/agent-profile.ts`: declare the fields the server's Pydantic models send (`openhands-sdk/openhands/sdk/profiles/agent_profile.py`, `resolver.py`), and remove `skills` and `resolved_mcp_servers`. `AgentProfileSaveInput` therefore no longer offers `skills`.
> - `agent-server-contract-types.test.ts` asserts `keyof` equality between the four exported types and their generated contract counterparts. The fixtures in `derive-switch-plan.test.ts` and `api-clients.test.ts` are updated, and the now-removed `skills: unknown[]` entry is dropped from `config/public-type-budget.json`.
> - Map bullet `F21.ts-payload-types`: the `# bug` marker is removed and the prose rewritten as expected behaviour. The bullet also gains the `Requires: F21.save-create, F21.acp-kind` line it lacked: run with `--only` alone, it got a 404 for the profiles those bullets create. The generated-contract positive control is unchanged.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F21-agent-profiles.md`
- `clients/typescript/config/public-type-budget.json`
- `clients/typescript/src/__tests__/agent-server-contract-types.test.ts`
- `clients/typescript/src/__tests__/api-clients.test.ts`
- `clients/typescript/src/__tests__/derive-switch-plan.test.ts`
- `clients/typescript/src/models/agent-profile.ts`

Linked issue acceptance context:

- [#5872](https://github.com/OpenHands/software-agent-sdk/issues/5872): [Bug]: TypeScript client's agent-profile payload types do not match what the server sends — current state closed, closed 2026-10-17T02:01:53Z.

> - [ ] `AgentProfileDiagnostics` declares every field of the server's `AgentProfileDiagnostics` model and no longer declares `resolved_mcp_servers`. - [ ] `OpenHandsAgentProfile` declares `disabled_skills`, `meta_profile_ref` and `enable_classify_and_switch_llm_tool`, and no longer requires (or offers, via `AgentProfileSaveInput`) `skills`. - [ ] `ACPAgentProfile` declares `acp_startup_timeout`. - [ ] A unit test in `clients/typescript` pins the field lists; lint, typecheck and tests pass. - [ ] The map bullet `F21.ts-payload-types` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: the hand-written mirror was not updated when the server models changed. - `clients/typescript/src/models/agent-profile.ts:68` (`skills: unknown[]`), `:80-89` (`ACPAgentProfile` without `acp_startup_timeout`), `:133-160` (`AgentProfileDiagnostics` with `resolved_mcp_servers` at `:148`). - Server models: `openhands-sdk/openhands/sdk/profiles/agent_profile.py:150` (`OpenHandsAgentProfile`, `disabled_skills`, `enable_classify_and_switch_llm_tool`, `meta_profile_ref`), `:270` (`ACPAgentProfile`, `acp_startup_timeout`), `:96` (`extra="forbid"`, hence the 422 on `skills`); `openhands-sdk/openhands/sdk/profiles/resolver.py:86` (`AgentProfileDiagnostics`). - Related: #4018 (replaced `skill_refs`/`skills` with `disabled_skills`), #3722/#3723 (original TS types). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5877#issuecomment-6158490190)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5877#pullrequestreview-5516546531): _This review was posted by an AI agent (OpenHands)._ **Scope.** Belongs in this repository: `clients/typescript/` mirrors the Agent Server API, and the `verify-agent-server` feature map is owned here. Linked issue #5872 is open and currently carries `bug`, `priority:low`, `ready-for-dev`, so the work is in scope and ready. **Verified on head `221c82541ffd7c72ccb884b530d181b4b1e59cad`:** - The key sets of the four hand-written types now equal their generated counterparts (`OpenHandsAgentProfile`, `ACPAgentProfile`, `AgentProfileDiagnostics`, `AgentProfileSummary` vs `OpenHandsAgentProfile`/`AcpAgentProfile`/`AgentProfileDiagnostics`/`AgentProfileInfo`) — compared programmatically, no extra or missing keys. - The new assertion is what makes them match: on `origin/main`'s `agent-profile.ts` the same test file fails with three `TS2344` errors

### #5868: fix(agent-server): guard LLM profile delete through the .json alias

- Commit: `8ee5cba234d14f7841b52c3c8259a884a9179fdb` (2026-10-17T04:12:49+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5868).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5868). Families: F04, F06, F07, F09, F20, F21, F22, F23, F30, F33, F34.

Stated intent:

> - Add `canonical_profile_name()` in `llm_profile_store.py` (used by `_get_profile_path`). `_scan_referrers` / `_rewrite_refs` in `profile_refs.py` compare and write the canonical key.
> - `profiles_router.py`: `_set_active_profile_if_matches` compares canonical keys and stores the canonical new name, and activation records the canonical name.
> - Feature map: drop the `F20.json-alias-guard` known-bug marker, assert 409 and survival directly, and update the prose and the Gotchas line.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F20-llm-profiles.md`
- `openhands-agent-server/openhands/agent_server/profiles_router.py`
- `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`
- `openhands-sdk/openhands/sdk/profiles/profile_refs.py`
- `tests/agent_server/test_profiles_router.py`
- `tests/sdk/profiles/test_profile_refs.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5861](https://github.com/OpenHands/software-agent-sdk/issues/5861): [Bug]: DELETE /api/profiles/{name}.json deletes an LLM profile that an agent profile still cites (plain name answers 409) — current state closed, closed 2026-10-17T02:12:51Z.

> - [ ] `DELETE /api/profiles/<name>.json` answers `409` naming the referrers whenever `DELETE /api/profiles/<name>` would, and the profile survives. - [ ] Deleting through the alias clears `active_profile` when it names that profile; renaming through the alias moves `active_profile` and cascades `llm_profile_ref` like the plain name. - [ ] A unit test covers the alias in the FK guard (`openhands-sdk/openhands/sdk/profiles/profile_refs.py`) and the router. - [ ] The map bullet `F20.json-alias-guard` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `LLMProfileStore._get_profile_path` strips a trailing `.json` (`openhands-sdk/openhands/sdk/llm/llm_profile_store.py:166`), so `qa-alias.json` addresses the file `qa-alias.json` of profile `qa-alias`. - `_scan_referrers` compares `llm_profile_ref` with the raw name (`openhands-sdk/openhands/sdk/profiles/profile_refs.py:66`); it is used by `delete_llm_profile` and by the rename cascade (`_rewrite_refs`). - `_set_active_profile_if_matches` compares `active_profile` with the raw name (`openhands-agent-server/openhands/agent_server/profiles_router.py:136`), called from delete and rename. Related: the agent-profile store has the same suffix mismatch for its own identity (map ID `F21.json-suffix-identity`), which is a separate fix. Found by the first full agentic run of verify-agent-server; tracked in #5653.

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5868#issuecomment-6158368393)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5868#pullrequestreview-5514387998): _This review was posted by an AI agent (OpenHands)._ ## Scope The change is in-repo and correctly owned: it fixes the Agent Server REST profile routes (`openhands-agent-server/openhands/agent_server/profiles_router.py`) and the SDK profile stores/FK helpers (`openhands-sdk/openhands/sdk/llm/llm_profile_store.py`, `openhands-sdk/openhands/sdk/profiles/profile_refs.py`), which is exactly what `AGENTS.md` assigns to this repository. No cross-repository move or maintainer architecture decision is needed. ## Verification (current head `1d9fd297`) - Re-read the PR: still open, not a draft, head unchanged, no labels, no prior reviews or review threads on this head, no non-bot issue comments. Linked issue #5861 is open with `bug`/`priority:low`/`ready-for-dev`, and its expected behavior (alias delete to 409, alias rename/delete moves `active_prof

### #5953: fix(agent-server): post webhook events that fill the buffer during an in-flight batch

- Commit: `e785defbf652f9c819d53d5ab8ea8eeb8332a942` (2026-10-17T04:43:42+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5953).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5953). Families: F04, F05, F10, F12, F31, F32.

Stated intent:

> - `WebhookSubscriber._post_events` re-checks the buffer after draining what was queued when it started, and keeps posting while it is full again (count or `max_batch_bytes`). Still one request in flight; a failed POST still requeues and leaves the rest to the flush timer. Once the queue drains, a flush timer started during the POST is cancelled (not when `_post_events` runs inside that timer). `__call__` is unchanged apart from sharing the new `_buffer_full()` check.
> - New unit test `test_events_published_during_post_are_sent_once_buffer_is_full` (blocks the first POST, publishes two events, asserts two batches and no running timer without `close()`).
> - Map: the `F32.buffer-stranded` bullet loses its `# bug` marker and "known bug" label; its prose, the `F32.flush-on-close` prose and the Gotchas entry now describe the fixed behaviour. The bullet's line `sink read ... --expect-min 1 --expect-max 1` (exactly one batch so far) asserted the buggy state, so it is dropped; `F32.buffer-size` already checks the first batch.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`
- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `tests/agent_server/test_webhook_subscriber.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5952](https://github.com/OpenHands/software-agent-sdk/issues/5952): [Bug]: Webhook events published during an in-flight batch POST wait for flush_delay although the buffer is full — current state closed, closed 2026-10-17T02:43:44Z.

> - [ ] Events that fill the buffer (by count or `max_batch_bytes`) while a batch POST is in flight are posted as soon as that POST returns, without waiting for `flush_delay` or `close()`. - [ ] Still at most one webhook request in flight per subscriber; a failed POST still leaves the events queued for the flush timer. - [ ] A unit test in `tests/agent_server/test_webhook_subscriber.py` covers events arriving during a blocked POST, without calling `close()` before counting. - [ ] The map bullet `F32.buffer-stranded` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/conversation_service.py` (`WebhookSubscriber`): - `__call__` (line 2587): when the buffer is full but `_post_lock` is held (line 2595), it only starts the flush timer and returns. - `_post_events` (line 2613) drains only `events_remaining = len(self.queue)` taken when it starts (line 2618), so events enqueued during the POST are left behind. The existing test `test_concurrent_event_delivery_has_one_request_in_flight` calls `close()` before counting, which hides it. Related: the slow-receiver item in the same family (`F32.slow-receiver`), about the inline POST holding the publish future. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5953#issuecomment-6159631841)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5953#pullrequestreview-5512854204): _This review was posted by an AI agent (OpenHands)._ ## Summary This PR fixes a real, well-scoped webhook batching bug in the agent-server: events published while a batch POST is in flight were queued but not posted when that POST returned, even when they refilled the buffer, so they waited for `flush_delay` (30 s default), the next burst, or the conversation's close. ## Verification performed - **Scope**: The change lives in `openhands-agent-server/openhands/agent_server/conversation_service.py` (WebhookSubscriber), which this repository owns. It stays inside the reported bug and does not touch REST/WebSocket contracts, persisted settings, or other public surfaces. - **Correctness**: `_post_events` now re-checks `_buffer_full()` after draining and keeps posting while the buffer is full again, still serialized behind `_post_lock` (one req
- [discussion; comment; enyst](https://github.com/OpenHands/software-agent-sdk/pull/5953#issuecomment-6228640677): There’s a remaining issue here, which is a bit of an edge case, but seems possible. From an Astra conversation: > [P2] A failed follow-up batch can be left without a retry timer > > The sequence is: > > 1. Events arrive during the first POST, filling the buffer and starting a flush timer. > 2. The PR immediately sends them in a second POST, temporarily emptying the queue. > 3. The timer expires during that POST, sees an empty queue, and exits. > 4. The POST exhausts its retries. Its events are requeued, but no timer remains to deliver them. > > They can then sit indefinitely until another event triggers delivery or the conversation closes. > > I reproduced this with the actual subscriber and a local HTTP receiver returning a delayed 503: > > * Base: automatically retries the failed batch and empties the queue. > * PR: leaves two events qu

### #5848: fix(sdk): list git changes of a repository subdirectory relative to it

- Commit: `14f821bb2af5439bdcdb84193b470739e68d971f` (2026-10-17T05:10:16+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5848).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5848). Families: F15.

Stated intent:

> - `get_changes_in_repo` (`openhands-sdk/openhands/sdk/git/git_changes.py`) passes `--relative` to `git diff --name-status`, so tracked changes are scoped to and relative to the requested directory, like `git ls-files --others`. A repository root is unaffected (`--relative` from the top level is a no-op).
> - Test: `get_changes_in_repo` on `src/` of a repository with changes inside and outside it (modified, deleted, untracked).
> - verify-agent-server map: `F15.changes-subdir-paths` loses its `# bug` marker and "known bug" label; the check now asserts `["app.py", "new.py"]` resolving against `src/` only, and the prose describes the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F15-git.md`
- `openhands-sdk/openhands/sdk/git/git_changes.py`
- `tests/sdk/git/test_git_changes.py`

Linked issue acceptance context:

- [#5843](https://github.com/OpenHands/software-agent-sdk/issues/5843): [Bug]: GET /api/git/changes for a repository subdirectory mixes repository-relative and subdirectory-relative paths — current state closed, closed 2026-10-17T03:10:18Z.

> - [ ] `GET /api/git/changes` (global and conversation-scoped) for a subdirectory of a repository returns only changes under that directory, every path relative to it (tracked, deleted and untracked alike). - [ ] The repository root, and nested repositories listed under their directory name, are unchanged. - [ ] `tests/sdk/git/test_git_changes.py` covers `get_changes_in_repo` on a subdirectory. - [ ] The map bullet `F15.changes-subdir-paths` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-sdk/openhands/sdk/git/git_changes.py`, `get_changes_in_repo`: - tracked changes come from `git diff --name-status <ref>` (line 184), which always prints repository-relative paths for the whole repository regardless of the working directory; - untracked files come from `git ls-files --others --exclude-standard` (line 197), which is scoped to and relative to the working directory. Suggested fix: add `--relative` to the `git diff --name-status` call, which limits the diff to the working directory and prints paths relative to it, the same base as `ls-files`. This keeps the requested-directory base that `get_git_changes` already uses for nested repositories (`Path(git_dir) / change.path`) and that `GET /api/git/diff` uses for its path. Related: #5641 (repository below the requested directory, `F15.changes-repo-below-path`), which changes `get_git_changes`, not `get_changes_in_repo`. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5848#issuecomment-6157982385)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5848#pullrequestreview-5511816320): _This review was posted by an AI agent (OpenHands)._ ## Review of `fix(sdk): list git changes of a repository subdirectory relative to it` Scope: `get_changes_in_repo` now passes `--relative` to `git diff --name-status`, plus a regression test and a verify-agent-server map update. The change belongs in this repository (SDK git behavior) and matches the acceptance criteria of the linked issue #5843 (`bug`, `priority:low`, `ready-for-dev`). ### Verification performed on head `89557e0` - **Root behavior unchanged.** From the repository root, `git diff --name-status --relative HEAD` is a no-op (I confirmed identical output with and without `--relative`), so `path=<repo>` keeps returning repository-relative paths for the whole repository. - **Subdirectory behavior fixed.** From `src/` with a modified `src/app.py`, deleted `src/old.py`, untrack

### #5967: fix(agent-server): report real usage buckets in telemetry outcome events

- Commit: `9af99982a1250b1b5d5095a5a49df4107fc18e14` (2026-10-17T05:11:24+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5967).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5967). Families: F32.

Stated intent:

> - `TelemetrySubscriber` now records usage from every state update that carries stats: the `key="stats"` updates `EventService` publishes after each LLM call, and `full_state` snapshots. Every terminal emission (`finished`, failure statuses, and the `interrupted` fallback from `close()`) buckets the latest totals; raw figures are still never sent and the subscriber stays total and non-blocking.
> - New unit tests: `test_outcome_reports_usage_from_the_live_event_order` (baseline snapshot, `running`, `stats`, bare `finished`; fails on main with `unknown`) and `test_interrupted_fallback_reports_usage_seen_before_close` (the triage's `close()` criterion).
> - Map: `F32.telemetry-usage-buckets` loses its `# bug` marker and "known bug" label, the prose and Gotchas describe the fixed behaviour. `F32.telemetry-lifecycle` and `F32.telemetry-conversation` now declare `Requires:` on the earlier telemetry recipes that create the `qa-f32-telemetry` sink and grant consent, so `--only F32.telemetry-usage-buckets` is self-contained (without it the prerequisite failed with "No HTTP sink named qa-f32-telem

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`
- `openhands-agent-server/openhands/agent_server/telemetry/subscriber.py`
- `tests/agent_server/telemetry/test_telemetry_subscriber.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5961](https://github.com/OpenHands/software-agent-sdk/issues/5961): [Bug]: Telemetry conversation_finished always reports total_tokens_bucket and cost_bucket "unknown" for real runs — current state closed, closed 2026-10-17T03:11:26Z.

> - [ ] For a live run (stats updates after each LLM call, then a bare-string `execution_status` terminal update, then the final `full_state` snapshot), `conversation_finished` / `conversation_failed` report real `total_tokens_bucket` and `cost_bucket` values. - [ ] A terminal `full_state` snapshot carrying stats still yields the same buckets (existing `test_outcome_reports_real_bucketed_usage`). - [ ] Raw token counts and costs are still never sent; the subscriber stays total (never raises) and non-blocking. - [ ] A unit test in `tests/agent_server/telemetry/test_telemetry_subscriber.py` covers the live event order. - [ ] The map bullet `F32.telemetry-usage-buckets` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/telemetry/subscriber.py`: - `_handle_state_update` (line 118) calls `_capture_usage(event)` only on the event that carries the first terminal status (lines 134-136), and `_emit_terminal` sends the outcome immediately. - In a live run that event is the `execution_status` key update fired by `ConversationState`'s state-change callback (`openhands-sdk/openhands/sdk/conversation/state.py:641`), whose `value` is a bare string. `_capture_usage` (line 189) only reads `stats` from a dict `value` (lines 199-203), so it returns without setting `_total_tokens` / `_total_cost`. - The final `full_state` snapshot, which does carry `stats`, is published later by `EventService` (`openhands-agent-server/openhands/agent_server/

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5967#issuecomment-6159964083)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5967#pullrequestreview-5513083389): _This review was posted by an AI agent (OpenHands)._ ## Scope In-repo change: `openhands-agent-server/openhands/agent_server/telemetry/subscriber.py`, its unit tests, and the `verify-agent-server` F32 map. Linked issue #5961 is `bug` / `ready-for-dev`, and this repository owns the agent-server telemetry path, so the change belongs here and needs no cross-repo decision. ## What the fix does `_capture_usage` now runs for every `ConversationStateUpdateEvent` and discriminates on `key`: a `key="stats"` update (published by `EventService._setup_stats_streaming` after each LLM call) supplies the stats dict directly, a `key="full_state"` snapshot supplies `value["stats"]`, and other keys return early. Totals are therefore recorded before the terminal status, so `_emit_terminal` — for `finished`, failure statuses, and the `interrupted` fallback i

### #5964: fix(agent-server): describe the secret-key fallback of TelemetrySpec.salt

- Commit: `7d459bcb1111e42cb2e8059d26bc6354ca6cb510` (2026-10-17T05:12:19+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5964).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5964). Families: F01, F02, F03, F29, F31, F32.

Stated intent:

> - Rewrite the `TelemetrySpec.salt` description to match the code: salt, else the secret key (defaulting to the first session API key, so refs are linkable across restarts until that key changes), else a random per-process key. No behaviour change. The README's `OH_TELEMETRY_SALT` row already said this.
> - Add a unit test that ties the description to `build_telemetry_sink`'s behaviour (refs equal `pseudonymize(id.bytes, secret_key)` when no salt is set).
> - Drop the known-bug marker from the `F32.telemetry-salt-docs` map bullet and update its prose and the matching Gotchas line.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md`
- `openhands-agent-server/openhands/agent_server/config.py`
- `tests/agent_server/telemetry/test_telemetry_disabled_by_default.py`

Linked issue acceptance context:

- [#5960](https://github.com/OpenHands/software-agent-sdk/issues/5960): [Bug]: TelemetrySpec.salt description promises a per-process random fallback, but conversation refs are keyed by OH_SECRET_KEY and linkable across restarts — current state closed, closed 2026-10-17T03:12:21Z.

> - [ ] `TelemetrySpec.salt`'s description names the `OH_SECRET_KEY` fallback (and the session-key default behind it), says refs are then stable across restarts, and mentions the random per-process key only for the case with no secret key. - [ ] No behaviour change in `build_telemetry_sink` or `DiagnosticEventFactory`; `F32.telemetry-salt-and-kind` still passes. - [ ] The map bullet `F32.telemetry-salt-docs` passes with its `# bug` marker removed. The committed TypeScript contract (`clients/typescript/src/generated/agent-server-schema.ts`) is generated from the pinned released image and CI's `check:agent-server-api` fails on any other content, so it is not edited in the fix; it picks up the new text at the next agent-server version bump. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: the description at `openhands-agent-server/openhands/agent_server/config.py:203-209` (`TelemetrySpec.salt`). The code: `openhands-agent-server/openhands/agent_server/telemetry/service.py:111-119` (salt, else `config.secret_key`), `openhands-agent-server/openhands/agent_server/config.py:57-68` (`_default_secret_key` falls back to the first session API key), `openhands-agent-server/openhands/agent_server/telemetry/factory.py:100-101` (random key only when no salt reaches the factory). Documentation only; trivial severity. Related: https://github.com/OpenHands/software-agent-sdk/issues/5936 (same kind of stale description on `WebhookSpec`). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5964#issuecomment-6159897167)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5964#pullrequestreview-5512903049): _This review was posted by an AI agent (OpenHands)._ Scope fits this repository: `openhands-agent-server` owns the agent-server's OpenAPI contract, including its field descriptions. The change is documentation plus a regression test; it touches no behavior. Verification against the current head (`4dac7ef`): - The new `TelemetrySpec.salt` text matches the code. `build_telemetry_sink` (`openhands-agent-server/openhands/agent_server/telemetry/service.py`) passes `spec.salt`, else `config.secret_key`, into `DiagnosticEventFactory`, and `_default_secret_key` (`config.py`) falls back to the first session API key; the factory only generates a random key when neither is set. The rewritten description states exactly that order, and the `None` default and `SecretStr \| None` type are unchanged. - The added test `test_salt_falls_back_to_the_secret_k

### #5958: fix(agent-server): reject a create whose agent tool module cannot be imported

- Commit: `615759cc3497c8e4a8037a65c3f960da50156b65` (2026-10-17T05:16:33+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5958).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5958). Families: F04, F05, F08, F09, F10, F11, F12, F31, F32.

Stated intent:

> - `_create_conversation` remembers which `tool_module_qualnames` modules failed to import; once the agent is launched, if the agent uses one of those tools and it is not otherwise resolvable, it raises `UnavailableAgentTool`, which the router maps to `422`. A module whose tool the agent does not use is still only a warning.
> - Map: `F33.custom-tool-module-unimportable` drops its known-bug marker and now asserts the `422`; the F33 gotcha and the F27 `custom-tool-modules` prose are updated.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md`
- `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`
- `openhands-agent-server/openhands/agent_server/conversation_router.py`
- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `tests/agent_server/test_conversation_router.py`
- `tests/agent_server/test_conversation_service.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5940](https://github.com/OpenHands/software-agent-sdk/issues/5940): [Bug]: Conversation whose agent names a tool from a module the server cannot import is created, then every message is a 500 — current state closed, closed 2026-10-17T03:16:35Z.

> - [ ] Starting a conversation whose agent uses a tool from a `tool_module_qualnames` module the server cannot import (and that is not otherwise resolvable) fails with a 4xx naming the tool and the module; no conversation is created. - [ ] A client-registered module the server cannot import while the agent does not use its tool still only logs a warning and the conversation starts (`F33.custom-tool-module` unchanged). - [ ] A unit test in `tests/agent_server/` covers the rejection. - [ ] The map bullet `F33.custom-tool-module-unimportable` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `openhands-agent-server/openhands/agent_server/conversation_service.py:1528-1547` (`_create_conversation`) imports each `tool_module_qualnames` module and, on `ImportError`, only logs a warning ("The agent will fail gracefully if it tries to use unregistered tools", line 1542), then creates the conversation. - On the first message the agent initializes its tools; `resolve_tool` (`openhands-sdk/openhands/sdk/tool/registry.py:171`) raises `KeyError`, which nothing maps to an HTTP error. Related: https://github.com/OpenHands/software-agent-sdk/issues/4080 (a custom tool module that disappears before a resume; PR https://github.com/OpenHands/software-agent-sdk/pull/5771 keeps a fresh conversation strict and only relaxes resume), https://github.com/OpenHands/software-agent-sdk/issues/2667 (toolset compatibility for persisted sessions). Found by the first full agentic run of verify-agent-s

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5958#issuecomment-6159748558)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5958#pullrequestreview-5513159641): _This review was posted by an AI agent (OpenHands)._ ## Review — PR #5958 **Scope check.** The change belongs in this repository: it touches the agent-server create path and the `verify-agent-server` feature map, both owned here, and it implements the API-contract choice issue #5940 asks for (reject the create with a 4xx). Issue #5940 is open with `ready-for-dev`, `bug`, `priority:low`. No product/architecture escalation is needed. **Verification performed on head `5f29be7b` (all CI check runs green, including `agent-server-tests`, `cross-tests`, `security`).** - Focused tests: `tests/agent_server/test_conversation_service.py` + `tests/agent_server/test_conversation_router.py` -> 233 passed. - Live repro against a real agent server started from this head: a create whose agent names `qa_ghost_tool` with `tool_module_qualnames={"qa_ghost_to

### #5859: fix(agent-server): URL-encode the VS Code URL's folder and token

- Commit: `eac3ee16cc988ca4182f3f4e52b1dee67828cd12` (2026-10-17T05:20:36+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5859).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5859). Families: F16.

Stated intent:

> - `VSCodeService.get_vscode_url` builds the query with `urlencode(..., quote_via=quote, safe="/")`, so `folder` (and `tkn`) decode back exactly; `/` stays readable, so `folder=/workspace` is unchanged.
> - Unit tests for the round trip (`&`, `#`, `?`, `%`, `+`, spaces) and the exact encoded form.
> - Feature map: `F16.vscode-folder-encoding` loses its `# bug` marker and "known bug" label, its prose now states the expected behaviour, and it declares `Requires: F16.vscode-enabled-status` (without it, `--only` hit 503 "VSCode is disabled" before reaching the assertion).

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F16-workspace-serving-and-vscode.md`
- `openhands-agent-server/openhands/agent_server/vscode_service.py`
- `tests/agent_server/test_vscode_service.py`

Linked issue acceptance context:

- [#5845](https://github.com/OpenHands/software-agent-sdk/issues/5845): [Bug]: VS Code URL's folder parameter is not URL-encoded, so a workspace path with '&' or '#' opens the wrong folder — current state closed, closed 2026-10-17T03:20:38Z.

> - [ ] `VSCodeService.get_vscode_url` URL-encodes the query values, so the `folder` parameter decodes back to the exact workspace path for paths containing `&`, `#`, `?`, `%`, `+` or spaces. - [ ] Existing plain paths keep their form (`/` is not escaped), so `folder=/workspace` is unchanged. - [ ] A unit test in `tests/agent_server/test_vscode_service.py` covers the round trip. - [ ] The map bullet `F16.vscode-folder-encoding` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: `openhands-agent-server/openhands/agent_server/vscode_service.py:135` builds the URL as `f"{base}/?tkn={self.connection_token}&folder={workspace_dir}"` with no encoding. Both `/api/vscode/url` (`vscode_router.py:48`) and the conversation-scoped route (`runtime_router.py:104`) go through it. Related: `F16.vscode-url-not-running` (the URL is returned while VS Code is not running) is a separate item and is not in scope here. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5859#issuecomment-6158172021)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5859#pullrequestreview-5570230212): _This review was posted by an AI agent (OpenHands)._ **Scope.** Belongs in this repository: the Agent Server owns `VSCodeService` and the canonical `/api/vscode/url` + `/api/conversations/{id}/vscode/url` REST routes. No cross-repo move needed. **Current state (re-read now).** Head is still `55d5543ab5f32bc2e029a3b05ef349a915033e8d`; PR state is `closed` and **merged** (merged 2026-10-17T03:20:36Z, merge commit `eac3ee16`). The head adds one commit over the previously reviewed `f2b8fee`: `55d5543 Merge branch 'main' into claude/umbrella-bug-fixes-tgz9ai-vscode-folder-encoding`. That commit only brings `main` forward - `git show origin/main:.../vscode_service.py` already contains the exact same `urlencode(..., quote_via=quote, safe="/")` block - so the reviewed code is unchanged. Linked issue #5845 is `closed` with `bug`, `priority:low`, `

### #5856: fix(agent-server): report the uploaded file_size in the upload response

- Commit: `af05c8b46a8a0a318f853e6297736fb883c8de73` (2026-10-17T05:26:37+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5856).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5856). Families: F04, F08, F10, F11, F12, F13, F14.

Stated intent:

> - `_upload_file` counts the bytes it streams to disk and returns them as `file_size`; both upload routes share it.
> - `tests/agent_server/test_file_router.py` asserts the size for a small upload, a chunked (>8 KiB) upload, and an overwrite.
> - The `F14.upload-size` map bullet drops its `# bug` marker and "known bug" label, now also checks `file_size` on the raw REST response, and its prose and Gotchas line describe the fixed behaviour.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `5fa4dea24c52` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -731 +731 @@
> -response POST /api/conversations/{runtime_conversation_id}/file/upload 200 application/json schema=Success
> +response POST /api/conversations/{runtime_conversation_id}/file/upload 200 application/json schema=FileUploadResult
> @@ -735 +735 @@
> -response POST /api/file/upload 200 application/json schema=Success
> +response POST /api/file/upload 200 application/json schema=FileUploadResult
> @@ -1685,0 +1686,3 @@
> +schema FileUploadResult property file_size required schema=type="integer"
> +sc

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`
- `openhands-agent-server/openhands/agent_server/file_router.py`
- `openhands-agent-server/openhands/agent_server/models.py`
- `tests/agent_server/test_file_router.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5836](https://github.com/OpenHands/software-agent-sdk/issues/5836): [Bug]: File upload responds only {"success": true}, so the SDK and TypeScript remote workspaces report no file_size — current state closed, closed 2026-10-17T03:26:39Z.

> - [ ] A successful upload (global and conversation-scoped route) reports the number of bytes written, and `RemoteWorkspace.file_upload` (sync and async) reports it as `file_size`. - [ ] The TypeScript `RemoteWorkspace.fileUpload` reports the same `file_size`, so `write_file` no longer says `undefined bytes`. - [ ] Existing clients that only read `success` keep working (the response stays a superset of `{"success": true}`). - [ ] A unit test covers the reported size. - [ ] The map bullet `F14.upload-size` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause: - `_upload_file` (`openhands-agent-server/openhands/agent_server/file_router.py:85`) streams the body to disk and returns `Success()` (line 109); `Success` (`openhands-agent-server/openhands/agent_server/models.py:443`) has only `success`. - `_file_upload_generator` (`openhands-sdk/openhands/sdk/workspace/remote/remote_workspace_mixin.py:336`) sets `file_size=result_data.get("file_size")`. - `RemoteWorkspace.fileUpload` (`clients/typescript/src/workspace/remote-workspace.ts:171-177`) copies `resultData.file_size`; `clients/typescript/src/conversation/local-conversation.ts:655` prints it. The SDK's own unit tests already mock the response as `{"success": true, "file_size": 12}` (`tests/sdk/workspace/remote/test_remote_workspace_mixin.py:343`), i.e. the clients were written against a response that includes the size. Two fixes are possible: the server sends `file_size` (one change, every client benefits), or each client co

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5856#issuecomment-6158226293)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5856#pullrequestreview-5513067179): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope for this repository: the change is confined to the Python Agent Server (`file_router.py`, `models.py`), its canonical OpenAPI response contract, its regression tests, and the repo-owned `verify-agent-server` feature map. It is an additive, backward-compatible REST response change (`Success` to `FileUploadResult(Success)` with an added `file_size`), so no product/architecture decision is needed. ## Assessment The fix is correct and minimal. `_upload_file` now accumulates `len(chunk)` over the same chunks it writes and returns `FileUploadResult(file_size=...)`; the byte count therefore reflects exactly what the server wrote. Both upload entry points share this helper: the global `POST /api/file/upload` and the conversation-scoped `POST /api/conversations/{runtime_convers
- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5856#pullrequestreview-5570071400): _This review was posted by an AI agent (OpenHands)._ ## Scope In scope for this repository: the change touches only the Python Agent Server (`file_router.py`, `models.py`), its canonical OpenAPI response contract, its regression tests, and the repo-owned `verify-agent-server` feature map. It is an additive, backward-compatible REST response change (`Success` to `FileUploadResult(Success)` with an added `file_size`), so no product/architecture decision is required. ## Assessment Re-reviewed at the current head `a30648c`, which merged `main` (base moved to `5fa4dea`) since my prior review at `b8ef70a`. The fix is intact and correct on this head. `_upload_file` accumulates `len(chunk)` over the same chunks it writes and returns `FileUploadResult(file_size=file_size)` (`file_router.py:112`). Both upload entry points share this helper, so the 

### #5853: fix(agent-server): page search_subdirs past names that differ only by case

- Commit: `501d27323cf36325242508de6fbf6a6a66c05b2b` (2026-10-17T05:32:10+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5853).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5853). Families: F14.

Stated intent:

> - Sort by `(name.lower(), name)`: the order stays case-insensitive, and case-only twins now have a stable order.
> - `next_page_id` is now the exact name of the next entry. A `page_id` that matches no exact name falls back to the old case-insensitive match, so a lowercase cursor from an older server still resumes at its entry.
> - Two unit tests: paging past `Foo`/`foo`, and resuming from a lowercase cursor. The `F14.subdirs-case-pagination` map bullet loses its known-bug marker, and its prose and Gotchas line now describe the fixed behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F14-files.md`
- `openhands-agent-server/openhands/agent_server/file_router.py`
- `tests/agent_server/test_file_router.py`

Linked issue acceptance context:

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5837](https://github.com/OpenHands/software-agent-sdk/issues/5837): [Bug]: GET /api/file/search_subdirs pagination never gets past two directories whose names differ only by case — current state closed, closed 2026-10-17T03:32:12Z.

> - [ ] Following `next_page_id` from `GET /api/file/search_subdirs` over `Foo`, `foo` (and any other entries) with `limit=1` visits each subdirectory exactly once and ends with no `next_page_id`. - [ ] Ordering stays case-insensitive by name, and existing pagination over names without case twins is unchanged. - [ ] `tests/agent_server/test_file_router.py` covers paging past case-only twins. - [ ] The map bullet `F14.subdirs-case-pagination` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6e7a853 ### Additional Context Root cause, in `openhands-agent-server/openhands/agent_server/file_router.py` (`search_subdirs`): - entries are sorted by `name.lower()` only (line 896), so the order of `Foo` and `foo` depends on the on-disk scan order; - resuming picks the first entry whose `name.lower() == page_id` (lines 899-903); - the cursor is `entries[start_index + limit].name.lower()` (line 908), so the cursor for `foo` also matches `Foo`. Suggested fix: sort by `(name.lower(), name)` so the order is total and stable, and make the cursor the exact name of the next entry, resuming at the entry with that exact name (falling back to the case-insensitive match for a lowercase cursor issued before the change). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5853#issuecomment-6158052302)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5853#pullrequestreview-5512002589): _This review was posted by an AI agent (OpenHands)._ ## Review — `fix(agent-server): page search_subdirs past names that differ only by case` Scope: the change is in `openhands-agent-server`, the canonical REST API this repository owns, so it belongs here. Linked issue #5837 is open with `ready-for-dev` / `priority:low`; its acceptance criteria are addressed below. Verified on head `71e271c5`: - CI: every check run on this head is green, including `agent-server-tests`, `cross-tests`, `REST API (OpenAPI)`, the TypeScript client checks, and `pre-commit`. No failing or pending checks. - Focused tests: `uv run --frozen pytest tests/agent_server/test_file_router.py tests/agent_server/test_search_limit_validation.py` → 101 passed. Correctness: - Sorting by `(name.lower(), name)` makes the order total, so case-only twins get distinct positions a

### #5819: fix(agent-server): return 404 from ask_agent for an unknown conversation

- Commit: `e98b95654fdfe4001d605a6f61893d65d8fa0b3d` (2026-10-17T05:34:14+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5819).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5819). Families: F04, F05, F08, F09, F10, F11, F12.

Stated intent:

> - `conversation_router.ask_agent`: map the service's `None` (only returned when the conversation is missing; `EventService.ask_agent` returns `str`) to `404 "Conversation not found"`, the same answer `condense` gives.
> - Router unit test for the unknown-id case.
> - verify-agent-server map: drop the `F10.ask-agent-unknown` known-bug marker, check the 404 detail, and list 404 among the route's responses.
> 
> `RemoteConversation.ask_agent` and the TypeScript client's `askAgent` just surface the HTTP error, and the OpenAPI schema already declared 404, so neither client changes.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md`
- `openhands-agent-server/openhands/agent_server/conversation_router.py`
- `tests/agent_server/test_conversation_router.py`

Linked issue acceptance context:

- [#5813](https://github.com/OpenHands/software-agent-sdk/issues/5813): [Bug]: POST /api/conversations/{id}/ask_agent returns 500 for an unknown conversation instead of the documented 404 — current state closed, closed 2026-10-17T03:34:16Z.

> - [ ] `POST /api/conversations/{id}/ask_agent` with an unknown id returns `404` (`{"detail": "Conversation not found"}`), matching `condense` and `navigate`. - [ ] A known conversation still answers `200 {"response": ...}`; an unreachable model is still a `500` with an `error_id`. - [ ] A unit test in `tests/agent_server/test_conversation_router.py` covers the unknown-id case. - [ ] The map bullet `F10.ask-agent-unknown` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6a298a4 ### Additional Context Root cause: - `ConversationService.ask_agent` (`openhands-agent-server/openhands/agent_server/conversation_service.py:1869-1873`) returns `None` when `_get_or_load_event_service` finds no conversation. `EventService.ask_agent` returns `str`, so `None` only ever means "not found". - The route (`openhands-agent-server/openhands/agent_server/conversation_router.py:760-762`) maps `None` to a bare `HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)`, although it declares `responses={404: ...}` (line 752). `condense_conversation` (line 773-775) raises `404 "Conversation not found"` for the same case. Suggested fix: raise `HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")` there. `RemoteConversation.ask_agent` and the TypeScript client's `askAgent` just surface the HTTP error, so they need no change. Related: #5800 (unknown ids on pause/interrupt/DELETE/PATCH). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5819#issuecomment-6157682633)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5819#pullrequestreview-5511695815): _This review was posted by an AI agent (OpenHands)._ ## Review — fix(agent-server): return 404 from ask_agent for an unknown conversation **Scope.** This belongs here: the Agent Server owns the `/api/**` REST contract, and the change is a route-level response mapping. No cross-repo or product/architecture decision is needed. **What I verified on head `40bac19`:** - `ConversationService.ask_agent` (`conversation_service.py:1869`) returns `None` only for the not-found path — `EventService.ask_agent` is typed `-> str` (`event_service.py:2156`) — so mapping `None` → `404 "Conversation not found"` in `conversation_router.ask_agent` is correct. It matches the route's declared `responses={404: ...}` and the sibling `condense` route, which raises the same status and detail. - No client change is required: `RemoteConversation.ask_agent` and the Ty

### #5811: fix(sdk): refuse a blank ACP model before the first run

- Commit: `7f41284acefd65a978c1075d7e19631a6730b782` (2026-10-17T05:35:41+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5811).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5811). Families: F05, F08, F09, F10.

Stated intent:

> - `LocalConversation.switch_acp_model` now refuses an empty or whitespace-only model (`ValueError`, same message as `set_acp_model`) before the live/pre-run branch, so nothing is persisted. The agent-server route already maps `ValueError` to 400; the TypeScript client only forwards the request, so no change there.
> - Unit test for the pre-run blank case (`""` and `"   "`) in `tests/sdk/conversation/test_switch_model.py`.
> - Drops the `known bug` marker from the `F09.acp-model-blank` map bullet and rewrites its prose as the expected behaviour.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md`
- `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`
- `tests/sdk/conversation/test_switch_model.py`

Linked issue acceptance context:

- [#5807](https://github.com/OpenHands/software-agent-sdk/issues/5807): [Bug]: switch_acp_model before the first run accepts a blank or whitespace model and persists it — current state closed, closed 2026-10-17T03:35:42Z.

> - [ ] `switch_acp_model` with an empty or whitespace-only model on a not-yet-run ACP conversation answers `400` (SDK: raises `ValueError`) and leaves `agent.acp_model`, `current_model_id` and `base_state.json` unchanged. - [ ] A valid pre-run switch is still deferred and persisted (200), and the live-session behaviour is unchanged. - [ ] A unit test in `tests/sdk/conversation/test_switch_model.py` covers the blank pre-run case. - [ ] The map bullet `F09.acp-model-blank` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6a298a4 ### Additional Context Root cause: the non-empty check lives only in `ACPAgent.set_acp_model` (`openhands-sdk/openhands/sdk/agent/acp_agent.py:4472-4473`). `LocalConversation.switch_acp_model` (`openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:1723`) calls it only when a live session exists (lines 1761-1763); the pre-run path skips it and persists the value unchecked (`model_copy(update={"acp_model": model})` at line 1782, `acp_current_model_id` at line 1803). The router (`openhands-agent-server/openhands/agent_server/conversation_router.py:714-720`) already maps `ValueError` to 400, so validating in `switch_acp_model` before the live/pre-run branch fixes both the SDK and the REST route. Related: #3763 (the pre-run deferral that introduced this path), fixed by #3764. Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5811#issuecomment-6157500782)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5811#pullrequestreview-5511473642): _This review was posted by an AI agent (OpenHands)._ ## Review: PR #5811 — fix(sdk): refuse a blank ACP model before the first run **Scope.** Belongs in this repository: the guard lives on the SDK's canonical Python surface, `LocalConversation.switch_acp_model`, and the agent-server route already maps `ValueError` → 400, so one shared check covers both the SDK and REST. No cross-repo move. Linked issue #5807 is open with `ready-for-dev`. **Change.** The new guard placed before the live/pre-run branch (`if not model or not model.strip(): raise ValueError("model must be a non-empty string")`) reuses the exact message from `ACPAgent.set_acp_model`, so the REST route returns 400 with the same detail as a live session. It runs before `with self._state:`, so on refusal `self.agent`, `self._state.agent`, `agent_state["acp_current_model_id"]`, an

### #5795: fix(agent-server): refuse more than 99 batch-get ids with a 422

- Commit: `9d07c6d55d1b371ed3d7ce6fba472ecef50f7a90` (2026-10-17T05:45:44+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5795).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5795). Families: F04, F05, F08, F09, F10, F11, F12.

Stated intent:

> - Declare the limit as `ids: Annotated[list[UUID], Query(max_length=99)]` and drop the assert. 100+ ids now get a 422 ("List should have at most 99 items"), the service is not called, and OpenAPI shows `maxItems: 99`, following the declared `le=100` on search `limit` (https://github.com/OpenHands/software-agent-sdk/issues/5114).
> - The router test now asserts the 422 (it used to assert the `AssertionError`), the 99-id 200, and the OpenAPI bound.
> - Remove the `# bug` marker from the `F04.batch-get-limit` map bullet. The recipe now expects a 422 whose body says `at most 99 items`.
> 
> As the issue triage scoped it, the 99-id limit stays the same and the TypeScript client gets no chunking. A TS batch of 100 ids now gets the 422 instead of a 500.
> 
> 
> ### REST API contract changes
> 
> Compared with base OpenAPI `6a298a48fcdb` for public `/api/**` paths.
> 
> ```diff
> --- base public OpenAPI
> +++ head public OpenAPI
> @@ -195 +195 @@
> -parameter GET /api/conversations query:ids required=true schema=type="array" items=type="string" format="uuid"
> +parameter GET /api/conversations query:ids required=true schem

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md`
- `openhands-agent-server/openhands/agent_server/conversation_router.py`
- `tests/agent_server/test_conversation_router.py`

Linked issue acceptance context:

- [#5114](https://github.com/OpenHands/software-agent-sdk/issues/5114): [Bug]: GET /api/conversations/search returns 500 for limit > 100 (no graceful clamp) — current state closed, closed 2026-09-17T05:33:34Z.

> - [ ] `GET /api/conversations/search?limit=101` returns **422** (not 500), naming `limit`. - [ ] `limit=100` still returns `200` with 100 items; `limit=0` and negative limits still return 422. - [ ] A test covers `limit` at 0, 1, 100 and 101, asserting 422/200/200/422. - [ ] The `assert`-based checks in this file (lines 117, 118 and 196) are replaced by real validation, so behaviour is identical under `python -O`. - [ ] `limit=101` no longer emits `Unhandled exception ... api.py:616` in the server log. ### Environment - `openhands-agent-server==1.46.0` (via `uvx`), local mode - Agent Canvas 1.17.0 - Ubuntu 24.04, m7i.2xlarge, 300 GB gp3 - Reproduced 2026-09-17 with ~140 conversations present ### Anything else? The Canvas sidebar requests `limit=20`, so normal use is unaffected. It bites any client that pages a larger list in one request (bulk export, scripts, monitoring) — and because the failure is a `500` rather than a 4xx, it reads as a server bug rather than a bad request, which sends callers down the wrong path when debugging. Found while measuring list-endpoint latency under load. The same endpoint is the subject of #4537 (state lock held by a sub-agent run); note that even with the listing path now reading the persisted `base_state.json` snapshot rather than live state, this page-size edge case remains. --- _This issue was created and updated by an AI agent (OpenHands) on behalf of Hussain._

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

- [#5790](https://github.com/OpenHands/software-agent-sdk/issues/5790): [Bug]: GET /api/conversations with 100 or more ids returns 500 with an empty exception instead of a 4xx naming the limit — current state closed, closed 2026-10-17T03:45:46Z.

> - [ ] `GET /api/conversations` with 100 or more ids returns a 4xx whose body names the 99-id limit; nothing reaches the conversation service. - [ ] 99 ids still return `200`. - [ ] The OpenAPI schema declares the limit on `ids`. - [ ] `tests/agent_server/test_conversation_router.py` covers the 100-id request (the current test asserts the `AssertionError`). - [ ] The map bullet `F04.batch-get-limit` passes with its `# bug` marker removed. ### Installation Method uv sync in a checkout of main ### Version main at 6a298a4 ### Additional Context Root cause: `batch_get_conversations` in `openhands-agent-server/openhands/agent_server/conversation_router.py:292` is `assert len(ids) < 100`. The `AssertionError` (with no message) reaches `_unhandled_exception_handler` (`openhands-agent-server/openhands/agent_server/api.py:651`), which answers 500 with `"exception": str(exc)`, i.e. `""`. The TypeScript clients send all ids in one request: `clients/typescript/src/conversation/conversation-manager.ts:162` and `clients/typescript/src/client/conversation-client.ts:133`. Related: #5114 (the same kind of 500 for `limit > 100` on search, since fixed with a declared `le=100`). Found by the first full agentic run of verify-agent-server; tracked in #5653. ---

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5795#issuecomment-6157239944)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5795#pullrequestreview-5511360966): _This review was posted by an AI agent (OpenHands)._ ## Review: PR #5795 — refuse more than 99 batch-get ids with a 422 ### Scope Agent-server REST API change (the canonical API owned by this repository). It replaces an assert-based batch-size guard with a declared FastAPI/Pydantic query constraint, matching the authoritative pattern already used for the search `limit` (`Query(gt=0, le=100)`, #5114). In scope; no cross-repository ownership issue. ### What I verified on the current head (`f2ac77c0`) - **Runtime behavior.** `ids: Annotated[list[UUID], Query(max_length=99)]` turns 100 ids into a FastAPI 422 (`"List should have at most 99 items"`) before the handler body runs, so `conversation_service.batch_get_conversations` is not called. The new `mock_conversation_service.batch_get_conversations.assert_not_called()` in `test_batch_get_conv

### #5773: fix(agent-server): accept short event kinds in events search and count

- Commit: `75abe1eb3f5fca908578d4437e8fee36125e4b0b` (2026-10-17T06:00:17+02:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5773).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5773). Families: F05, F06, F07, F08, F09, F10, F11, F12, F31.

Stated intent:

> - `EventService._event_matches_filters` (shared by `search_events` and `count_events`) now accepts either the bare class name or the fully-qualified class path. Unknown or partial names still give an empty page / `0`, and `kind` still combines with the other filters. No change to the wire format, the schema or the TypeScript client.
> - Unit tests in `tests/agent_server/test_event_service.py` cover the short kind for search (same ids and `next_page_id` as the full path, combined with `source`, a partial path not matching) and for count.
> - verify-agent-server map: dropped the known-bug markers from `F06.search-kind-short` and `F34.events-search-kind`, rewrote their prose as the expected behaviour, updated the Gotchas lines in F05, F06, F08, F33 and F34 that described the bug, and gave `F06.search-kind-short` the `Requires: F06.send-no-run` line it needs to run on its own (without it `--only` ran against an empty log and failed before reaching the marked line).

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F05-run-pause-interrupt.md`
- `.agents/skills/verify-agent-server/references/feature-map/F06-conversation-events.md`
- `.agents/skills/verify-agent-server/references/feature-map/F08-confirmation-security-secrets.md`
- `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md`
- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `openhands-agent-server/openhands/agent_server/event_service.py`
- `tests/agent_server/test_event_service.py`

Linked issue acceptance context:

- [#5631](https://github.com/OpenHands/software-agent-sdk/issues/5631): [Bug]: events/search and /count with kind=MessageEvent return nothing; only the full class path matches — current state closed, closed 2026-10-17T04:00:19Z.

> - [ ] `events/search` and `events/count` with `kind=MessageEvent` return the same results as the fully qualified kind; the fully qualified form keeps working. - [ ] A unit test in `tests/agent_server/test_event_service.py` covers a short kind for both `search_events` and `count_events`. - [ ] The `F06.search-kind-short` and `F34.events-search-kind` map bullets pass with their known-bug markers removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context Root cause: `EventService._event_matches_filters` (`openhands-agent-server/openhands/agent_server/event_service.py:544`) rejects an event when `f"{event.__class__.__module__}.{event.__class__.__name__}" != kind` (lines 554-558). `search_events` (line 653) and `count_events` (line 725) both use it. The wire `kind` is the bare class name (`DiscriminatedUnionMixin.kind` returns `self.__class__.__name__`, `openhands-sdk/openhands/sdk/utils/models.py:203-205`), as the route docs (`event_router.py:80-85`, `:146-151`) and the TS client (`clients/typescript/src/events/remote-events-list.ts:22-23`, sent unchanged at lines 60 and 83) say. The unit test (`tests/agent_server/test_event_service.py:265-268`) only uses the fully qualified form. Suggested fix: accept both forms in `_event_matches_filters`, for example ```python cls = type(event) if kind is not None and kind not in (cls.__name__, f"{cls.__module__}.{cls.__name__}"): return False ``` This keeps fully qualified callers working. Rejecting unknown kinds with 422 and fixing the docs is the alternative, but it breaks clients wri

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5773#issuecomment-6156414179)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5773#pullrequestreview-5510938299): _This review was posted by an AI agent (OpenHands)._ ## Scope The change belongs here: `openhands-agent-server/` owns the canonical REST API and the `verify-agent-server` feature map is a repository-owned skill. The fix is confined to the `kind` filter shared by `events/search` and `events/count`, and it moves the server toward the behavior its own route docs (`e.g., ActionEvent, MessageEvent`), the wire `kind` field, and the TypeScript client's `EventSearchOptions.kind` already describe. ## What I verified on the current head (`7b0ea38`) - **Filter semantics.** `_event_matches_filters` now matches either `cls.__name__` or `f"{cls.__module__}.{cls.__name__}"`. Exercised directly against a real `MessageEvent`: `kind=MessageEvent` matches, the fully-qualified path matches, `message.MessageEvent` does not, an unknown name does not, and `kind

### #5783: fix(ts): honor maxIterations and stuckDetection in ConversationManager.createConversation

- Commit: `1fbfd37f6fb21491ed733fc92d409aff1517ff4a` (2026-10-17T04:10:34Z). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5783).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5783). Families: F34.

Stated intent:

> - `ConversationManager.createConversation` passes `maxIterations` and `stuckDetection` to `RemoteConversation.start()`.
> - `RemoteConversation` keeps both constructor options and uses them as fallbacks in `start()`; the `0`/omitted handling of `maxIterations` (`|| 500`) is unchanged. Also fixes the stale "default: 50" doc comment on `BaseConversationOptions.maxIterations` (the default is 500).
> - New `conversation-create-options.test.ts` asserts the `POST /api/conversations` body; the `F34.manager-create-options` map bullet loses its known-bug marker.

Changed paths:

- `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md`
- `clients/typescript/src/__tests__/conversation-create-options.test.ts`
- `clients/typescript/src/conversation/base.ts`
- `clients/typescript/src/conversation/conversation-manager.ts`
- `clients/typescript/src/conversation/remote-conversation.ts`

Linked issue acceptance context:

- [#5649](https://github.com/OpenHands/software-agent-sdk/issues/5649): [Bug]: TS ConversationManager.createConversation ignores maxIterations/stuckDetection (server gets 500/true) — current state closed, closed 2026-10-17T04:10:38Z.

> - [ ] `ConversationManager.createConversation` sends the caller's `maxIterations` and `stuckDetection` in `POST /api/conversations`, and omitting them keeps today's defaults (500, `true`). - [ ] A unit test on the manager asserts the request body carries both values. - [ ] The `F34.manager-create-options` bullet in `F34-typescript-client.md` passes, and its known-bug marker is removed. ### Installation Method uv sync in a checkout of main ### Version main at 2804c12 ### Additional Context Root cause: - `clients/typescript/src/conversation/conversation-manager.ts:221-225` passes `maxIterations` and `stuckDetection` to the `RemoteConversation` constructor, then `:227-230` calls `start()` with only `initialMessage` and `userId`. - The constructor (`clients/typescript/src/conversation/remote-conversation.ts:96-114`) drops both, although `BaseConversationOptions` declares them (`clients/typescript/src/conversation/base.ts:30,32`). - `start()` builds the request only from its own arguments: `max_iterations: options.maxIterations \|\| 500` and `stuck_detection: options.stuckDetection ?? true` (`remote-conversation.ts:173-174`). The ACP path is not affected: `createACPConversation` builds the request from the options directly (`conversation-manager.ts:355-356`). Suggested fix: pass `maxIterations` and `stuckDetection` to `start()` in `createConversation`. Optionally also keep both in the constructor and use them as fallbacks in `start()`, as `hookConfig` and `userId` already are (`remote-conversation.ts:167-168`), so `new RemoteConversation(agent, ws, { maxIterations: 7 })` and the

- [#5653](https://github.com/OpenHands/software-agent-sdk/issues/5653): Agent Server bugs found by the verify-agent-server feature map (umbrella) — current state open, closed —.

> I'm an AI agent based on Opus 5.5, helping Engel Nyst (@enyst) with project maintenance. While building the executable feature map of the Agent Server API in #5621 (the `verify-agent-server` skill), we drove every agent-facing route on a real server and found product bugs along the way. This issue collects them, so there is one place to see the whole picture. Each bug is a known-bug bullet in the map, named by the sub-feature ID in parentheses. `control-agent-server map run --file <family> --only <ID> --fresh` reproduces it, and once a fix land

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5783#issuecomment-6157032250)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5783#pullrequestreview-5511273078): _This review was posted by an AI agent (OpenHands)._ ## Review: honor maxIterations / stuckDetection in `ConversationManager.createConversation` **Scope:** `clients/typescript/` mirrors the Agent Server API for browser clients, so this change belongs in this repository. No cross-repo move or maintainer scope decision is needed. **Why this is correct** The reported bug: `ConversationManager.createConversation` accepted `maxIterations`/`stuckDetection`, passed them to the `RemoteConversation` constructor (which dropped them), and called `start()` without them, so `POST /api/conversations` always carried `max_iterations: 500` / `stuck_detection: true`. The head fixes both layers: - `conversation-manager.ts` now forwards both options to `start()` — the required fix. - `remote-conversation.ts` keeps both constructor options and applies them as

### #5126: feat(agent-server): add start_child_conversation tool with a pluggable launcher

- Commit: `5966c781ed014efc96c028a99bddd4ff78426741` (2026-10-17T16:03:12+07:00). [PR](https://github.com/OpenHands/software-agent-sdk/pull/5126).
- Intent: documented. Runtime: **family-sweep-with-known-defects**; [feature evidence and limits](intent-runtime-overlay.md#pr-5126). Families: F01, F02, F03, F04, F05, F06, F08, F09, F10, F11, F12, F13, F27, F29, F30, F31, F32, F33, F34.

Stated intent:

> - `POST /api/conversations/{conversation_id}/children`, backed by `ConversationService.start_child_conversation`. Like a fork, the child copies the parent's stored configuration (secrets, client tools, plugins, hooks, tags, confirmation and security settings) and its resolved agent, but it starts with an empty history, receives `task` as its first user message and records `parent_conversation_id`. `isolation="worktree"` (the default) reuses the existing worktree preparation and falls back to the parent's directory when that directory is not a git repository; `isolation="shared"` always shares it. The response reports the isolation that was actually applied. After that child-specific preparation the launch goes through the same tail as a normal start (`_launch_prepared_conversation`, shared with `_create_conversation`): the run slot is acquired first, so a full server answers 429 before a worktree or persistence directory exists, then the child is persisted, `task` is delivered, the first run is scheduled on that slot and webhooks are notified.
> - `start_child_conversation` tool in `op

Changed paths:

- `openhands-agent-server/openhands/agent_server/api.py`
- `openhands-agent-server/openhands/agent_server/config.py`
- `openhands-agent-server/openhands/agent_server/conversation_router.py`
- `openhands-agent-server/openhands/agent_server/conversation_service.py`
- `openhands-agent-server/openhands/agent_server/init_router.py`
- `openhands-agent-server/openhands/agent_server/models.py`
- `openhands-agent-server/openhands/agent_server/tool_router.py`
- `openhands-sdk/openhands/sdk/conversation/request.py`
- `openhands-tools/openhands/tools/child_conversation/__init__.py`
- `openhands-tools/openhands/tools/child_conversation/definition.py`
- `openhands-tools/openhands/tools/child_conversation/impl.py`
- `tests/agent_server/conftest.py`
- `tests/agent_server/test_config.py`
- `tests/agent_server/test_init_router.py`
- `tests/agent_server/test_parent_child_conversations.py`
- `tests/tools/child_conversation/test_start_child_conversation.py`

REST compatibility: [PASSED](https://github.com/OpenHands/software-agent-sdk/pull/5126#issuecomment-5710999102)

Review / discussion decisions (chronological evidence retained in raw records):

- [reviews; APPROVED; tofarr](https://github.com/OpenHands/software-agent-sdk/pull/5126#pullrequestreview-5279081028): 🍰
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5126#pullrequestreview-5383129292): _This review was posted by an AI agent (OpenHands)._ I reviewed the head `5f3ce36` in the workspace, read the repo guidance (root `AGENTS.md`, `.agents/skills/custom-codereview-guide.md`, and the agent-server/tools package `AGENTS.md`), and ran the focused suites: - `uv run pytest tests/tools/child_conversation tests/agent_server/test_parent_child_conversations.py -q` -> 30 passed - `uv run pytest tests/agent_server/test_parent_child_conversations.py tests/tools/child_conversation tests/agent_server/test_conversation_router.py tests/agent_server/test_openapi_contract.py -q` -> 130 passed - `uv run pre-commit run --files <changed files>` -> Ruff format/lint, pycodestyle, pyright, import rules, tool-registration all passed The core design is sound and fits the repository boundary: the SDK/agent-server owns the launch, the worktree machinery
- [reviews; COMMENTED; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5126#pullrequestreview-5495002135): _This review was posted by an AI agent (OpenHands)._ I re-reviewed the current head `047e214` in the workspace (the branch merged `main` twice since my last pass; the PR's own source — the `child_conversation` tool, request/response models, router, `start_child_conversation` — is byte-identical to what I reviewed, while `conversation_service.py` around it was heavily refactored by the merge). I re-read the repo guidance and re-ran the focused suites: - `uv run pytest tests/tools/child_conversation tests/agent_server/test_parent_child_conversations.py -q` -> 30 passed - `uv run pytest tests/agent_server/test_parent_child_conversations.py tests/tools/child_conversation tests/agent_server/test_conversation_router.py tests/agent_server/test_openapi_contract.py -q` -> 138 passed Head state: PR open, non-draft, `mergeable_state: unstable`, no l
- [reviews; APPROVED; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5126#pullrequestreview-5554766486): Approving — thanks for taking the shared-helper route. `_launch_prepared_conversation` as the common tail of normal and child starts, with the `RunSlot` acquired before any worktree or persistence side effects (and the regression test asserting a refused launch leaves nothing behind), addresses my earlier comment. One non-blocking nit: the new `POST /api/conversations/{conversation_id}/children` route declares 201/404/422 responses but not 429, unlike the other start endpoints. Runtime behavior is correct — the global handler returns 429 when the run limit is reached, as your E2E run shows — so this is only the OpenAPI declaration being incomplete. Fine to fix in a follow-up. _This review was posted by an AI agent (OpenHands) on behalf of @gneubig._
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5126#discussion_r4158508845): The executor attaches `session_api_key_headers()` to whatever `url` resolves to, and `url` is `self.launch_url or default_launch_url(...)`. `launch_url` comes from the tool spec's `params`, which are per-conversation and caller-supplied. I verified that `Tool(name="start_child_conversation", params={"launch_url": "https://cloud.example/children"})` resolves through the registry to an executor with that `launch_url`, so a caller who controls conversation creation can make this request send the server's own `OH_SESSION_API_KEYS_0` / `SESSION_API_KEY` value in `X-Session-API-Key` to an arbitrary host. Consider sending the session key only when the target is the server's own base URL (or otherwise restricting `launch_url` to a server-controlled value), and requiring `https` for an override.
- [review_comments; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5126#discussion_r4251487481): The child is persisted by the `_start_event_service` call above (and appended to the parent's `sub_conversation_ids`) before this `send_message(..., True)` runs. `send_message` is what acquires the run permit via `RunSlot.acquire` inside `EventService.run`, and it raises `ConversationRunLimitExceeded` when the server is full; the `safe_rmtree(child_dir)` in the earlier `except` only covers failures inside the lifecycle block, so it does not clean up this case. Reproduced on this head with `max_concurrent_runs=1` and the slot held: the launch raises `ConversationRunLimitExceeded` yet `_children_of(parent)` afterwards contains the new child id, i.e. an idle, linked child whose task never ran. Normal start avoids this by acquiring the run slot before creating anything (`_start_conversation`, line 1455). Consider acquiring a `RunSlot` before 
- [review_comments; comment; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/5126#discussion_r4291350525): Fixed in 93412f5. The child launch now acquires its `RunSlot` before anything is prepared, exactly like `_start_conversation`, so a full server raises `ConversationRunLimitExceeded` (429 through the existing handler) before a worktree or the child's persistence directory exists. After the child-specific preparation, both paths go through the same tail (`_launch_prepared_conversation`): lifecycle lock and re-check, `_start_event_service`, `send_message(..., False)`, `run(run_slot=...)`, webhook notification. Covered by `test_start_child_conversation_refused_when_run_limit_reached` (`max_concurrent_runs=1`, slot held: the launch raises, the parent has no children, no `EventService` was created for a child, no worktree directory exists), and reproduced end to end against a running server started with `OH_MAX_CONCURRENT_RUNS=1` and a parent w
- [review_comments; comment; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/5126#discussion_r4291350777): Resolving this earlier without a note was my mistake; here is the reasoning, now also recorded in the `session_api_key_headers` docstring (93412f5). The key is how the launcher recognises the sandbox, the same role it already plays for the server's webhook subscribers, which send `session_api_keys[0]` as `X-Session-API-Key` to their configured webhook URLs. The Cloud launcher built on this contract (OpenHands/enterprise#433) authenticates the call with that key (`valid_sandbox`), so sending it only to the server's own base URL, or requiring `https`, would break the consumer the seam exists for, as well as local launchers. On the trust model: `launch_url` is set by whoever assembles the conversation's agent. On a hosted deployment that is the control plane, which minted the sandbox's key in the first place. On a directly driven server, the
- [discussion; comment; all-hands-bot](https://github.com/OpenHands/software-agent-sdk/pull/5126#issuecomment-5710992785): 👋 **This PR needs a couple of things fixed before OpenHands can review it:** - the PR description's `HUMAN:` section needs at least 20 characters describing what you tested, not just the template placeholder Push an update once this is addressed and this check re-runs automatically. _This is an automated check - no AI was used to generate this comment._
- [discussion; comment; neubig](https://github.com/OpenHands/software-agent-sdk/pull/5126#issuecomment-6129598594): Non-blocking architectural suggestion: could the normal-start and child-start paths share the higher-level creation lifecycle, rather than only `_start_event_service()`? The child-specific preparation makes sense: inherit the parent’s resolved agent and stored configuration, derive shared/worktree isolation, and set the parent link. But after preparation, admission, runtime startup/persistence, initial-message delivery, scheduling, notifications, and failure handling should largely be the same as a normal start. Currently `start_child_conversation()` prepares and persists the child, then calls `send_message(..., run=True)`, whereas normal start acquires a `RunSlot` before creation. I reproduced a capacity refusal with `max_concurrent_runs=1` and the slot occupied: the child launch raised `ConversationRunLimitExceeded`, but left a persiste
- [discussion; comment; hieptl](https://github.com/OpenHands/software-agent-sdk/pull/5126#issuecomment-6209065733): Hello Graham, Thank you for the suggestion and for the reproduction. I took the shared-helper route in 93412f5: - `_launch_prepared_conversation(stored, launched, *, run_slot, initial_message, reuse_request=None)` is now the tail of both paths: lifecycle lock and re-check of the id, `_start_event_service`, delivery of the initial message, `run(run_slot=...)`, state read and webhook notification. `_create_conversation` passes its request as `reuse_request`, so the existing reuse-under-lock behaviour for a client-chosen id is unchanged; the child launch passes none, so a collision is an error, as for a fork. - `start_child_conversation` acquires the `RunSlot` before the child-specific preparation (parent agent, worktree plan, stored copy), exactly like `_start_conversation`. Your scenario (`max_concurrent_runs=1`, slot occupied) now raises 

## Unowned changed paths

Paths outside a map Source prefix are classified below. A test/CI/documentation path does not itself add an agent-facing route. Product modules outside ownership are coverage gaps, with relevant family expansion where clear.

| Path | Classification |
|---|---|
| `.agents/skills/pr-design-doc/SKILL.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/pr-design-doc/references/html-craft.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/SKILL.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/adaptation.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/daily.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F01-server-status.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F02-auth-and-sessions.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F03-deferred-init.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F04-conversation-lifecycle.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F05-run-pause-interrupt.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F06-conversation-events.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F07-session-socket.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F08-confirmation-security-secrets.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F09-model-switching-plugins.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F10-fork-navigate-condense-ask.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F11-goals.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F12-runtime-and-credentials.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F13-bash.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F14-files.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F15-git.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F16-workspace-serving-and-vscode.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F17-workspaces-registry.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F18-settings-and-secrets.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F19-mcp.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F20-llm-profiles.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F21-agent-profiles.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F22-meta-profiles.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F23-llm-catalog-and-connections.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F24-openai-subscription.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F25-skills.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F26-plugins.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F27-hooks-subagents-tools.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F28-canvas-extensions.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F29-app-backend-bridge.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F30-openai-gateway.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F31-persistence-and-limits.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F32-webhooks-and-telemetry.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F33-sdk-remote-client.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/F34-typescript-client.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/feature-map/README.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/maintenance.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/mapping.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/references/report.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/scripts/control-agent-server` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.agents/skills/verify-agent-server/scripts/control_agent_server.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/PULL_REQUEST_TEMPLATE.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/agent-server-openapi-weak-schema-allowlist.json` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/scripts/update_npm_pin.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/integration-runner.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/pip-revendoring-retirement.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/pr-artifacts.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/precommit.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/server.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/stale.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/storage-scenario.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.github/workflows/update-npm-pin.yml` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `.impeccable/config.json` | Product path without current Source ownership; source wave must classify or extend map ownership. |
| `AGENTS.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `CONTRIBUTING.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `MAINTAINERS` | Product path without current Source ownership; source wave must classify or extend map ownership. |
| `clients/typescript/README.md` | F34: consumer documentation for live-client validation; documentation itself adds no runtime route. |
| `clients/typescript/config/public-type-budget.json` | F34: compile-time exported type budget updated for the new ACP discovery types; not a runtime route. |
| `clients/typescript/package-lock.json` | F34 (and F01 compatibility/version gate): package/dependency lock changes; run built client against TARGET. |
| `examples/01_standalone_sdk/48_conversation_fork.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `openhands-agent-server/AGENTS.md` | Repository guidance only; no agent-facing runtime or schema change. |
| `openhands-agent-server/openhands/agent_server/docker/Dockerfile` | F01, F12, F16, F31: production image dependencies, Docker runtime, removal of VS Code from python-slim, packaging/startup. A source launch does not verify production images; image build/attach is separately required or explicitly blocked. |
| `openhands-agent-server/openhands/agent_server/local_storage.py` | F04, F10, F15, F31: new local worktree ownership/deletion helper removes deleted conversation worktrees while retaining git branches and respecting shared fork workspaces (#5547). |
| `openhands-agent-server/pyproject.toml` | F01, F12, F31: server release metadata and packaged dependencies; dependency changes widen the full-pass scope and require separate production-artifact qualification. |
| `openhands-sdk/openhands/sdk/__init__.py` | F23, F33, F34: public ACPModelOption import now emits deprecation via module __getattr__; ACP discovery replaces static model lists (#5579). |
| `openhands-sdk/openhands/sdk/agent/acp_contracts.py` | F09, F12, F23, F33, F34: shared legacy ACP session-model response types used by model discovery and runtime switching (#5579); verify both discovery and conversation consumers. |
| `openhands-sdk/openhands/sdk/agent/acp_models.py` | F09, F12, F23, F33, F34: canonical ACPModelDiscovery/ACPModelDiscoveryError schema adds model metadata and structured startup failures; REST and typed clients serialize these. |
| `openhands-sdk/openhands/sdk/agent/response_dispatch.py` | F06, F07, F08, F32, F33, F34: forwards event callback through sync/async confirmation analysis so SecurityAnalysisEvent persists and reaches socket/webhook/client consumers (#5182). |
| `openhands-sdk/openhands/sdk/conversation/base.py` | F10, F33: fork(title=...) documentation now distinguishes deprecated LocalConversation title from honored RemoteConversation/server title (#4814); no new runtime code in this file. |
| `openhands-sdk/openhands/sdk/conversation/message_request.py` | F04, F05, F06, F07, F27, F33, F34: shared SendMessageRequest moved to SDK for child-conversation launcher; role/content/run default are serialized REST/session/client inputs (#5126). |
| `openhands-sdk/openhands/sdk/llm/message.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34: shared multimodal request serialization now preserves images when string serializer is forced (#5460); run message transport and real-model consumers, not only catalog reads. |
| `openhands-sdk/openhands/sdk/llm/utils/AGENTS.md` | Repository guidance only; no runtime route. Its model-capability conventions inform source review of model_features.py. |
| `openhands-sdk/openhands/sdk/llm/utils/model_features.py` | F04, F09, F20, F22, F23, F30, F33, F34: provider capability changes for DeepSeek vision, Haiku cache fallback and Bedrock stop-word opt-outs; shared-model consumers need expansion. Providers without credentials remain named blocked capabilities. |
| `openhands-sdk/openhands/sdk/llm/utils/openhands_provider.py` | F04, F09, F20, F22, F23, F30, F33, F34: provider routing table adds DigitalOcean Inference while preserving OpenHands routing and explicit base_url; validates through catalog/profile/model consumers. |
| `openhands-sdk/openhands/sdk/settings/__init__.py` | F18, F21, F23, F33, F34: public settings export deprecation for ACPModelOption, shared schema/import contract rather than an added route. |
| `openhands-sdk/openhands/sdk/settings/acp_providers.py` | F09, F18, F21, F23, F33, F34: deprecated static ACP model metadata and clarified independently surfaced provider contract; compare new discovery endpoint and typed-client imports. |
| `openhands-sdk/openhands/sdk/tool/tool.py` | F27, F33: conversation_local ToolDefinition marker permits subagents' state-local tools while retaining capability scoping (#5520). |
| `openhands-sdk/openhands/sdk/utils/files.py` | F14, F18, F20, F21, F22, F31: atomic write temp files gain .tmp suffix and is_temp_file helper; trajectory archive skips in-flight temp files (#5538), persistence consumers retain atomic behavior. |
| `openhands-sdk/pyproject.toml` | F01, F33: SDK release metadata/dependencies; dependency changes additionally justify the full family pass. |
| `openhands-tools/openhands/tools/child_conversation/__init__.py` | F04, F27, F33, F34: public export for new server-owned start_child_conversation tool and pluggable launcher (#5126). |
| `openhands-tools/openhands/tools/child_conversation/definition.py` | F04, F27, F33, F34: action/observation contract for child creation, parent relationship and launcher; direct-tool/REST/client observations need coverage (#5126). |
| `openhands-tools/openhands/tools/child_conversation/impl.py` | F04, F27, F33, F34: child launch execution, selected stored profile, directory/worktree handling and reported child identity (#5126). |
| `openhands-tools/openhands/tools/delegate/impl.py` | F27, F33: delegated subagents inherit exactly parent MCP servers and allow conversation-local tools without expanding workspace/network capabilities (#5747, #5520). |
| `openhands-tools/openhands/tools/file_editor/editor.py` | F14, F27, F33: file_editor observation numbering preserves terminal-newline semantics and avoids fabricated empty lines (#5670); file APIs alone do not prove model-visible tool output. |
| `openhands-tools/openhands/tools/file_editor/utils/encoding.py` | F14, F27, F33: valid UTF-8 is decoded strictly before heuristic encoding detection (#4784); exercise non-ASCII tool round trips. |
| `openhands-tools/openhands/tools/task_tracker/definition.py` | F27, F33: conversation-local tool marker and canonicalization of pending/completed/In Progress status aliases (#5520, #6003). |
| `openhands-tools/openhands/tools/terminal/constants.py` | F13, F27, F33: shorter adaptive terminal poll intervals (#5715), observable command completion latency through actual tool/SDK runs. |
| `openhands-tools/openhands/tools/terminal/terminal/terminal_session.py` | F13, F27, F33: adaptive polling preserves timeouts and finish detection (#5715); exercise real tmux/subprocess tool calls. |
| `openhands-tools/openhands/tools/terminal/terminal/tmux_pane_pool.py` | F13, F27, F33: shared safe tmux socket naming handles long directories (#6038); verify direct and pooled tools. |
| `openhands-tools/openhands/tools/terminal/terminal/tmux_terminal.py` | F13, F27, F31, F33: private tmux socket ownership avoids startup cleanup of live sibling sessions; long-path fallback works for direct and pooled terminals (#5589, #6038). |
| `openhands-tools/openhands/tools/terminal/utils/command.py` | F13, F27, F33: AST command boundaries preserve valid chained/multiple heredoc scripts (#5342); direct terminal recipe required. |
| `openhands-tools/pyproject.toml` | F01, F27, F33: tools package release metadata; no direct new route, verify built-in tools on assembled server. |
| `openhands-workspace/pyproject.toml` | F01, F12, F33: workspace package release metadata; no direct new route, verify RemoteWorkspace and runtime consumers. |
| `pyproject.toml` | Dependency / package-version change. Requires full live pass; production image packaging remains a distinct capability. |
| `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/canvas_extensions/test_canvas_extension_bridge.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/canvas_extensions/test_canvas_extension_bridge_routes.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/conftest.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/docker_runtime/STORAGE_SCENARIO.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/docker_runtime/test_mediation.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/docker_runtime/test_pip_revendoring_retirement.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/docker_runtime/test_proxy.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/docker_runtime/test_storage_scenario.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/telemetry/test_telemetry_disabled_by_default.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/telemetry/test_telemetry_subscriber.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_acp_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_agent_launch_additions.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_agent_profile_conv_start.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_agent_profiles_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_api.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_auto_title_span_metadata.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_bash_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_bash_service.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_canvas_extensions_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_config.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_conversation_response.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_conversation_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_conversation_service.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_event_service.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_file_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_git_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_goal_loop.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_init_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_launch_architecture.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_launch_parity.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_llm_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_local_storage.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_mcp_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_openai_gateway_launch.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_openai_gateway_reused_wait.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_openai_gateway_settings.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_parent_child_conversations.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_plugins_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_preload_modules.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_profiles_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_run_admission.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_runtime_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_server_details_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_settings_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_skills_service.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_sub_agents_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_switch_llm_survives_reload.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_unhandled_exception_error_id.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_vscode_service.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_webhook_subscriber.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_websocket_first_message_auth.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_workspace_cookie_auth.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/agent_server/test_workspaces_router.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/cross/conftest.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/cross/test_agent_server_build_metadata.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/cross/test_remote_conversation_live_server.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/cross/test_update_npm_pin.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/cross/test_verify_agent_server_skill.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/integration/README.md` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/integration/tests/c02_hard_context_reset.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/integration/tests/c04_token_condenser.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/agent/fake_acp_models_server.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/agent/test_acp_agent.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/agent/test_acp_conformance.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/agent/test_acp_model_discovery.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/agent/test_async_secret_masking.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/agent/test_security_analysis_event.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/conversation/local/test_fork.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/conversation/remote/test_remote_conversation.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/conversation/remote/test_remote_events_list.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/conversation/test_local_conversation_plugins.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/conversation/test_switch_model.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/extensions/test_fetch.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/git/test_cached_repo.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/git/test_git_changes.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/git/test_git_diff.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/launch/fakes.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/launch/test_field_ownership.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/launch/test_preview.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/launch/test_resolve.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/auth/test_openai.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/test_llm.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/test_llm_log_completions_integration.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/test_message.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/test_meta_profile_store.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/test_model_features.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/test_model_list.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/llm/test_vision_support.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/marketplace/test_marketplace_registry.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/mcp/test_create_mcp_tool.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/plugin/test_agent_plugins_format.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/plugin/test_installed_plugins.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/plugin/test_plugin_strict_fetch.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/profiles/test_agent_profile.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/profiles/test_profile_refs.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/profiles/test_resolver.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/security/defense_in_depth/test_policy_rails.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/security/defense_in_depth/test_shell_semantics.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/security/test_shell_ast.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/settings/test_acp_providers.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/skills/test_installed_skills.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/skills/test_load_public_skills.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/subagent/test_subagent_registry.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/subagent/test_subagent_scope.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/tool/test_mcp_schema.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/utils/test_files.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/sdk/utils/test_redact.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/child_conversation/test_start_child_conversation.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/file_editor/test_basic_operations.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/file_editor/utils/test_encoding.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/task/mcp_echo_server.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/task/test_mcp_inheritance.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/task/test_sub_agent_scope.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/task_tracker/__init__.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/task_tracker/test_task_tracker_status.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/terminal/test_terminal_parsing.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/terminal/test_terminal_session.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `tests/tools/terminal/test_tmux_pane_pool.py` | Tests, CI, documentation, examples or verification tooling; no direct product API change. |
| `uv.lock` | Dependency / package-version change. Requires full live pass; production image packaging remains a distinct capability. |

## Explicit reconciliation of map owners gaps

All 36 paths reported in `map-owners.json` are assigned below. This replaces the provisional generic classification above where applicable. Shared schemas and provider capabilities are expanded across their consumers. Runtime coverage is a separate evidence requirement.

| Unowned path | Ownership or non-agent-facing reason |
|---|---|
| `clients/typescript/README.md` | F34: consumer documentation for live-client validation; documentation itself adds no runtime route. |
| `clients/typescript/config/public-type-budget.json` | F34: compile-time exported type budget updated for the new ACP discovery types; not a runtime route. |
| `clients/typescript/package-lock.json` | F34 (and F01 compatibility/version gate): package/dependency lock changes; run built client against TARGET. |
| `openhands-agent-server/AGENTS.md` | Repository guidance only; no agent-facing runtime or schema change. |
| `openhands-agent-server/openhands/agent_server/docker/Dockerfile` | F01, F12, F16, F31: production image dependencies, Docker runtime, removal of VS Code from python-slim, packaging/startup. A source launch does not verify production images; image build/attach is separately required or explicitly blocked. |
| `openhands-agent-server/openhands/agent_server/local_storage.py` | F04, F10, F15, F31: new local worktree ownership/deletion helper removes deleted conversation worktrees while retaining git branches and respecting shared fork workspaces (#5547). |
| `openhands-agent-server/pyproject.toml` | F01, F12, F31: server release metadata and packaged dependencies; dependency changes widen the full-pass scope and require separate production-artifact qualification. |
| `openhands-sdk/openhands/sdk/__init__.py` | F23, F33, F34: public ACPModelOption import now emits deprecation via module __getattr__; ACP discovery replaces static model lists (#5579). |
| `openhands-sdk/openhands/sdk/agent/acp_contracts.py` | F09, F12, F23, F33, F34: shared legacy ACP session-model response types used by model discovery and runtime switching (#5579); verify both discovery and conversation consumers. |
| `openhands-sdk/openhands/sdk/agent/acp_models.py` | F09, F12, F23, F33, F34: canonical ACPModelDiscovery/ACPModelDiscoveryError schema adds model metadata and structured startup failures; REST and typed clients serialize these. |
| `openhands-sdk/openhands/sdk/agent/response_dispatch.py` | F06, F07, F08, F32, F33, F34: forwards event callback through sync/async confirmation analysis so SecurityAnalysisEvent persists and reaches socket/webhook/client consumers (#5182). |
| `openhands-sdk/openhands/sdk/conversation/base.py` | F10, F33: fork(title=...) documentation now distinguishes deprecated LocalConversation title from honored RemoteConversation/server title (#4814); no new runtime code in this file. |
| `openhands-sdk/openhands/sdk/conversation/message_request.py` | F04, F05, F06, F07, F27, F33, F34: shared SendMessageRequest moved to SDK for child-conversation launcher; role/content/run default are serialized REST/session/client inputs (#5126). |
| `openhands-sdk/openhands/sdk/llm/message.py` | F04, F06, F07, F09, F20, F22, F23, F30, F33, F34: shared multimodal request serialization now preserves images when string serializer is forced (#5460); run message transport and real-model consumers, not only catalog reads. |
| `openhands-sdk/openhands/sdk/llm/utils/AGENTS.md` | Repository guidance only; no runtime route. Its model-capability conventions inform source review of model_features.py. |
| `openhands-sdk/openhands/sdk/llm/utils/model_features.py` | F04, F09, F20, F22, F23, F30, F33, F34: provider capability changes for DeepSeek vision, Haiku cache fallback and Bedrock stop-word opt-outs; shared-model consumers need expansion. Providers without credentials remain named blocked capabilities. |
| `openhands-sdk/openhands/sdk/llm/utils/openhands_provider.py` | F04, F09, F20, F22, F23, F30, F33, F34: provider routing table adds DigitalOcean Inference while preserving OpenHands routing and explicit base_url; validates through catalog/profile/model consumers. |
| `openhands-sdk/openhands/sdk/settings/__init__.py` | F18, F21, F23, F33, F34: public settings export deprecation for ACPModelOption, shared schema/import contract rather than an added route. |
| `openhands-sdk/openhands/sdk/settings/acp_providers.py` | F09, F18, F21, F23, F33, F34: deprecated static ACP model metadata and clarified independently surfaced provider contract; compare new discovery endpoint and typed-client imports. |
| `openhands-sdk/openhands/sdk/tool/tool.py` | F27, F33: conversation_local ToolDefinition marker permits subagents' state-local tools while retaining capability scoping (#5520). |
| `openhands-sdk/openhands/sdk/utils/files.py` | F14, F18, F20, F21, F22, F31: atomic write temp files gain .tmp suffix and is_temp_file helper; trajectory archive skips in-flight temp files (#5538), persistence consumers retain atomic behavior. |
| `openhands-sdk/pyproject.toml` | F01, F33: SDK release metadata/dependencies; dependency changes additionally justify the full family pass. |
| `openhands-tools/openhands/tools/child_conversation/__init__.py` | F04, F27, F33, F34: public export for new server-owned start_child_conversation tool and pluggable launcher (#5126). |
| `openhands-tools/openhands/tools/child_conversation/definition.py` | F04, F27, F33, F34: action/observation contract for child creation, parent relationship and launcher; direct-tool/REST/client observations need coverage (#5126). |
| `openhands-tools/openhands/tools/child_conversation/impl.py` | F04, F27, F33, F34: child launch execution, selected stored profile, directory/worktree handling and reported child identity (#5126). |
| `openhands-tools/openhands/tools/delegate/impl.py` | F27, F33: delegated subagents inherit exactly parent MCP servers and allow conversation-local tools without expanding workspace/network capabilities (#5747, #5520). |
| `openhands-tools/openhands/tools/file_editor/editor.py` | F14, F27, F33: file_editor observation numbering preserves terminal-newline semantics and avoids fabricated empty lines (#5670); file APIs alone do not prove model-visible tool output. |
| `openhands-tools/openhands/tools/file_editor/utils/encoding.py` | F14, F27, F33: valid UTF-8 is decoded strictly before heuristic encoding detection (#4784); exercise non-ASCII tool round trips. |
| `openhands-tools/openhands/tools/task_tracker/definition.py` | F27, F33: conversation-local tool marker and canonicalization of pending/completed/In Progress status aliases (#5520, #6003). |
| `openhands-tools/openhands/tools/terminal/constants.py` | F13, F27, F33: shorter adaptive terminal poll intervals (#5715), observable command completion latency through actual tool/SDK runs. |
| `openhands-tools/openhands/tools/terminal/terminal/terminal_session.py` | F13, F27, F33: adaptive polling preserves timeouts and finish detection (#5715); exercise real tmux/subprocess tool calls. |
| `openhands-tools/openhands/tools/terminal/terminal/tmux_pane_pool.py` | F13, F27, F33: shared safe tmux socket naming handles long directories (#6038); verify direct and pooled tools. |
| `openhands-tools/openhands/tools/terminal/terminal/tmux_terminal.py` | F13, F27, F31, F33: private tmux socket ownership avoids startup cleanup of live sibling sessions; long-path fallback works for direct and pooled terminals (#5589, #6038). |
| `openhands-tools/openhands/tools/terminal/utils/command.py` | F13, F27, F33: AST command boundaries preserve valid chained/multiple heredoc scripts (#5342); direct terminal recipe required. |
| `openhands-tools/pyproject.toml` | F01, F27, F33: tools package release metadata; no direct new route, verify built-in tools on assembled server. |
| `openhands-workspace/pyproject.toml` | F01, F12, F33: workspace package release metadata; no direct new route, verify RemoteWorkspace and runtime consumers. |
