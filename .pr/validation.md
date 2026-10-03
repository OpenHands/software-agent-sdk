# Validation for #5380

Environment: macOS arm64; native CPython 3.13.13; uv 0.11.19. Base `35410b3af87e672b0264ba711755053c9d517bef`. No Docker CLI and no tmux installed. The SDK used its supported subprocess terminal backend. No real LLM calls or human verification.

## Regression and focused tests

```sh
uv run --frozen pytest -q tests/cross/test_subagent_build_artifacts.py
```

Before changing MANIFEST.in: **FAIL**, extracted definitions `{}` vs four source definitions (1 failed, 2.45s).
After: **PASS**, 1 passed in 3.35s. This builds the actual root sdist, extracts it, then builds the tools wheel from the extracted workspace and checks the definitions' bytes at both boundaries. No mocked packaging or registry.

```sh
uv run --frozen pytest -q tests/tools/test_builtin_agents.py tests/tools/test_preset_default.py tests/tools/task tests/sdk/subagent tests/agent_server/test_sub_agents_router.py tests/agent_server/docker_runtime/test_mediation.py
```

**PASS**, 242 passed, 24 warnings in 7.25s. Warnings include the tmux fallback and deprecated `default` agent alias.

## Packaged-process A/B

Run these at the base revision, then repeat on the fixed revision with distinct output directories and environments. The actual before directories were `.pr/artifacts`, `.pr/extracted`, `.pr/artifact-env`; after directories append `-after`.

```sh
uv sync --frozen --dev --python cpython-3.13-macos-aarch64-none
uv build --sdist --out-dir .pr/artifacts
mkdir -p .pr/extracted
tar -xzf .pr/artifacts/openhands_build_context-0.0.0.tar.gz -C .pr/extracted
UV_PROJECT_ENVIRONMENT="$PWD/.pr/artifact-env" uv sync \
  --project .pr/extracted/openhands_build_context-0.0.0 \
  --frozen --dev --no-editable --python cpython-3.13-macos-aarch64-none
LOG_LEVEL=CRITICAL OPENHANDS_SUPPRESS_BANNER=1 \
  .pr/artifact-env/bin/python .pr/probe_runtime.py
```

Module paths printed by the probe were inside each artifact environment's `site-packages`, not the source checkout. All four workspace packages were installed from the extracted sdist. This reproduces the Dockerfile's non-editable installation boundary on macOS; it does not run Docker or freeze the server.

| Check | Source checkout | Base sdist installation | Fixed sdist installation |
|---|---|---|---|
| Startup registry | All four | Empty | All four |
| Built-in discovery | All four | Empty | All four |
| general-purpose task | completed | error: none registered | completed |
| code-explorer task | completed | error: none registered | completed |
| bash-runner task | completed | error: none registered | completed |
| genuinely-unknown-5380 | unknown-agent error | unknown-agent error | unknown-agent error |
| discover_builtin_agents(False) | three non-browser agents | empty | three non-browser agents |

The tasks use real TaskExecutor, TaskManager, built-in factories and child LocalConversation instances with TestLLM scripted to call `finish`. No registration/loader mocks or manual removal of definitions. This checks delegation, not live model reasoning or browser operation.

The existing server startup passes `enable_browser=True`; this change does not alter it. The browser-disabled check exercises the authoritative discovery function's existing flag, not an assertion that the server catalog filters on OH_ENABLE_BROWSER.

## Real HTTP server startup

```sh
.venv/bin/python .pr/probe_http.py \
  .venv/bin/python \
  .pr/artifact-env/bin/python \
  .pr/artifact-env-after/bin/python
```

**PASS**, exact output:

```text
.venv HTTP 200 ['bash-runner', 'code-explorer', 'general-purpose', 'web-researcher']
artifact-env HTTP 200 []
artifact-env-after HTTP 200 ['bash-runner', 'code-explorer', 'general-purpose', 'web-researcher']
```

Each probe starts `python -m openhands.agent_server --host 127.0.0.1 --port <free-port>` with a fresh working/persistence directory, waits for Uvicorn startup, POSTs `/api/sub-agents` with user/project loading disabled, then terminates its server. `OH_CONVERSATION_RUNTIME=local` matches the setting Docker registry.py supplies **inside** the conversation container. Host Docker orchestration is not exercised. VSCode and tool preloading are disabled for the probe.

## PyInstaller collection

```sh
.pr/artifact-env-after/bin/python - <<'PY'
from PyInstaller.utils.hooks import collect_data_files
files = collect_data_files('openhands.tools.preset', includes=['subagents/*.md'])
print(len(files))
for source, destination in sorted(files):
    print(source, '->', destination)
assert len(files) == 4
PY
```

**PASS**: all four definitions collected from installed site-packages into `openhands/tools/preset/subagents`. This is the unchanged collector expression in agent-server.spec, not a complete PyInstaller build.

## Checks and limitations

- **PASS**: `make build` (dependency setup and hook installation).
- **PASS**: `uv run --frozen pre-commit run --files MANIFEST.in tests/cross/test_subagent_build_artifacts.py .pr/probe_runtime.py .pr/probe_http.py .pr/investigation.md .pr/validation.md` (Ruff formatting/lint, pycodestyle, pyright and repository checks on applicable files).
- **PASS**: `git diff --check`.
- **FAIL, recovered**: initial x86_64 `uv sync --frozen --dev` could not build LiteLLM because the Rust x86_64 target was absent. Native arm64 sync passed without source changes.
- **FAIL, recovered**: initial pre-commit dependency fetch failed connecting to GitHub HTTPS. Retried with a per-command SSH URL rewrite; checks passed. Initial evidence-script Ruff run applied one import-format fix; rerun passed.
- **NOT RUN**: full Linux Docker image build and OH_CONVERSATION_RUNTIME=docker end-to-end smoke; no Docker CLI/daemon available.
- **NOT RUN**: full frozen agent-server binary build/execution.
- **NOT RUN**: live web-researcher/browser/MCP execution, real LLM or human tests.

Before marking ready, a human should verify the freshly built Docker image through the issue's host-runtime flow and fill the PR's HUMAN section. The artifact/process evidence is an explicit substitute for local validation, not a claim of complete Docker verification.
