# Investigation #5380

Base: 35410b3af87e672b0264ba711755053c9d517bef (upstream/main), checked 2026-10-03 Asia/Shanghai.

## Initial hypotheses and evidence

1. Missing packaged definitions. CONFIRMED at the Docker build-context sdist boundary. Root `uv build --sdist --out-dir .pr/artifacts` succeeds but contains zero `preset/subagents/*.md`. A direct tools wheel contains all four. Root MANIFEST.in includes tools Python/Jinja/py.typed but no subagent Markdown. `_make_build_context` builds and extracts that root sdist; server.yml feeds it to docker/build-push-action. Dockerfile installs the extracted packages non-editably before PyInstaller collects data. Root .dockerignore is absent from this sdist, so its Markdown exclusion is not the cause of this supported CI path.
2. Startup registration missing in the conversation process. Disfavored as the primary cause: the same real tool_router import registers all four from source and none from an installed build-context artifact. Registration executes but has no definitions. api.py imports tool_router. Docker containers run agent-server with OH_CONVERSATION_RUNTIME=local; host process registry state is not required to explain the failure.
3. Host/container forwarding or process-local state. Disfavored as primary cause: independently installed artifact reproduces the failure without a host/serialization boundary. Both source and artifact execute the same TaskExecutor/TaskManager lookup path. No host registry copying is needed to fix missing assets.
4. Recent profile refactor fixes the issue. Excluded for this packaging failure on current main: #5151, #5358, #5406 are already merged at the tested SHA; artifact still loses data and reproduces the task error. #5406 moved serialization out of mediation.py, which now only materializes secrets.

## Runtime evidence

macOS arm64, CPython 3.13.13. Docker CLI unavailable. First x86_64 uv sync failed building litellm (missing Rust x86_64 target); explicit native arm64 sync passed.

- `.venv/bin/python .pr/probe_runtime.py`: real startup registration, discovery and real child LocalConversation/task execution with scripted TestLLM; general-purpose/code-explorer/bash-runner completed. Unknown fails. Browser-disabled discovery excludes web-researcher.
- `.pr/artifact-env/bin/python .pr/probe_runtime.py`: all packages installed non-editably from the unmodified root sdist with `uv sync --frozen --dev --no-editable`; module paths verified inside artifact-env/site-packages. Registry and discovery empty; three built-ins fail with exact `none registered` TaskObservation. Unknown also fails.
- These are packaging/process-level runtime reproductions, NOT a full OH_CONVERSATION_RUNTIME=docker or frozen Linux binary reproduction. Model is scripted; no human testing or real LLM calls claimed. Terminal falls back to supported subprocess backend because tmux is absent.

## Competition

Issue Open, ready-for-dev + priority:medium, no assignee/comments; no closing linked PR in GraphQL. Timeline references #5151 (merged, report origin), #5394/#5395 (other issues), no implementation claim.

Searched issue number/title and docker runtime sub-agent, built-in sub-agents, none registered, register_builtins_agents, general-purpose docker; broadened to subagent, docker, agent definitions, sdist, MANIFEST.in, dockerignore, subagents/*.md, build context agents, Markdown packaging.

Reviewed #5011 (open, per-conversation custom registry isolation; built-ins remain process-global; distinct), #4445 (open, browser JS wheel package-data; does not fix root sdist or subagent data; distinct), #3290 (merged PyInstaller collector, does not include root sdist data), #5358 (merged capability scoping), #5151 and #5406 (merged profile launch changes). No direct/high-overlap packaging fix found.

## Root cause / scope / decision

Root sdist MANIFEST.in omits built-in Markdown, so the authoritative registration path sees no definitions in Docker-derived installed artifacts. Confidence HIGH for this demonstrated failure; full Docker/frozen runtime remains unverified.

Scope: packaging + real artifact build regression test. Preserve startup, registry precedence, profiles, API and mediation. Minimal fix includes subagent Markdown in root sdist. Do not add a second registration path or forward host registry.

Decision: GO. Current-main failure confirmed through actual packaged process (allowed substitute), no competing implementation found, narrow fix and deterministic real-build regression possible.
