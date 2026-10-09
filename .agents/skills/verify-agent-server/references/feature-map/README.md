# Agent Server feature map

This directory is the maintained source for verifying the agent-facing
behavior of the OpenHands Agent Server: every REST route and WebSocket (the
default app plus the routes Docker runtime mode adds; `map routes` lists
both), and the behaviors around them (persistence, webhooks, auth,
consumers). Read this
index before driving the server, then use the matching family file as the
recipe.

Maintenance baseline: `8ba966d1d3af49f08e04349c055009e1614b48d6` (first pass, 2026-10-07).

The map has **932 sub-features**.

## Baseline preconditions

- `make build` (or `uv sync --dev`) has run in the repository root.
- `control-agent-server` is on `PATH`:
  `export PATH="$PWD/.agents/skills/verify-agent-server/scripts:$PATH"`.
- A run of this checkout is live and exported:
  `export AGENT_SERVER_VERIFY_RUN=$(control-agent-server launch --new --print-run)`.
- `control-agent-server doctor` reports `ok: true`.
- Model-backed recipes: `$DEEPSEEK_API_KEY` is set and
  `control-agent-server llm preset deepseek` has run (deepseek-flash active).
- Never drive a server that this verification run did not launch or attach.

## Driving conventions

- Start every family from the baseline unless its `Preconditions:` say
  otherwise; run its bullets top to bottom in one shell.
- Capture per-run values into shell variables (`--print-id`, `--field`,
  `--print-path`); never paste ids from an earlier run.
- Treat every command as literal. Keep quoted JSON and flags unchanged.
- Prefix fixtures and names with `qa-` or `QA_`; restore shared state (active
  profile, settings) after a mutation unless a later bullet needs it.
- Assert status codes, stored values, files, event kinds and tool
  observations, never the model's wording.

## Proof and skip reporting

- A mutation is proven by a read-only second view: a GET, a WebSocket frame,
  a file on disk, or a webhook delivery.
- Save the exchange that proves each sub-feature with `--save <ID>/<name>` and
  cite it in `evidence add`.
- Report an unreachable path with the attempted command and the unmet
  precondition (`blocked`), never as a pass through a different entry point.

## Family entry contract

Each family file starts with an H1 title, one paragraph describing the
agent-visible behavior, a `Source:` line and a `Routes:` line (see
[mapping.md](../mapping.md#3-write-each-entry)). It then uses exactly four H2
sections in this order: `Sub-features`, `How to get to it (agent POV)`,
`Driving it with control-agent-server` (starting with `Preconditions:`), and
`Gotchas`.

How to write and prove new entries: [../mapping.md](../mapping.md). How to keep
them honest: [../maintenance.md](../maintenance.md).

## Families

| ID | Family | What it covers | Entry points | Needs | Sub-features |
|---|---|---|---|---|---|
| F01 | [Server status, info and contract](F01-server-status.md) | liveness, health, readiness, server info and its `/` alias, uptime and idle time, OpenAPI document, the static-files mode, DEBUG tracebacks, tool preloading | `GET /alive`, `/health`, `/ready`, `/server_info`, `/`, `/openapi.json` | baseline | 11 |
| F02 | [Authentication and workspace sessions](F02-auth-and-sessions.md) | session keys on HTTP and on WebSockets (first frame, deprecated query and header forms, close codes), several and rotated keys, no-auth and legacy modes, the default bind address, the workspace session cookie, CORS | `X-Session-API-Key`, `POST`/`DELETE /api/auth/workspace-session`, WebSocket auth on every socket | tmux | 21 |
| F03 | [Deferred init (warm-pool dormant mode)](F03-deferred-init.md) | warm-pool dormant mode: the 503 gate, init key auth, validation and redaction, rollback, one-shot init, and every field the init body delivers (keys, secret key, paths, worktree root, run limit, CORS, webhooks, env, web URL) | `GET`/`POST /api/init`, `X-Init-API-Key` | git; launch `--no-auth --deferred-init` | 20 |
| F04 | [Conversation lifecycle and catalog](F04-conversation-lifecycle.md) | create from settings, an agent profile or an inline agent; client ids, parents, worktrees, start-time secrets, 422 redaction; get, batch get, search, count; PATCH; final response; DELETE; restart | `POST`/`GET`/`PATCH`/`DELETE /api/conversations[/{id}]`, `/search`, `/count`, `/agent_final_response` | llm, git | 32 |
| F05 | [Run, pause, interrupt and status transitions](F05-run-pause-interrupt.md) | run, pause and interrupt during model and tool calls; status transitions on every view; stuck detection and max_iterations stops; 409 and 429; graceful and SIGKILL restarts | `POST /api/conversations/{id}/run`, `/pause`, `/interrupt` | llm, tmux; launch `--config-json '{"max_concurrent_runs": 1}'` | 31 |
| F06 | [Conversation events and the events WebSocket](F06-conversation-events.md) | appending messages, event search, count, get and batch get with filters and paging, the events WebSocket (auth, snapshot, replay, live frames), history across restart | `POST`/`GET /api/conversations/{id}/events[...]`, `WS /sockets/events/{id}` | llm; launch `--env TZ=UTC` | 28 |
| F07 | [Session WebSocket protocol](F07-session-socket.md) | the resumable session socket: sync, durable and transient frames, seq replay, inbound messages, error and close codes, streaming deltas, backpressure, subscriber cap | `WS /sockets/session/{id}` | llm | 24 |
| F08 | [Confirmation policy, security analyzer and conversation secrets](F08-confirmation-security-secrets.md) | confirmation policies and accept/reject, the security analyzer, runtime conversation secrets (static, lookup, masking, encryption, no cipher), and what survives a restart | `POST /api/conversations/{id}/confirmation_policy`, `/security_analyzer`, `/secrets`, `/events/respond_to_confirmation` | llm, tmux | 23 |
| F09 | [Model, profile and ACP switching; runtime plugins](F09-model-switching-plugins.md) | swap the agent LLM, switch to a saved profile, switch an ACP model and discover the models an ACP server offers, load a plugin at runtime; stats, hooks and restart persistence | `POST /api/conversations/{id}/switch_llm`, `/switch_profile`, `/switch_acp_model`, `/load_plugin`, `POST /api/acp/models` | llm, tmux, node, network | 30 |
| F10 | [Fork, navigate, condense and ask_agent](F10-fork-navigate-condense-ask.md) | fork, navigate between branches, condense with a real summarizer, ask the agent a side question; state after restart | `POST /api/conversations/{id}/fork`, `/navigate`, `/condense`, `/ask_agent` | llm | 25 |
| F11 | [Goal loop](F11-goals.md) | goal loops: start, LLM-judged completion and caps, stop, resume, supersede, interrupt, failures, busy and capacity limits | `POST /api/conversations/{id}/goal`, `/goal/stop`, `/goal/resume` | llm, tmux; launch `--config-json '{"max_concurrent_runs": 1}'` | 25 |
| F12 | [Conversation runtime info, sandbox pause and credential bindings](F12-runtime-and-credentials.md) | runtime info and reprovision, prepare-for-sandbox-pause, credential bindings for ACP agents, managed LLM key refresh, the Docker runtime mode's routes and contract | `GET /api/conversations/{id}/runtime`, `POST .../runtime/reprovision`, `POST /api/conversations/prepare-for-sandbox-pause`, `PUT .../credential-bindings/{name}`, Docker mode `DELETE .../runtime`, `POST .../runtime/credentials` | llm, node, network | 30 |
| F13 | [Bash commands and bash events](F13-bash.md) | global and conversation-scoped shell commands: execute, start, stop, timeouts, large output, events search and clear, retention, the bash events socket | `/api/bash/*`, `/api/conversations/{id}/bash/*`, `WS /sockets/bash-events` | baseline | 29 |
| F14 | [File upload, download, archive and directory helpers](F14-files.md) | upload, download, create directory, home and subdirectory search, git-delta and tar.gz archives, trajectory zips, conversation-scoped variants and their path guard | `/api/file/*`, `/api/conversations/{id}/file/*` | llm, git, node | 30 |
| F15 | [Git changes, diffs, commits and repository search](F15-git.md) | working-tree changes and diffs, explicit refs, nested repositories, commit history and per-commit changes, scoped variants, GitHub repository search | `/api/git/*`, `/api/conversations/{id}/git/*`, `GET /api/git/repositories/search` | llm, git, node | 28 |
| F16 | [Workspace file serving and VS Code endpoints](F16-workspace-serving-and-vscode.md) | the per-conversation static workspace server (index, content types, ranges, traversal, cookie auth) and the VS Code status and URL routes | `GET /api/conversations/{id}/workspace[/{path}]`, `/api/vscode/*`, `/api/conversations/{id}/vscode/*` | llm, git | 26 |
| F17 | [Workspace registry and parents](F17-workspaces-registry.md) | saved workspaces and parents: add, list, delete, validation, persistence, concurrent writers, corrupted files | `GET`/`POST`/`DELETE /api/workspaces`, `/api/workspaces/parents` | git, node | 24 |
| F18 | [Server settings, settings schemas and the secrets store](F18-settings-and-secrets.md) | persisted settings with redacted, plaintext and encrypted exposure, sparse PATCH, settings schemas, the named secrets store, encryption at rest, cipher-key changes | `GET`/`PATCH /api/settings`, `/api/settings/agent-schema`, `/conversation-schema`, `/api/settings/secrets[/{name}]` | git, node | 29 |
| F19 | [MCP server settings, connection test and OAuth](F19-mcp.md) | MCP server settings CRUD, connection tests (stdio and HTTP), install-time OAuth jobs and token refresh, a model-driven MCP tool call | `/api/settings/mcp/{key}`, `POST /api/mcp/test`, `/api/mcp/oauth/*` | llm; launch `--webhook-sink` | 28 |
| F20 | [LLM profiles](F20-llm-profiles.md) | LLM profiles: save, read, validate against real and stubbed providers, activate, rename, delete, name rules, limits, store contention, restart | `/api/profiles[/{name}]`, `/validate`, `/activate`, `/rename` | llm, node | 35 |
| F21 | [Agent profiles](F21-agent-profiles.md) | agent profiles (OpenHands and ACP kinds): CRUD, default seed, activation, rename, delete, materialize, conversations started from a profile, store contention | `/api/agent-profiles[/{name}]`, `/rename`, `/{id}/activate`, `/materialize` | llm | 32 |
| F22 | [Meta-profiles (model router)](F22-meta-profiles.md) | meta-profiles (model router): save, read, activate, delete, settings reflection, routing in a conversation | `/api/meta-profiles[/{name}]`, `/activate` | llm, node | 24 |
| F23 | [LLM catalog and provider connections](F23-llm-catalog-and-connections.md) | provider and model catalogs, verified models, provider connections (create, rotate, delete, references from profiles) | `GET /api/llm/providers`, `/api/llm/models[/verified]`, `/api/llm/provider-connections[/{id}]` | llm, node, network | 26 |
| F24 | [OpenAI ChatGPT subscription device flow](F24-openai-subscription.md) | the ChatGPT subscription device flow: models, status, device start and poll, logout, upstream failures | `/api/llm/subscription/openai/*` | node, network | 20 |
| F25 | [Skills catalog, install and marketplace](F25-skills.md) | the merged skill catalog, public-repo sync, installed skills (install, enable, refresh, uninstall), marketplace, trigger-word activation in a conversation | `POST /api/skills`, `/api/skills/sync`, `/api/skills/install`, `/api/skills/installed[/{name}]`, `/api/skills/marketplace` | llm, git, network, node | 33 |
| F26 | [Plugins catalog, install and marketplace](F26-plugins.md) | plugin catalog, install from a path or git source, enable, refresh, uninstall, marketplace, loading a plugin into a conversation | `POST /api/plugins`, `/api/plugins/install`, `/api/plugins/installed[/{name}]`, `/api/plugins/marketplace` | llm, git, node, network | 25 |
| F27 | [Hooks, sub-agents and tool catalogs](F27-hooks-subagents-tools.md) | hooks and sub-agent catalogs, blocking hooks in a conversation, the tool registry and catalog, client tools, forwarded agent definitions, custom tool modules | `POST /api/hooks`, `POST /api/sub-agents`, `GET /api/tools/`, `/api/tools/catalog` | llm, git, tmux, chromium | 31 |
| F28 | [Canvas apps: install, catalog and backend lifecycle](F28-canvas-extensions.md) | Canvas app install, catalog, enable, uninstall, bundle and icon; backend approval, prepare, start, stop, logs, data, failure states, restart | `/api/canvas-extensions/install`, `/api/canvas-extensions/installed[/{name}][/backend...]` | git, node; launch `--env TZ=UTC` | 29 |
| F29 | [Canvas App backend bridge (session, HTTP and WebSocket proxy)](F29-app-backend-bridge.md) | the app-backend bridge: session cookies, origin and host checks, proxied HTTP methods and WebSockets, revocation | `/app-backends/{name}/session`, `/app-backends/{name}[/{path}]`, `WS /app-backends/{name}[/{path}]` | git; launch `--canvas-ingress` | 29 |
| F30 | [OpenAI-compatible gateway (/v1)](F30-openai-gateway.md) | the OpenAI-compatible gateway: models, chat completions (plain and streaming), responses, conversation reuse, usage, the official OpenAI SDK | `GET /v1/models`, `POST /v1/chat/completions`, `/v1/responses` | llm, tmux | 26 |
| F31 | [Persistence, restart recovery, leases and limits](F31-persistence-and-limits.md) | state that survives graceful and SIGKILL restarts, crash recovery, conversation ownership leases and takeover between servers, idle eviction, run capacity, config file vs environment precedence | behavior only: `restart`, `restart --hard`, a second server on shared storage | llm, tmux | 23 |
| F32 | [Event webhooks and product telemetry](F32-webhooks-and-telemetry.md) | event webhooks (event batches, conversation updates, headers, buffering, flush, limits, retries) and product telemetry (off by default, exporter lifecycle, sanitized payloads, consent) | behavior only: `webhooks` / `OH_WEBHOOKS_*`, `OH_TELEMETRY_*` | llm; launch `--webhook-sink` | 30 |
| F33 | [Python SDK remote client (RemoteConversation, RemoteWorkspace)](F33-sdk-remote-client.md) | the Python SDK in this checkout against the server: RemoteWorkspace commands, files, git and settings readers; RemoteConversation create, run, callbacks, streaming, reattach, fork, confirmation, pause, interrupt, stop hooks, client tools, completion logs | behavior only: `Workspace(host=...)`, `RemoteWorkspace`, `RemoteConversation` | llm, tmux, git | 43 |
| F34 | [TypeScript client (@openhands/typescript-client)](F34-typescript-client.md) | the TypeScript client against this checkout: server and version gate, workspace, bash, conversation, state, events and socket clients, hooks and MCP clients, endpoint audit and drift | behavior only: `@openhands/typescript-client` (`clients/typescript`) | llm, node, uvx, git, tmux, network | 32 |

## Not mapped

Routes deliberately left without an owner, with the reason:

- `GET /docs`, `GET /redoc`, `GET /docs/oauth2-redirect`: FastAPI's generated
  documentation pages, not part of the agent-facing contract (`/openapi.json`
  is mapped in F01).
- `POST /api/conversations/{conversation_id}`,
  `PUT /api/conversations/{conversation_id}`,
  `GET /api/conversations/{conversation_id}/{tail:path}`,
  `POST /api/conversations/{conversation_id}/{tail:path}`,
  `PUT /api/conversations/{conversation_id}/{tail:path}`,
  `PATCH /api/conversations/{conversation_id}/{tail:path}`,
  `DELETE /api/conversations/{conversation_id}/{tail:path}`: exist only in
  Docker runtime mode (`conversation_runtime: docker`), where they forward
  conversation-scoped calls into the conversation's container. The routes
  they forward are mapped in their own families; proving the forwarding
  needs a Docker daemon (`F12.docker-runtime`, blocked here). F12 owns the
  Docker mode's own runtime-control routes.
